"""Cache des modèles, moteur llama.cpp et exécution des tâches."""

import asyncio
import hashlib
import json
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from websockets.asyncio.server import serve

from maillon_agent import connection, hardware
from maillon_agent.engine import EngineError, LlamaEngine
from maillon_agent.store import ModelStore, StoreError

FAKE_SERVER = Path(__file__).with_name("fake_llama_server.py")
MODEL = b"GGUF faux modele " * 50_000  # ~850 Ko
SHA = hashlib.sha256(MODEL).hexdigest()


# ------------------------------------------------------------ serveur HTTP local
class Files:
    """Petit serveur HTTP : fichiers de modèles (avec Range) et route /agent/models/.../download."""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.ignore_range = False
        self.fail_after: int | None = None
        self.requests: list[dict] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                outer.requests.append({"path": self.path, "range": self.headers.get("Range"), "auth": self.headers.get("Authorization")})
                if self.path.startswith("/agent/models/"):
                    slug = self.path.split("/")[3]
                    if slug not in outer.files:
                        self.send_response(404); self.end_headers(); return
                    data = json.dumps({
                        "url": f"{outer.base}/files/{slug}.gguf",
                        "sha256": hashlib.sha256(outer.files[slug]).hexdigest(),
                        "size_bytes": len(outer.files[slug]),
                        "expires_at": "2099-01-01T00:00:00Z",
                    }).encode()
                    self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers()
                    self.wfile.write(data); return
                slug = Path(self.path).stem
                body = outer.files.get(slug)
                if body is None:
                    self.send_response(404); self.end_headers(); return
                start = 0
                header = self.headers.get("Range")
                if header and not outer.ignore_range:
                    start = int(header.split("=")[1].split("-")[0])
                    self.send_response(206)
                else:
                    self.send_response(200)
                chunk = body[start:]
                if outer.fail_after is not None:
                    chunk = chunk[: outer.fail_after]
                    outer.fail_after = None
                    self.send_header("Content-Length", str(len(body) - start))
                    self.end_headers()
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                self.send_header("Content-Length", str(len(chunk)))
                self.end_headers()
                self.wfile.write(chunk)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


@pytest.fixture
def files():
    server = Files()
    yield server
    server.close()


@pytest.fixture
def fake_engine(monkeypatch, tmp_path):
    """Le « binaire » llama-server est le faux serveur Python."""
    launcher = tmp_path / ("llama-server.cmd" if sys.platform == "win32" else "llama-server")
    if sys.platform == "win32":
        launcher.write_text(f'@"{sys.executable}" "{FAKE_SERVER}" %*\n')
    else:
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_SERVER}" "$@"\n')
        launcher.chmod(0o755)
    monkeypatch.setenv("MAILLON_ENGINE", "binary")
    monkeypatch.setenv("MAILLON_LLAMA_SERVER", str(launcher))


def put_model(store: ModelStore, slug: str, body: bytes = MODEL):
    store.directory.mkdir(parents=True, exist_ok=True)
    store.path(slug).write_bytes(body)
    store.digest_path(slug).write_text(hashlib.sha256(body).hexdigest())


# =================================================================== cache
def test_download_verifies_and_publishes_the_model(files, tmp_path):
    files.files["modele"] = MODEL
    store = ModelStore(tmp_path / "models")
    seen = []
    store.download("modele", f"{files.base}/files/modele.gguf", SHA, len(MODEL), lambda d, t: seen.append(d))
    assert store.has("modele") and store.digest("modele") == SHA
    assert store.cached() == ["modele"]
    assert store.path("modele").read_bytes() == MODEL and seen[-1] == len(MODEL)


def test_wrong_digest_deletes_the_file(files, tmp_path):
    files.files["modele"] = MODEL
    store = ModelStore(tmp_path / "models")
    with pytest.raises(StoreError, match="Empreinte"):
        store.download("modele", f"{files.base}/files/modele.gguf", "b" * 64, len(MODEL))
    assert not store.has("modele") and list((tmp_path / "models").iterdir()) == []


def test_interrupted_download_resumes_where_it_stopped(files, tmp_path):
    files.files["modele"] = MODEL
    files.fail_after = 300_000
    store = ModelStore(tmp_path / "models")
    with pytest.raises(StoreError, match="reprendra"):
        store.download("modele", f"{files.base}/files/modele.gguf", SHA, len(MODEL))
    assert (tmp_path / "models" / "modele.gguf.part").stat().st_size == 300_000
    store.download("modele", f"{files.base}/files/modele.gguf", SHA, len(MODEL))
    assert files.requests[-1]["range"] == "bytes=300000-"
    assert store.path("modele").read_bytes() == MODEL


