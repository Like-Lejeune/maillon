"""Ordonnanceur et /v1/chat/completions, avec de faux agents branchés en WebSocket.

Délais raccourcis par l'environnement de test : attente d'une machine 1 s,
accusé de réception 1 s, exécution 3 s.
"""

import asyncio
import json
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import text
from websockets.asyncio.client import connect

SHA = "a" * 64
HARDWARE = {
    "os": "Ubuntu 24.04", "gpu_model": "RTX 4070", "vram_mb": 12282,
    "cpu_cores": 12, "ram_mb": 32000, "disk_free_gb": 400,
}


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def add_model(running_app, slug="modele-test", min_vram=7000):
    async with running_app.state.engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO models (slug, display_name, min_vram_mb, context_length, file_url, file_sha256, "
                "file_size_bytes, price_input_per_mtok, price_output_per_mtok) "
                "VALUES (:s, 'Test', :v, 8192, 'https://exemple.com/m.gguf', :sha, 1000, 100, 200)"
            ),
            {"s": slug, "v": min_vram, "sha": SHA},
        )


async def client_key(client) -> str:
    session = (
        await client.post(
            "/api/auth/register",
            json={"email": "c@exemple.com", "password": "motdepasse-solide-1", "name": "C", "role": "client"},
        )
    ).json()["token"]
    return (await client.post("/api/api-keys", json={"name": "k"}, headers=bearer(session))).json()["key"]


_providers = 0


async def agent_token(client, hardware=HARDWARE) -> str:
    global _providers
    _providers += 1
    session = (
        await client.post(
            "/api/auth/register",
            json={"email": f"p{_providers}@exemple.com", "password": "motdepasse-solide-1", "name": "P", "role": "provider"},
        )
    ).json()["token"]
    declared = (await client.post("/api/machines", json={"name": "M"}, headers=bearer(session))).json()
    return (
        await client.post(
            "/agent/enroll",
            json={"enrollment_token": declared["enrollment_token"], "agent_version": "0.3.0", "hardware": hardware},
        )
    ).json()["agent_token"]


class FakeAgent:
    """Agent simulé : répond aux job.assign selon le comportement choisi."""

    def __init__(self, ws, behaviour):
        self.ws = ws
        self.behaviour = behaviour
        self.received: list[dict] = []
        self.assigned: list[dict] = []
        self.busy = 0
        self.max_busy = 0

    async def send(self, kind, data):
        await self.ws.send(json.dumps({"type": kind, "data": data}))

    async def loop(self):
        try:
            async for raw in self.ws:
                message = json.loads(raw)
                self.received.append(message)
                if message["type"] == "job.assign":
                    self.assigned.append(message["data"])
                    asyncio.create_task(self._handle(message["data"]))
        except Exception:
            pass

    async def _handle(self, job):
        self.busy += 1
        self.max_busy = max(self.max_busy, self.busy)
        try:
            await self.behaviour(self, job)
        finally:
            self.busy -= 1


async def answer(agent, job, delay=0.05):
    await agent.send("job.accepted", {"job_id": job["job_id"]})
    await asyncio.sleep(delay)
    last = job["request"]["messages"][-1]["content"]
    await agent.send(
        "job.result",
        {"job_id": job["job_id"], "content": f"Écho : {last}", "finish_reason": "stop",
         "prompt_tokens": 12, "completion_tokens": 5},
    )


@asynccontextmanager
async def fake_agent(live_server, token, behaviour=answer, cached=("modele-test",), hardware=HARDWARE):
    async with connect(f"ws://{live_server}/agent/ws", additional_headers=bearer(token)) as ws:
        await ws.send(json.dumps({"type": "hello", "data": {"agent_version": "0.3.0", "hardware": hardware, "cached_models": list(cached)}}))
        assert json.loads(await ws.recv())["type"] == "welcome"
        agent = FakeAgent(ws, behaviour)
        task = asyncio.create_task(agent.loop())
        try:
            yield agent
        finally:
            task.cancel()


async def complete(client, key, content="Bonjour", **extra):
    return await client.post(
        "/v1/chat/completions",
        json={"model": "modele-test", "messages": [{"role": "user", "content": content}], **extra},
        headers=bearer(key),
    )


async def job_row(running_app):
    async with running_app.state.engine.connect() as c:
        return (await c.execute(text("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 1"))).mappings().one()


