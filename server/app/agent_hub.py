"""Canal temps réel avec les agents : connexions ouvertes et présence des machines.

Chaque agent ouvre une connexion WebSocket sortante vers /agent/ws. Le hub garde
la connexion active de chaque machine (une seule à la fois) et tient à jour son
statut en base :

- « online » dès le message hello ;
- « offline » dès que la connexion se ferme, ou si aucun message n'arrive
  pendant heartbeat_timeout_seconds ;
- un balayage périodique remet hors ligne les machines restées « online »
  sans signal (redémarrage du serveur, processus tué).

Le hub vit dans un seul processus serveur, ce qui suffit pour la version 1.0.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import WebSocket
from sqlalchemy import true, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Machine

logger = logging.getLogger("maillon.agents")

# Codes de fermeture du canal (documentés dans docs/agent-protocol.md).
CLOSE_UNAUTHORIZED = 4001  # jeton absent, invalide ou révoqué : ne pas réessayer
CLOSE_SUSPENDED = 4003  # machine suspendue : ne pas réessayer
CLOSE_REPLACED = 4000  # une autre connexion a pris la place : ne pas réessayer
CLOSE_TIMEOUT = 4008  # aucun signal dans le délai : réessayer
CLOSE_PROTOCOL = 4002  # messages invalides répétés : réessayer après correction


class AgentHub:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory
        self._connections: dict[uuid.UUID, WebSocket] = {}
        self._lock = asyncio.Lock()

    def is_connected(self, machine_id: uuid.UUID) -> bool:
        return machine_id in self._connections

    def connected_ids(self) -> list[uuid.UUID]:
        return list(self._connections)

    @property
    def connected_count(self) -> int:
        return len(self._connections)

    async def register(self, machine_id: uuid.UUID, websocket: WebSocket) -> None:
        """Enregistre la connexion ; ferme l'éventuelle connexion précédente de la machine."""
        async with self._lock:
            previous = self._connections.get(machine_id)
            self._connections[machine_id] = websocket
        if previous is not None and previous is not websocket:
            await _close_quietly(previous, CLOSE_REPLACED, "Remplacée par une nouvelle connexion.")

    async def unregister(self, machine_id: uuid.UUID, websocket: WebSocket) -> bool:
        """Retire la connexion si c'est toujours celle de la machine. Renvoie vrai si c'était le cas."""
        async with self._lock:
            if self._connections.get(machine_id) is websocket:
                del self._connections[machine_id]
                return True
            return False

    async def kick(self, machine_id: uuid.UUID, code: int, reason: str) -> None:
        """Ferme la connexion d'une machine (retrait, suspension)."""
        async with self._lock:
            websocket = self._connections.pop(machine_id, None)
        if websocket is not None:
            await _close_quietly(websocket, code, reason)

    async def send(self, machine_id: uuid.UUID, message: dict) -> bool:
        websocket = self._connections.get(machine_id)
        if websocket is None:
            return False
        try:
            await websocket.send_json(message)
            return True
        except Exception:
            return False

    async def mark_offline(self, machine_id: uuid.UUID) -> None:
        async with self._session_factory() as db:
            await db.execute(
                update(Machine)
                .where(Machine.id == machine_id, Machine.status == "online")
                .values(status="offline")
            )
            await db.commit()

    async def sweep(self, timeout_seconds: int) -> int:
        """Passe hors ligne les machines « online » sans signal récent et sans connexion ici."""
        limit = datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)
        async with self._session_factory() as db:
            result = await db.execute(
                update(Machine)
                .where(
                    Machine.status == "online",
                    (Machine.last_heartbeat_at.is_(None)) | (Machine.last_heartbeat_at < limit),
                    Machine.id.not_in(list(self._connections)) if self._connections else true(),
                )
                .values(status="offline")
                .returning(Machine.id)
            )
            swept = [row[0] for row in result]
            await db.commit()
        if swept:
            logger.info("%d machine(s) passée(s) hors ligne faute de signal", len(swept))
        return len(swept)

    async def run_sweeper(self, interval_seconds: int, timeout_seconds: int) -> None:
        while True:
            try:
                await self.sweep(timeout_seconds)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Échec du balayage de présence")
            await asyncio.sleep(interval_seconds)

    async def close_all(self) -> None:
        async with self._lock:
            connections = list(self._connections.items())
            self._connections.clear()
        for _, websocket in connections:
            await _close_quietly(websocket, 1012, "Redémarrage du serveur.")


async def _close_quietly(websocket: WebSocket, code: int, reason: str) -> None:
    try:
        await websocket.close(code=code, reason=reason)
    except Exception:
        pass
