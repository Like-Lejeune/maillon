"""Connexion permanente de l'agent au serveur (docs/agent-protocol.md).

L'agent ouvre lui-même la connexion (sortante), ce qui traverse les box et les
pare-feu. Il envoie « hello », attend « welcome », puis un « heartbeat » à
l'intervalle demandé par le serveur. En cas de coupure, il se reconnecte seul,
avec une attente qui double à chaque échec (1 s, 2 s, 4 s… jusqu'à 60 s).

Certains codes de fermeture arrêtent l'agent au lieu de le faire réessayer :
jeton refusé (4001), machine suspendue (4003), connexion remplacée (4000).
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import subprocess
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidStatus, InvalidURI

from maillon_agent import __version__, hardware

logger = logging.getLogger("maillon_agent")

CLOSE_REPLACED = 4000
CLOSE_UNAUTHORIZED = 4001
CLOSE_SUSPENDED = 4003

# Code de sortie du programme pour chaque arrêt définitif.
EXIT_UNAUTHORIZED = 3
EXIT_SUSPENDED = 4
EXIT_REPLACED = 5

BACKOFF_START = 1.0
BACKOFF_MAX = 60.0
WELCOME_TIMEOUT = 15.0


class StopAgent(Exception):
    """Arrêt définitif : réessayer ne servirait à rien."""

    def __init__(self, exit_code: int, message: str) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def next_backoff(current: float) -> float:
    return min(current * 2, BACKOFF_MAX)


def with_jitter(delay: float) -> float:
    """±20 % pour que des centaines d'agents ne se reconnectent pas tous à la même seconde."""
    return delay * random.uniform(0.8, 1.2)


def message(kind: str, data: dict) -> str:
    return json.dumps(
        {
            "type": kind,
            "id": str(uuid.uuid4()),
            "ts": datetime.now(timezone.utc).isoformat(),
            "data": data,
        }
    )


def cached_models(models_dir: Path) -> list[str]:
    if not models_dir.is_dir():
        return []
    return sorted(path.stem for path in models_dir.glob("*.gguf"))[:100]


