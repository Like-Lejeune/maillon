"""Canal temps réel /agent/ws, testé avec un vrai client WebSocket.

Délais raccourcis par l'environnement de test : battement 1 s, coupure après 3 s,
balayage toutes les secondes.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import text, update
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from app.config import get_settings
from app.models import Machine

HARDWARE = {
    "os": "Ubuntu 24.04",
    "gpu_model": "NVIDIA GeForce RTX 4070",
    "vram_mb": 12282,
    "cpu_cores": 12,
    "ram_mb": 32000,
    "disk_free_gb": 400,
}


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def enrolled_machine(client: AsyncClient) -> tuple[str, str, str]:
    """Crée un fournisseur et une machine inscrite. Renvoie (session, machine_id, jeton d'agent)."""
    session = (
        await client.post(
            "/api/auth/register",
            json={"email": "p@exemple.com", "password": "motdepasse-solide-1", "name": "P", "role": "provider"},
        )
    ).json()["token"]
    declared = (await client.post("/api/machines", json={"name": "Poste"}, headers=bearer(session))).json()
    enrolled = (
        await client.post(
            "/agent/enroll",
            json={"enrollment_token": declared["enrollment_token"], "agent_version": "0.2.0", "hardware": HARDWARE},
        )
    ).json()
    return session, declared["machine"]["id"], enrolled["agent_token"]


def agent_connect(live_server: str, token: str | None):
    headers = bearer(token) if token else {}
    return connect(f"ws://{live_server}/agent/ws", additional_headers=headers, open_timeout=5)


async def hello(ws) -> dict:
    await ws.send(json.dumps({"type": "hello", "data": {"agent_version": "0.2.0", "hardware": HARDWARE}}))
    return json.loads(await asyncio.wait_for(ws.recv(), 5))


async def status_of(client, session) -> str:
    return (await client.get("/api/machines", headers=bearer(session))).json()[0]["status"]


async def wait_for_status(client, session, expected: str, within: float) -> float:
    start = asyncio.get_running_loop().time()
    while True:
        if await status_of(client, session) == expected:
            return asyncio.get_running_loop().time() - start
        if asyncio.get_running_loop().time() - start > within:
            pytest.fail(f"statut {expected!r} non atteint en {within} s")
        await asyncio.sleep(0.1)


async def closed_code(ws) -> int:
    with pytest.raises(ConnectionClosed) as caught:
        while True:
            await asyncio.wait_for(ws.recv(), 10)
    return caught.value.rcvd.code if caught.value.rcvd else None


# --------------------------------------------------------------------- tests
async def test_hello_puts_machine_online_and_returns_welcome(client, live_server):
    session, machine_id, token = await enrolled_machine(client)
    assert await status_of(client, session) == "offline"
    async with agent_connect(live_server, token) as ws:
        welcome = await hello(ws)
        assert welcome["type"] == "welcome"
        assert welcome["data"]["machine_id"] == machine_id
        assert welcome["data"]["tier"] == "B"
        assert welcome["data"]["heartbeat_interval_seconds"] == get_settings().heartbeat_interval_seconds
        assert await status_of(client, session) == "online"


async def test_closing_the_connection_puts_machine_offline_at_once(client, live_server):
    session, _, token = await enrolled_machine(client)
    async with agent_connect(live_server, token) as ws:
        await hello(ws)
    assert await wait_for_status(client, session, "offline", within=2) < 2


async def test_silent_agent_is_cut_and_offline_within_the_timeout(client, live_server):
    """Critère du planning : une machine coupée passe hors ligne en moins de 30 s
    (ici 3 s, délai raccourci pour les tests)."""
    session, _, token = await enrolled_machine(client)
    async with agent_connect(live_server, token) as ws:
        await hello(ws)
        assert await closed_code(ws) == 4008
    elapsed = await wait_for_status(client, session, "offline", within=1)
    assert elapsed < 1


async def test_heartbeats_keep_the_machine_online(client, live_server):
    timeout = get_settings().heartbeat_timeout_seconds
    session, _, token = await enrolled_machine(client)
    async with agent_connect(live_server, token) as ws:
        await hello(ws)
        for _ in range(timeout + 2):
            await ws.send(json.dumps({"type": "heartbeat", "data": {"busy": False, "gpu_util": 3.5}}))
            await asyncio.sleep(1)
        assert await status_of(client, session) == "online"


async def test_invalid_or_missing_token_is_refused_with_4001(client, live_server):
    for token in (None, "ma_invente", "mk_live_cle"):
        async with agent_connect(live_server, token) as ws:
            assert await closed_code(ws) == 4001


async def test_second_connection_replaces_the_first_without_going_offline(client, live_server):
    session, _, token = await enrolled_machine(client)
    async with agent_connect(live_server, token) as first:
        await hello(first)
        async with agent_connect(live_server, token) as second:
            await hello(second)
            assert await closed_code(first) == 4000
            await asyncio.sleep(0.3)
            assert await status_of(client, session) == "online"


async def test_removing_the_machine_closes_its_channel(client, live_server):
    session, machine_id, token = await enrolled_machine(client)
    async with agent_connect(live_server, token) as ws:
        await hello(ws)
        await client.delete(f"/api/machines/{machine_id}", headers=bearer(session))
        assert await closed_code(ws) == 4001
    async with agent_connect(live_server, token) as ws:
        assert await closed_code(ws) == 4001


async def test_suspended_machine_is_told_and_refused(client, live_server, running_app):
    session, _, token = await enrolled_machine(client)
    async with running_app.state.engine.begin() as connection:
        await connection.execute(text("UPDATE machines SET status = 'suspended', suspended_reason = 'Résultats faux'"))
    async with agent_connect(live_server, token) as ws:
        message = json.loads(await asyncio.wait_for(ws.recv(), 5))
        assert message["type"] == "suspended" and message["data"]["reason"] == "Résultats faux"
        assert await closed_code(ws) == 4003


async def test_invalid_messages_get_errors_then_the_channel_closes(client, live_server):
    _, _, token = await enrolled_machine(client)
    async with agent_connect(live_server, token) as ws:
        await ws.send(json.dumps({"type": "heartbeat", "data": {}}))
        assert json.loads(await ws.recv())["type"] == "error"  # heartbeat avant hello
        await ws.send("pas du json")
        assert json.loads(await ws.recv())["type"] == "error"
        await ws.send(json.dumps({"type": "hello", "data": {"agent_version": "x", "hardware": {}}}))
        assert json.loads(await ws.recv())["type"] == "error"
        await ws.send(json.dumps({"type": "inconnu"}))
        await ws.recv()
        await ws.send(json.dumps([1, 2]))
        await ws.recv()
        assert await closed_code(ws) == 4002


async def test_sweeper_puts_stale_online_machines_offline(client, running_app):
    """Cas d'un serveur redémarré : la base dit « online » mais plus aucune connexion n'existe."""
    session, _, _ = await enrolled_machine(client)
    async with running_app.state.session_factory() as db:
        await db.execute(
            update(Machine).values(
                status="online", last_heartbeat_at=datetime.now(timezone.utc) - timedelta(minutes=5)
            )
        )
        await db.commit()
    await wait_for_status(client, session, "offline", within=get_settings().presence_sweep_seconds + 2)


async def test_hello_updates_hardware_and_tier(client, live_server):
    session, _, token = await enrolled_machine(client)
    upgraded = {**HARDWARE, "gpu_model": "NVIDIA GeForce RTX 4090", "vram_mb": 24564}
    async with agent_connect(live_server, token) as ws:
        await ws.send(json.dumps({"type": "hello", "data": {"agent_version": "0.2.1", "hardware": upgraded}}))
        welcome = json.loads(await ws.recv())
        assert welcome["data"]["tier"] == "A"
        machine = (await client.get("/api/machines", headers=bearer(session))).json()[0]
        assert machine["tier"] == "A" and machine["agent_version"] == "0.2.1"
