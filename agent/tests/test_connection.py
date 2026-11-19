"""Connexion permanente, testée contre un faux serveur WebSocket local."""

import asyncio
import json
import socket
from pathlib import Path

import pytest
from websockets.asyncio.server import serve

from maillon_agent import connection, hardware

HW = hardware.Hardware("Linux test", "NVIDIA RTX 4070", 12282, 8, 32000, 200)


@pytest.fixture(autouse=True)
def fast_backoff(monkeypatch):
    monkeypatch.setattr(connection, "BACKOFF_START", 0.05)
    monkeypatch.setattr(connection, "with_jitter", lambda delay: delay)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def runner(port: int, tmp_path: Path) -> connection.AgentRunner:
    return connection.AgentRunner(
        websocket_url=f"ws://127.0.0.1:{port}/agent/ws",
        agent_token="ma_jeton",
        models_dir=tmp_path,
        detect_hardware=lambda: HW,
        gpu_usage=lambda: (12.5, 9000),
    )


def welcome(interval: float = 0.1) -> str:
    return json.dumps({"type": "welcome", "data": {"machine_id": "m-1", "tier": "B", "heartbeat_interval_seconds": interval}})


async def start(port: int, handler):
    return await serve(handler, "127.0.0.1", port)


async def run_until(task_runner: connection.AgentRunner, condition, timeout: float = 5):
    task = asyncio.create_task(task_runner.run_forever())
    try:
        async with asyncio.timeout(timeout):
            while not condition():
                if task.done():
                    task.result()
                await asyncio.sleep(0.02)
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, connection.StopAgent):
            pass


# ------------------------------------------------------------------- tests
def test_backoff_doubles_and_caps_at_60():
    delays, delay = [], 1.0
    for _ in range(9):
        delays.append(delay)
        delay = connection.next_backoff(delay)
    assert delays == [1, 2, 4, 8, 16, 32, 60, 60, 60]


async def test_hello_carries_token_hardware_and_heartbeats_follow(tmp_path):
    (tmp_path / "llama-3-8b.gguf").write_bytes(b"x")
    (tmp_path / "llama-3-8b.sha256").write_text("0" * 64)
    (tmp_path / "non-verifie.gguf").write_bytes(b"x")  # sans empreinte : pas annoncé
    received, headers = [], []

    async def handler(ws):
        headers.append(ws.request.headers.get("Authorization"))
        received.append(json.loads(await ws.recv()))
        await ws.send(welcome(0.05))
        async for raw in ws:
            received.append(json.loads(raw))

    port = free_port()
    async with await start(port, handler):
        await run_until(runner(port, tmp_path), lambda: len(received) >= 4)

    assert headers[0] == "Bearer ma_jeton"
    hello = received[0]
    assert hello["type"] == "hello"
    assert hello["data"]["hardware"]["vram_mb"] == 12282
    assert hello["data"]["cached_models"] == ["llama-3-8b"]
    beats = received[1:]
    assert all(m["type"] == "heartbeat" for m in beats)
    assert beats[0]["data"] == {"busy": False, "job_id": None, "gpu_util": 12.5, "vram_free_mb": 9000}


async def test_reconnects_after_the_server_drops_the_connection(tmp_path):
    sessions = []

    async def handler(ws):
        sessions.append(ws)
        await ws.recv()
        await ws.send(welcome())
        if len(sessions) < 3:
            await ws.close(code=4008, reason="silence")
        else:
            async for _ in ws:
                pass

    port = free_port()
    agent = runner(port, tmp_path)
    async with await start(port, handler):
        await run_until(agent, lambda: agent.connections >= 3)
    assert agent.connections >= 3


async def test_keeps_retrying_until_the_server_comes_back(tmp_path):
    port = free_port()
    agent = runner(port, tmp_path)
    task = asyncio.create_task(agent.run_forever())
    await asyncio.sleep(0.5)  # serveur absent : plusieurs échecs
    assert agent.connections == 0

    async def handler(ws):
        await ws.recv()
        await ws.send(welcome())
        async for _ in ws:
            pass

    async with await start(port, handler):
        async with asyncio.timeout(5):
            while agent.connections == 0:
                await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize(
    "code, exit_code",
    [
        (4001, connection.EXIT_UNAUTHORIZED),
        (4003, connection.EXIT_SUSPENDED),
        (4000, connection.EXIT_REPLACED),
    ],
)
async def test_final_close_codes_stop_the_agent(tmp_path, code, exit_code):
    async def handler(ws):
        await ws.close(code=code, reason="test")

    port = free_port()
    async with await start(port, handler):
        with pytest.raises(connection.StopAgent) as stopped:
            async with asyncio.timeout(5):
                await runner(port, tmp_path).run_forever()
    assert stopped.value.exit_code == exit_code


async def test_final_code_after_welcome_also_stops(tmp_path):
    async def handler(ws):
        await ws.recv()
        await ws.send(welcome())
        await asyncio.sleep(0.1)
        await ws.close(code=4001, reason="retirée")

    port = free_port()
    async with await start(port, handler):
        with pytest.raises(connection.StopAgent) as stopped:
            async with asyncio.timeout(5):
                await runner(port, tmp_path).run_forever()
    assert stopped.value.exit_code == connection.EXIT_UNAUTHORIZED


async def test_suspended_message_stops_with_the_reason(tmp_path):
    async def handler(ws):
        await ws.send(json.dumps({"type": "suspended", "data": {"reason": "résultats faux"}}))
        await asyncio.sleep(1)

    port = free_port()
    async with await start(port, handler):
        with pytest.raises(connection.StopAgent) as stopped:
            async with asyncio.timeout(5):
                await runner(port, tmp_path).run_forever()
    assert stopped.value.exit_code == connection.EXIT_SUSPENDED
    assert "résultats faux" in str(stopped.value)