def gpu_usage() -> tuple[float | None, int | None]:
    """Charge du GPU (%) et mémoire libre (Mio), ou (None, None) sans GPU."""
    executable = hardware.find_nvidia_smi()
    if executable is None:
        return None, None
    try:
        result = subprocess.run(
            [executable, "--query-gpu=utilization.gpu,memory.free", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        first = result.stdout.strip().splitlines()[0]
        util, free = (part.strip() for part in first.split(","))
        return float(util), int(float(free))
    except (OSError, subprocess.TimeoutExpired, IndexError, ValueError):
        return None, None


@dataclass
class AgentRunner:
    websocket_url: str
    agent_token: str
    models_dir: Path
    detect_hardware: Callable[[], hardware.Hardware]
    gpu_usage: Callable[[], tuple[float | None, int | None]] = gpu_usage
    # Rempli à chaque connexion réussie ; sert aux tests et aux journaux.
    connections: int = field(default=0, init=False)

    async def run_forever(self) -> None:
        """Se connecte et se reconnecte jusqu'à un arrêt définitif (StopAgent)."""
        delay = BACKOFF_START
        while True:
            try:
                await self._session()
                # Session terminée normalement après un welcome : on repart vite.
                delay = BACKOFF_START
            except StopAgent:
                raise
            except _Welcomed as session:
                # La connexion avait abouti avant de tomber : attente remise à zéro.
                delay = BACKOFF_START
                logger.warning("Connexion perdue (%s).", session.reason)
            except (OSError, ConnectionClosed, InvalidStatus, asyncio.TimeoutError) as exc:
                logger.warning("Connexion impossible (%s).", _describe(exc))
            wait = with_jitter(delay)
            logger.info("Nouvelle tentative dans %.0f s.", wait)
            await asyncio.sleep(wait)
            delay = next_backoff(delay)

    async def _session(self) -> None:
        try:
            async with connect(
                self.websocket_url,
                additional_headers={"Authorization": f"Bearer {self.agent_token}"},
                user_agent_header=f"maillon-agent/{__version__}",
                open_timeout=15,
                ping_interval=20,
                ping_timeout=20,
                max_size=4 * 1024 * 1024,
            ) as ws:
                try:
                    await self._converse(ws)
                except ConnectionClosed as exc:
                    # Le serveur peut fermer avant même notre hello (jeton refusé) :
                    # le code de fermeture décide s'il faut s'arrêter.
                    self._raise_if_final(exc)
                    raise
        except InvalidURI as exc:
            raise StopAgent(EXIT_UNAUTHORIZED, f"Adresse du serveur invalide : {exc}") from exc

    async def _converse(self, ws: ClientConnection) -> None:
        hw = self.detect_hardware()
        await ws.send(
            message(
                "hello",
                {
                    "agent_version": __version__,
                    "hardware": hw.as_payload(),
                    "cached_models": cached_models(self.models_dir),
                },
            )
        )
        welcome = await self._wait_welcome(ws)
        self.connections += 1
        interval = float(welcome.get("heartbeat_interval_seconds") or 10)
        logger.info(
            "Connecté. Machine %s, niveau %s, signal toutes les %.0f s.",
            welcome.get("machine_id"),
            welcome.get("tier") or "aucun",
            interval,
        )
        heartbeat = asyncio.create_task(self._heartbeats(ws, interval))
        try:
            await self._listen(ws)
        except ConnectionClosed as exc:
            self._raise_if_final(exc)
            raise _Welcomed(_describe(exc)) from exc
        finally:
            heartbeat.cancel()

    async def _wait_welcome(self, ws: ClientConnection) -> dict:
        try:
            while True:
                raw = await asyncio.wait_for(ws.recv(), WELCOME_TIMEOUT)
                incoming = _parse(raw)
                kind = incoming.get("type")
                if kind == "welcome":
                    return incoming.get("data") or {}
                if kind == "suspended":
                    raise StopAgent(EXIT_SUSPENDED, _suspended_text(incoming))
                if kind == "error":
                    logger.error("Le serveur a refusé hello : %s", (incoming.get("data") or {}).get("message"))
        except ConnectionClosed as exc:
            self._raise_if_final(exc)
            raise

    async def _heartbeats(self, ws: ClientConnection, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            util, free = await asyncio.to_thread(self.gpu_usage)
            data = {"busy": False, "job_id": None, "gpu_util": util, "vram_free_mb": free}
            try:
                await ws.send(message("heartbeat", data))
            except ConnectionClosed:
                return

    async def _listen(self, ws: ClientConnection) -> None:
        async for raw in ws:
            incoming = _parse(raw)
            kind = incoming.get("type")
            data = incoming.get("data") or {}
            if kind == "suspended":
                raise StopAgent(EXIT_SUSPENDED, _suspended_text(incoming))
            if kind == "error":
                logger.error("Message refusé par le serveur : %s", data.get("message"))
            elif kind == "job.assign":
                # L'exécution des tâches arrive avec le conteneur d'inférence.
                await ws.send(
                    message(
                        "job.error",
                        {
                            "job_id": data.get("job_id"),
                            "code": "runtime_error",
                            "message": "Exécution des tâches pas encore disponible dans cette version.",
                            "retryable": True,
                        },
                    )
                )
            else:
                logger.debug("Message ignoré : %s", kind)

    @staticmethod
    def _raise_if_final(exc: ConnectionClosed) -> None:
        code = exc.rcvd.code if exc.rcvd else None
        reason = exc.rcvd.reason if exc.rcvd else ""
        if code == CLOSE_UNAUTHORIZED:
            raise StopAgent(
                EXIT_UNAUTHORIZED,
                "Le serveur refuse le jeton de cette machine (retirée de l'espace fournisseur ?). "
                "Réinscrivez-la : maillon-agent enroll --force",
            ) from exc
        if code == CLOSE_SUSPENDED:
            raise StopAgent(EXIT_SUSPENDED, f"Machine suspendue. {reason}".strip()) from exc
        if code == CLOSE_REPLACED:
            raise StopAgent(
                EXIT_REPLACED,
                "Un autre agent s'est connecté avec le même jeton : celui-ci s'arrête.",
            ) from exc


class _Welcomed(Exception):
    """La session avait abouti (welcome reçu) avant d'être coupée."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _parse(raw: str | bytes) -> dict:
    try:
        value = json.loads(raw)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _suspended_text(incoming: dict) -> str:
    reason = (incoming.get("data") or {}).get("reason") or "aucune raison indiquée"
    return f"Machine suspendue par l'administrateur : {reason}."


def _describe(exc: BaseException) -> str:
    if isinstance(exc, ConnectionClosed):
        if exc.rcvd:
            return f"fermée par le serveur, code {exc.rcvd.code} {exc.rcvd.reason}".strip()
        return "fermée sans code"
    if isinstance(exc, InvalidStatus):
        return f"refusée, HTTP {exc.response.status_code}"
    if isinstance(exc, asyncio.TimeoutError):
        return "délai dépassé"
    return exc.__class__.__name__
