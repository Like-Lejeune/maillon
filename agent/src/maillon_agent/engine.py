"""Moteur d'inférence : un serveur llama.cpp (llama-server) local, un modèle à la fois.

Deux façons de le lancer, choisies par MAILLON_ENGINE (« auto » par défaut) :
- binary : l'exécutable llama-server, trouvé dans le PATH ou indiqué par
  MAILLON_LLAMA_SERVER (Windows : version CUDA téléchargée depuis les
  publications de llama.cpp) ;
- docker : l'image officielle avec accès au GPU (MAILLON_LLAMA_IMAGE, par défaut
  ghcr.io/ggml-org/llama.cpp:server-cuda), qui demande le NVIDIA Container Toolkit.
« auto » prend le binaire s'il existe, sinon Docker.

MAILLON_LLAMA_ARGS ajoute des options à llama-server, par exemple pour régler la
« réflexion » de certains modèles. Les options d'adresse, de port et de modèle
restent fixées par l'agent.

llama-server n'écoute que sur 127.0.0.1 : rien n'est exposé sur le réseau.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

logger = logging.getLogger("maillon_agent")

DEFAULT_IMAGE = "ghcr.io/ggml-org/llama.cpp:server-cuda"
LOAD_TIMEOUT_SECONDS = 300


class EngineError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass
class Completion:
    content: str
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


_RESERVED = {"-m", "--model", "--host", "--port", "-hf", "--hf-repo", "--api-key"}


def extra_args() -> list[str]:
    """Options supplémentaires de MAILLON_LLAMA_ARGS, sans celles que l'agent fixe lui-même."""
    args = shlex.split(os.environ.get("MAILLON_LLAMA_ARGS", ""), posix=os.name != "nt")
    for arg in args:
        if arg.split("=", 1)[0] in _RESERVED:
            raise EngineError("runtime_error", f"Option interdite dans MAILLON_LLAMA_ARGS : {arg}", retryable=False)
    return args


def detect_mode() -> tuple[str, str] | None:
    """Renvoie (mode, exécutable), ou None si aucun moteur n'est disponible."""
    wanted = os.environ.get("MAILLON_ENGINE", "auto").lower()
    binary = os.environ.get("MAILLON_LLAMA_SERVER") or shutil.which("llama-server")
    docker = shutil.which("docker")
    if wanted in ("auto", "binary") and binary:
        return "binary", binary
    if wanted in ("auto", "docker") and docker:
        return "docker", docker
    return None


class LlamaEngine:
    def __init__(self, models_dir: Path, context: int = 8192, log_path: Path | None = None) -> None:
        self.models_dir = models_dir
        self.context = context
        # Les journaux de llama-server vont dans un fichier : un tuyau non lu finirait
        # par se remplir et bloquer le moteur.
        self.log_path = log_path or models_dir.parent / "engine.log"
        self._log = None
        self.model: str | None = None
        self._process: subprocess.Popen | None = None
        self._container: str | None = None
        self._port: int | None = None
        self._lock = asyncio.Lock()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    async def ensure(self, slug: str) -> None:
        """Charge le modèle s'il ne l'est pas déjà (un seul modèle en mémoire à la fois)."""
        async with self._lock:
            if self.model == slug and self._alive():
                return
            await asyncio.to_thread(self._stop)
            await self._start(slug)

    async def complete(self, request: dict, timeout: float) -> Completion:
        payload = {key: value for key, value in request.items() if key != "model"}
        payload["stream"] = False
        try:
            async with httpx.AsyncClient(timeout=timeout) as http:
                response = await http.post(f"{self.base_url}/v1/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise EngineError("runtime_error", "Le moteur n'a pas répondu à temps.") from exc
        except httpx.HTTPError as exc:
            raise EngineError("runtime_error", f"Moteur injoignable ({exc.__class__.__name__}).") from exc
        if response.status_code >= 400:
            text = response.text[:300]
            if "out of memory" in text.lower():
                raise EngineError("out_of_memory", "Mémoire GPU insuffisante.")
            retryable = response.status_code >= 500
            raise EngineError("runtime_error" if retryable else "invalid_request", f"Moteur : {text}", retryable)
        body = response.json()
        choice = (body.get("choices") or [{}])[0]
        usage = body.get("usage") or {}
        return Completion(
            content=(choice.get("message") or {}).get("content") or "",
            finish_reason=choice.get("finish_reason") or "stop",
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
        )

    async def stop(self) -> None:
        async with self._lock:
            await asyncio.to_thread(self._stop)

    # ---------------------------------------------------------------- interne
    async def _start(self, slug: str) -> None:
        model_file = self.models_dir / f"{slug}.gguf"
        if not model_file.is_file():
            raise EngineError("model_missing", f"Modèle {slug} absent du cache.")
        found = detect_mode()
        if found is None:
            raise EngineError(
                "runtime_error",
                "Aucun moteur llama.cpp : installez llama-server ou Docker (voir le guide de l'agent).",
                retryable=True,
            )
        mode, executable = found
        self._port = _free_port()
        extra = extra_args()
        common = ["--host", "127.0.0.1", "--port", str(self._port), "-c", str(self.context), "-ngl", "999", *extra]
        if mode == "binary":
            command = [executable, "-m", str(model_file), *common]
        else:
            self._container = f"maillon-llama-{self._port}"
            image = os.environ.get("MAILLON_LLAMA_IMAGE", DEFAULT_IMAGE)
            command = [
                executable, "run", "--rm", "--name", self._container, "--gpus", "all",
                "-v", f"{self.models_dir.resolve()}:/models:ro",
                "-p", f"127.0.0.1:{self._port}:8080",
                image, "-m", f"/models/{model_file.name}",
                "--host", "0.0.0.0", "--port", "8080", "-c", str(self.context), "-ngl", "999", *extra,
            ]
        logger.info("Chargement du modèle %s (moteur : %s)…", slug, mode)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log = self.log_path.open("ab")
        self._log_start = self._log.tell()
        self._process = subprocess.Popen(
            command, stdout=self._log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL
        )
        self.model = slug
        await self._wait_ready()
        logger.info("Modèle %s prêt.", slug)

    async def _wait_ready(self) -> None:
        deadline = time.monotonic() + LOAD_TIMEOUT_SECONDS
        async with httpx.AsyncClient(timeout=5) as http:
            while time.monotonic() < deadline:
                if not self._alive():
                    error = self._log_tail()
                    self.model = None
                    code = "out_of_memory" if "out of memory" in error.lower() else "runtime_error"
                    raise EngineError(code, f"Le moteur s'est arrêté au chargement. {error}".strip())
                try:
                    if (await http.get(f"{self.base_url}/health")).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                await asyncio.sleep(0.5)
        await asyncio.to_thread(self._stop)
        raise EngineError("runtime_error", "Chargement du modèle trop long.")

    def _alive(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _stop(self) -> None:
        if self._container:
            subprocess.run(["docker", "stop", "-t", "5", self._container], capture_output=True, check=False)
            self._container = None
        if self._process is not None:
            if self._process.poll() is None:
                self._process.terminate()
                try:
                    self._process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=5)
        if self._log is not None:
            self._log.close()
            self._log = None
        self._process = None
        self.model = None

    def _log_tail(self) -> str:
        """Fin du journal écrit par le moteur depuis son dernier démarrage."""
        try:
            with self.log_path.open("rb") as log:
                log.seek(max(self._log_start, log.seek(0, 2) - 2000))
                return log.read().decode(errors="replace")[-500:].strip()
        except OSError:
            return ""