async def machine_row(running_app):
    async with running_app.state.engine.connect() as c:
        return (await c.execute(text("SELECT * FROM machines LIMIT 1"))).mappings().one()


# ===================================================================== tests
async def test_request_is_served_by_a_remote_machine(client, live_server, running_app):
    """Jalon du 21 novembre : une requête traitée par une machine distante."""
    await add_model(running_app)
    key = await client_key(client)
    async with fake_agent(live_server, await agent_token(client)) as agent:
        response = await complete(client, key, "Bonjour Maillon", max_tokens=50, user="ignoré")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["object"] == "chat.completion" and body["id"].startswith("chatcmpl-")
        assert body["model"] == "modele-test"
        assert body["choices"][0]["message"] == {"role": "assistant", "content": "Écho : Bonjour Maillon"}
        assert body["choices"][0]["finish_reason"] == "stop"
        assert body["usage"] == {"prompt_tokens": 12, "completion_tokens": 5, "total_tokens": 17}

        sent = agent.assigned[0]
        assert sent["model"] == "modele-test" and sent["timeout_seconds"] == 3
        assert sent["request"]["stream"] is False and sent["request"]["max_tokens"] == 50
        assert "user" not in sent["request"]

    job = await job_row(running_app)
    assert job["status"] == "succeeded" and job["attempts"] == 1
    assert job["compute_ms"] is not None and job["compute_ms"] >= 40
    machine = await machine_row(running_app)
    assert machine["current_job_id"] is None and machine["jobs_succeeded"] == 1


async def test_unknown_model_and_streaming_are_refused(client, live_server, running_app):
    await add_model(running_app)
    key = await client_key(client)
    unknown = await client.post(
        "/v1/chat/completions", json={"model": "inconnu", "messages": [{"role": "user", "content": "x"}]}, headers=bearer(key)
    )
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "model_not_found"
    streamed = await complete(client, key, stream=True)
    assert streamed.status_code == 400 and streamed.json()["error"]["code"] == "stream_not_supported"


async def test_no_machine_gives_503_no_capacity(client, running_app):
    await add_model(running_app)
    key = await client_key(client)
    response = await complete(client, key)
    assert response.status_code == 503
    assert response.json()["error"]["type"] == "no_capacity"
    assert (await job_row(running_app))["status"] == "failed"


async def test_machine_without_the_model_is_asked_to_prepare_it(client, live_server, running_app):
    await add_model(running_app)
    key = await client_key(client)
    async with fake_agent(live_server, await agent_token(client), cached=()) as agent:
        await asyncio.sleep(0.2)
        assert {"type": "model.prepare", "model": "modele-test"} in [
            {"type": m["type"], "model": m["data"].get("model")} for m in agent.received
        ]
        assert (await complete(client, key)).status_code == 503  # pas encore en cache

        await agent.send("model.cached", {"model": "modele-test", "sha256": SHA})
        await asyncio.sleep(0.2)
        assert (await complete(client, key)).status_code == 200


async def test_model_cached_with_a_wrong_digest_is_refused(client, live_server, running_app):
    await add_model(running_app)
    key = await client_key(client)
    async with fake_agent(live_server, await agent_token(client), cached=()) as agent:
        await agent.send("model.cached", {"model": "modele-test", "sha256": "b" * 64})
        await asyncio.sleep(0.2)
        assert any(m["type"] == "error" for m in agent.received)
        assert (await complete(client, key)).status_code == 503


async def test_machine_with_too_little_vram_is_never_used(client, live_server, running_app):
    await add_model(running_app, min_vram=20000)
    key = await client_key(client)
    async with fake_agent(live_server, await agent_token(client)) as agent:
        await asyncio.sleep(0.1)
        assert not any(m["type"] == "model.prepare" for m in agent.received)
        assert (await complete(client, key)).status_code == 503
        assert agent.assigned == []


async def test_agent_error_gives_502_and_frees_the_machine(client, live_server, running_app):
    async def fails(agent, job):
        await agent.send("job.accepted", {"job_id": job["job_id"]})
        await agent.send("job.error", {"job_id": job["job_id"], "code": "out_of_memory", "message": "VRAM", "retryable": True})

    await add_model(running_app)
    key = await client_key(client)
    async with fake_agent(live_server, await agent_token(client), fails):
        response = await complete(client, key)
        assert response.status_code == 502 and response.json()["error"]["code"] == "out_of_memory"
    job = await job_row(running_app)
    assert job["status"] == "failed" and job["error_code"] == "out_of_memory"
    machine = await machine_row(running_app)
    assert machine["current_job_id"] is None and machine["jobs_failed"] == 1
    assert float(machine["reliability_score"]) == 0.0