def test_server_ignoring_range_restarts_from_zero(files, tmp_path):
    files.files["modele"] = MODEL
    store = ModelStore(tmp_path / "models")
    (tmp_path / "models").mkdir()
    (tmp_path / "models" / "modele.gguf.part").write_bytes(MODEL[:1000])
    files.ignore_range = True
    store.download("modele", f"{files.base}/files/modele.gguf", SHA, len(MODEL))
    assert store.path("modele").read_bytes() == MODEL


def test_refuses_plain_http_to_remote_hosts_and_bad_slugs(tmp_path):
    store = ModelStore(tmp_path)
    with pytest.raises(StoreError, match="HTTPS"):
        store.download("modele", "http://exemple.com/m.gguf", SHA, 10)
    with pytest.raises(StoreError, match="invalide"):
        store.path("../evasion")


def test_not_enough_disk_space(files, tmp_path):
    store = ModelStore(tmp_path)
    with pytest.raises(StoreError, match="Espace disque"):
        store.download("modele", f"{files.base}/files/modele.gguf", SHA, 10**15)


# ================================================================== moteur
async def test_engine_loads_answers_and_switches_model(fake_engine, tmp_path):
    store = ModelStore(tmp_path / "models")
    put_model(store, "alpha")
    put_model(store, "beta", MODEL + b"b")
    engine = LlamaEngine(store.directory)
    try:
        await engine.ensure("alpha")
        first = await engine.complete({"messages": [{"role": "user", "content": "Bonjour"}]}, timeout=10)
        assert first.content == "[alpha] Réponse à : Bonjour" and first.prompt_tokens == 11
        port = engine._port
        await engine.ensure("alpha")
        assert engine._port == port  # déjà chargé : pas de redémarrage
        await engine.ensure("beta")
        second = await engine.complete({"messages": [{"role": "user", "content": "Salut"}]}, timeout=10)
        assert second.content.startswith("[beta]")
    finally:
        await engine.stop()
    assert engine.model is None and "faux llama-server" in engine.log_path.read_text()


async def test_engine_reports_out_of_memory_at_load(fake_engine, tmp_path):
    store = ModelStore(tmp_path / "models")
    put_model(store, "enorme", b"oom" + MODEL)
    engine = LlamaEngine(store.directory)
    with pytest.raises(EngineError) as failure:
        await engine.ensure("enorme")
    assert failure.value.code == "out_of_memory"


async def test_engine_without_llama_server_nor_docker(monkeypatch, tmp_path):
    monkeypatch.setenv("MAILLON_ENGINE", "binary")
    monkeypatch.delenv("MAILLON_LLAMA_SERVER", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path))
    store = ModelStore(tmp_path / "models")
    put_model(store, "alpha")
    with pytest.raises(EngineError, match="Aucun moteur"):
        await LlamaEngine(store.directory).ensure("alpha")


# ============================================================ agent complet
def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture(autouse=True)
def fast_backoff(monkeypatch):
    monkeypatch.setattr(connection, "BACKOFF_START", 0.05)
    monkeypatch.setattr(connection, "with_jitter", lambda delay: delay)


async def test_prepare_then_run_a_job_end_to_end(fake_engine, files, tmp_path):
    """Le serveur demande un modèle, l'agent le télécharge, l'annonce, puis exécute une tâche."""
    files.files["alpha"] = MODEL
    received = []
    done = asyncio.Event()

    async def server(ws):
        received.append(json.loads(await ws.recv()))  # hello
        await ws.send(json.dumps({"type": "welcome", "data": {"machine_id": "m", "tier": "B", "heartbeat_interval_seconds": 0.2}}))
        await ws.send(json.dumps({"type": "model.prepare", "data": {"model": "alpha"}}))
        async for raw in ws:
            msg = json.loads(raw)
            received.append(msg)
            if msg["type"] == "model.cached":
                await ws.send(json.dumps({"type": "job.assign", "data": {
                    "job_id": "j-1", "model": "alpha", "timeout_seconds": 20,
                    "request": {"messages": [{"role": "user", "content": "Combien font 2 + 2 ?"}], "stream": False}}}))
            if msg["type"] in ("job.result", "job.error"):
                done.set()

    port = free_port()
    agent = connection.AgentRunner(
        websocket_url=f"ws://127.0.0.1:{port}", agent_token="ma_jeton", models_dir=tmp_path / "models",
        detect_hardware=lambda: hardware.Hardware("Linux", "RTX", 12282, 8, 32000, 200),
        gpu_usage=lambda: (None, None), server_url=files.base,
    )
    async with serve(server, "127.0.0.1", port):
        task = asyncio.create_task(agent.run_forever())
        try:
            await asyncio.wait_for(done.wait(), 30)
        finally:
            task.cancel()
            await agent.engine.stop()

    kinds = [m["type"] for m in received]
    assert kinds[0] == "hello" and received[0]["data"]["cached_models"] == []
    assert files.requests[0]["path"] == "/agent/models/alpha/download"
    assert files.requests[0]["auth"] == "Bearer ma_jeton"
    cached = next(m for m in received if m["type"] == "model.cached")
    assert cached["data"] == {"model": "alpha", "sha256": SHA}
    assert kinds.index("job.accepted") < kinds.index("job.result")
    result = next(m for m in received if m["type"] == "job.result")["data"]
    assert result["job_id"] == "j-1"
    assert result["content"] == "[alpha] Réponse à : Combien font 2 + 2 ?"
    assert result["prompt_tokens"] > 0 and result["completion_tokens"] > 0
    busy = [m["data"]["busy"] for m in received if m["type"] == "heartbeat"]
    assert busy  # des battements ont circulé pendant le téléchargement et la tâche


