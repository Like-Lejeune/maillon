"""WebSocket /agent/ws : canal temps réel entre le serveur et chaque agent.

Déroulé (docs/agent-protocol.md) :
1. l'agent se connecte avec « Authorization: Bearer ma_... » ;
2. il envoie « hello » (version, matériel, modèles en cache) ;
3. le serveur répond « welcome » (identifiant, niveau, intervalle du battement) ;
4. l'agent envoie « heartbeat » à intervalle régulier.

Sans message pendant heartbeat_timeout_seconds, le serveur ferme la connexion
(code 4008) et la machine passe hors ligne.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select, update

from app.agent_hub import (
    CLOSE_PROTOCOL,
    CLOSE_SUSPENDED,
    CLOSE_TIMEOUT,
    CLOSE_UNAUTHORIZED,
    AgentHub,
)
from app.config import get_settings
from app.models import Machine
from app.schemas import Hardware
from app.security import AGENT_TOKEN_PREFIX, token_digest
from app.tiers import compute_tier

router = APIRouter()
logger = logging.getLogger("maillon.agents")

MAX_INVALID_MESSAGES = 5


class HelloData(BaseModel):
    agent_version: str = Field(min_length=1, max_length=40)
    hardware: Hardware
    cached_models: list[str] = Field(default_factory=list, max_length=100)


class HeartbeatData(BaseModel):
    busy: bool = False
    job_id: uuid.UUID | None = None
    gpu_util: float | None = Field(default=None, ge=0, le=100)
    vram_free_mb: int | None = Field(default=None, ge=0)


def _message(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": kind,
        "id": str(uuid.uuid4()),
        "ts": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }


def _bearer(websocket: WebSocket) -> str | None:
    header = websocket.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.startswith(AGENT_TOKEN_PREFIX):
        return None
    return token.strip()


async def _authenticate(websocket: WebSocket) -> Machine | None:
    token = _bearer(websocket)
    if token is None:
        return None
    async with websocket.app.state.session_factory() as db:
        return (
            await db.execute(
                select(Machine).where(
                    Machine.agent_token_hash == token_digest(token), Machine.removed_at.is_(None)
                )
            )
        ).scalar_one_or_none()


async def _receive(websocket: WebSocket, timeout: int) -> dict | None:
    """Attend un message JSON. None si le message n'est pas un objet JSON."""
    raw = await asyncio.wait_for(websocket.receive_text(), timeout=timeout)
    try:
        message = json.loads(raw)
    except ValueError:
        return None
    return message if isinstance(message, dict) else None


@router.websocket("/agent/ws")
async def agent_channel(websocket: WebSocket) -> None:
    settings = get_settings()
    hub: AgentHub = websocket.app.state.agent_hub
    session_factory = websocket.app.state.session_factory

    # On accepte d'abord, pour pouvoir répondre par un code de fermeture explicite.
    await websocket.accept()
    machine = await _authenticate(websocket)
    if machine is None:
        await websocket.close(code=CLOSE_UNAUTHORIZED, reason="Jeton d'agent invalide ou révoqué.")
        return
    if machine.status == "suspended":
        await websocket.send_json(_message("suspended", {"reason": machine.suspended_reason or ""}))
        await websocket.close(code=CLOSE_SUSPENDED, reason="Machine suspendue.")
        return

    machine_id = machine.id
    await hub.register(machine_id, websocket)
    greeted = False
    invalid = 0
    try:
        while True:
            try:
                message = await _receive(websocket, settings.heartbeat_timeout_seconds)
            except asyncio.TimeoutError:
                await websocket.close(code=CLOSE_TIMEOUT, reason="Aucun signal dans le délai.")
                return

            kind = message.get("type") if message else None
            data = message.get("data") if message else None
            try:
                if kind == "hello":
                    hello = HelloData.model_validate(data or {})
                    tier = await _on_hello(session_factory, machine_id, hello)
                    greeted = True
                    await websocket.send_json(
                        _message(
                            "welcome",
                            {
                                "machine_id": str(machine_id),
                                "tier": tier,
                                "heartbeat_interval_seconds": settings.heartbeat_interval_seconds,
                            },
                        )
                    )
                elif kind == "heartbeat" and greeted:
                    HeartbeatData.model_validate(data or {})
                    await _touch(session_factory, machine_id)
                elif kind == "heartbeat":
                    raise ValueError("hello attendu avant le premier heartbeat")
                else:
                    raise ValueError(f"type de message inconnu : {kind!r}")
            except (ValidationError, ValueError) as exc:
                invalid += 1
                await websocket.send_json(_message("error", {"message": str(exc)[:300]}))
                if invalid >= MAX_INVALID_MESSAGES:
                    await websocket.close(code=CLOSE_PROTOCOL, reason="Trop de messages invalides.")
                    return
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Erreur sur le canal de la machine %s", machine_id)
    finally:
        # Hors ligne seulement si c'était encore la connexion active de la machine :
        # une connexion remplacée ne doit pas faire tomber la nouvelle.
        if await hub.unregister(machine_id, websocket):
            await hub.mark_offline(machine_id)


async def _on_hello(session_factory, machine_id: uuid.UUID, hello: HelloData) -> str | None:
    hw = hello.hardware
    tier = compute_tier(hw.vram_mb, hw.ram_mb, hw.disk_free_gb)
    async with session_factory() as db:
        await db.execute(
            update(Machine)
            .where(Machine.id == machine_id, Machine.status != "suspended")
            .values(
                status="online",
                last_heartbeat_at=datetime.now(timezone.utc),
                agent_version=hello.agent_version,
                os=hw.os,
                gpu_model=hw.gpu_model,
                vram_mb=hw.vram_mb,
                cpu_cores=hw.cpu_cores,
                ram_mb=hw.ram_mb,
                disk_free_gb=hw.disk_free_gb,
                tier=tier,
            )
        )
        await db.commit()
    return tier


async def _touch(session_factory, machine_id: uuid.UUID) -> None:
    async with session_factory() as db:
        await db.execute(
            update(Machine)
            .where(Machine.id == machine_id, Machine.status != "suspended")
            .values(status="online", last_heartbeat_at=datetime.now(timezone.utc))
        )
        await db.commit()