async def test_agent_that_never_accepts_gives_502(client, live_server, running_app):
    async def silent(agent, job):
        await asyncio.sleep(10)

    await add_model(running_app)
    key = await client_key(client)
    async with fake_agent(live_server, await agent_token(client), silent):
        response = await complete(client, key)
        assert response.status_code == 502 and response.json()["error"]["code"] == "agent_unresponsive"
    assert (await machine_row(running_app))["current_job_id"] is None


async def test_too_slow_job_expires_with_504_and_cancel_is_sent(client, live_server, running_app):
    async def slow(agent, job):
        # Comme le vrai agent, il continue d'envoyer ses battements pendant la tâche.
        await agent.send("job.accepted", {"job_id": job["job_id"]})
        for _ in range(20):
            await agent.send("heartbeat", {"busy": True, "job_id": job["job_id"]})
            await asyncio.sleep(0.5)

    await add_model(running_app)
    key = await client_key(client)
    async with fake_agent(live_server, await agent_token(client), slow) as agent:
        response = await complete(client, key)
        assert response.status_code == 504
        await asyncio.sleep(0.1)
        assert any(m["type"] == "job.cancel" for m in agent.received)
    assert (await job_row(running_app))["status"] == "expired"


async def test_machine_lost_during_the_job_gives_503(client, live_server, running_app):
    async def drops(agent, job):
        await agent.send("job.accepted", {"job_id": job["job_id"]})
        await agent.ws.close()

    await add_model(running_app)
    key = await client_key(client)
    async with fake_agent(live_server, await agent_token(client), drops):
        response = await complete(client, key)
    assert response.status_code == 503 and response.json()["error"]["code"] == "machine_lost"
    assert (await job_row(running_app))["status"] == "failed"
    machine = await machine_row(running_app)
    assert machine["current_job_id"] is None and machine["status"] == "offline"


async def test_one_machine_never_runs_two_jobs_at_once(client, live_server, running_app):
    await add_model(running_app)
    key = await client_key(client)

    async def steady(agent, job):
        await answer(agent, job, delay=0.3)

    async with fake_agent(live_server, await agent_token(client), steady) as agent:
        first, second = await asyncio.gather(complete(client, key, "un"), complete(client, key, "deux"))
        assert first.status_code == second.status_code == 200
        assert agent.max_busy == 1 and len(agent.assigned) == 2


async def test_two_machines_share_the_load(client, live_server, running_app):
    await add_model(running_app)
    key = await client_key(client)

    async def steady(agent, job):
        await answer(agent, job, delay=0.4)

    async with fake_agent(live_server, await agent_token(client), steady) as a, fake_agent(
        live_server, await agent_token(client), steady
    ) as b:
        responses = await asyncio.gather(complete(client, key, "un"), complete(client, key, "deux"))
        assert all(r.status_code == 200 for r in responses)
        assert len(a.assigned) == 1 and len(b.assigned) == 1


async def test_result_for_another_job_is_ignored(client, live_server, running_app):
    async def confused(agent, job):
        await agent.send("job.accepted", {"job_id": job["job_id"]})
        await agent.send("job.result", {"job_id": "00000000-0000-0000-0000-000000000000", "content": "faux"})
        await answer(agent, job)

    await add_model(running_app)
    key = await client_key(client)
    async with fake_agent(live_server, await agent_token(client), confused):
        response = await complete(client, key, "vrai")
    assert response.json()["choices"][0]["message"]["content"] == "Écho : vrai"


async def test_model_download_link_for_eligible_agents_only(client, running_app):
    await add_model(running_app, slug="petit", min_vram=7000)
    await add_model(running_app, slug="gros", min_vram=20000)
    token = await agent_token(client)
    ok = await client.get("/agent/models/petit/download", headers=bearer(token))
    assert ok.status_code == 200
    assert ok.json()["url"] == "https://exemple.com/m.gguf" and ok.json()["sha256"] == SHA
    assert (await client.get("/agent/models/gros/download", headers=bearer(token))).status_code == 403
    assert (await client.get("/agent/models/absent/download", headers=bearer(token))).status_code == 404