async def test_second_job_while_busy_is_refused_and_heartbeat_says_busy(fake_engine, tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_REPLY_SECONDS", "1.0")
    store = ModelStore(tmp_path / "models")
    put_model(store, "alpha")
    received = []
    finished = asyncio.Event()

    def assign(job_id):
        return json.dumps({"type": "job.assign", "data": {"job_id": job_id, "model": "alpha", "timeout_seconds": 20,
                          "request": {"messages": [{"role": "user", "content": job_id}]}}})

    async def server(ws):
        received.append(json.loads(await ws.recv()))
        await ws.send(json.dumps({"type": "welcome", "data": {"heartbeat_interval_seconds": 0.1}}))
        await asyncio.sleep(1.0)  # laisse le préchargement finir
        await ws.send(assign("j-1"))
        await asyncio.sleep(0.3)
        await ws.send(assign("j-2"))
        async for raw in ws:
            msg = json.loads(raw)
            received.append(msg)
            if msg["type"] == "job.result":
                finished.set()

    port = free_port()
    agent = connection.AgentRunner(
        websocket_url=f"ws://127.0.0.1:{port}", agent_token="ma_jeton", models_dir=store.directory,
        detect_hardware=lambda: hardware.Hardware("Linux", "RTX", 12282, 8, 32000, 200),
        gpu_usage=lambda: (None, None),
    )
    async with serve(server, "127.0.0.1", port):
        task = asyncio.create_task(agent.run_forever())
        try:
            await asyncio.wait_for(finished.wait(), 20)
        finally:
            task.cancel()
            await agent.engine.stop()

    assert received[0]["data"]["cached_models"] == ["alpha"]
    refused = next(m["data"] for m in received if m["type"] == "job.error")
    assert refused["job_id"] == "j-2" and refused["code"] == "busy" and refused["retryable"] is True
    beats = [m["data"] for m in received if m["type"] == "heartbeat"]
    assert any(b["busy"] and b["job_id"] == "j-1" for b in beats)
    assert next(m["data"] for m in received if m["type"] == "job.result")["job_id"] == "j-1"


async def test_job_for_a_missing_model_fails_cleanly(tmp_path):
    received = []
    got = asyncio.Event()

    async def server(ws):
        await ws.recv()
        await ws.send(json.dumps({"type": "welcome", "data": {"heartbeat_interval_seconds": 5}}))
        await ws.send(json.dumps({"type": "job.assign", "data": {"job_id": "j-1", "model": "absent", "request": {}}}))
        async for raw in ws:
            received.append(json.loads(raw))
            if received[-1]["type"] == "job.error":
                got.set()

    port = free_port()
    agent = connection.AgentRunner(
        websocket_url=f"ws://127.0.0.1:{port}", agent_token="ma_jeton", models_dir=tmp_path,
        detect_hardware=lambda: hardware.Hardware("Linux", None, 0, 8, 32000, 200), gpu_usage=lambda: (None, None),
    )
    async with serve(server, "127.0.0.1", port):
        task = asyncio.create_task(agent.run_forever())
        try:
            await asyncio.wait_for(got.wait(), 10)
        finally:
            task.cancel()
    error = next(m["data"] for m in received if m["type"] == "job.error")
    assert error["code"] == "model_missing" and error["retryable"] is True


async def test_extra_llama_args_are_passed_and_reserved_ones_refused(fake_engine, tmp_path, monkeypatch):
    store = ModelStore(tmp_path / "models")
    put_model(store, "alpha")
    monkeypatch.setenv("MAILLON_LLAMA_ARGS", "--reasoning-budget 0 --flash-attn on")
    engine = LlamaEngine(store.directory)
    try:
        await engine.ensure("alpha")
    finally:
        await engine.stop()
    assert "'--reasoning-budget', '0', '--flash-attn', 'on'" in engine.log_path.read_text()

    monkeypatch.setenv("MAILLON_LLAMA_ARGS", "--host 0.0.0.0")
    with pytest.raises(EngineError, match="interdite"):
        await LlamaEngine(store.directory).ensure("alpha")
