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
from sqlalchemy import select, text, update

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


class ModelCachedData(BaseModel):
    model: str = Field(min_length=1, max_length=120)
    sha256: str = Field(min_length=64, max_length=64)


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
    dispatcher = websocket.app.state.dispatcher
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
                    # Demande à la machine de télécharger les modèles qu'elle peut servir.
                    for slug in await _models_to_prepare(session_factory, machine_id):
                        await websocket.send_json(_message("model.prepare", {"model": slug}))
                    await dispatcher.on_capacity_changed()
                elif not greeted:
                    raise ValueError("hello attendu avant tout autre message")
                elif kind == "heartbeat":
                    HeartbeatData.model_validate(data or {})
                    await _touch(session_factory, machine_id)
                elif kind == "job.accepted":
                    await dispatcher.on_accepted(machine_id, data or {})
                elif kind == "job.result":
                    await dispatcher.on_result(machine_id, data or {})
                elif kind == "job.error":
                    await dispatcher.on_error(machine_id, data or {})
                elif kind == "model.cached":
                    cached = ModelCachedData.model_validate(data or {})
                    if await _on_model_cached(session_factory, machine_id, cached):
                        await dispatcher.on_capacity_changed()
                    else:
                        raise ValueError(f"modèle inconnu ou empreinte différente : {cached.model}")
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
            await dispatcher.on_machine_lost(machine_id)
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
        # Modèles en cache annoncés par l'agent : seuls ceux du catalogue comptent.
        await db.execute(text("DELETE FROM machine_models WHERE machine_id = :m"), {"m": machine_id})
        if hello.cached_models:
            await db.execute(
                text(
                    "INSERT INTO machine_models (machine_id, model_id) "
                    "SELECT :m, id FROM models WHERE slug = ANY(:slugs) AND is_active"
                ),
                {"m": machine_id, "slugs": hello.cached_models},
            )
        await db.commit()
    return tier


async def _models_to_prepare(session_factory, machine_id: uuid.UUID) -> list[str]:
    async with session_factory() as db:
        rows = await db.execute(
            text(
                """
                SELECT md.slug FROM models md
                JOIN machines m ON m.id = :m
                WHERE md.is_active AND m.tier IS NOT NULL AND m.vram_mb >= md.min_vram_mb
                  AND NOT EXISTS (
                      SELECT 1 FROM machine_models mm WHERE mm.machine_id = m.id AND mm.model_id = md.id
                  )
                ORDER BY md.slug
                """
            ),
            {"m": machine_id},
        )
        return [row[0] for row in rows]


async def _on_model_cached(session_factory, machine_id: uuid.UUID, cached: ModelCachedData) -> bool:
    """Enregistre un modèle téléchargé, si son empreinte est celle du catalogue."""
    async with session_factory() as db:
        result = await db.execute(
            text(
                """
                INSERT INTO machine_models (machine_id, model_id)
                SELECT :m, id FROM models WHERE slug = :slug AND is_active AND file_sha256 = :sha
                ON CONFLICT DO NOTHING
                RETURNING model_id
                """
            ),
            {"m": machine_id, "slug": cached.model, "sha": cached.sha256.lower()},
        )
        inserted = result.first() is not None
        if not inserted:
            exists = await db.execute(
                text(
                    "SELECT 1 FROM machine_models mm JOIN models md ON md.id = mm.model_id "
                    "WHERE mm.machine_id = :m AND md.slug = :slug AND md.file_sha256 = :sha"
                ),
                {"m": machine_id, "slug": cached.model, "sha": cached.sha256.lower()},
            )
            inserted = exists.first() is not None
        await db.commit()
        return inserted


async def _touch(session_factory, machine_id: uuid.UUID) -> None:
    async with session_factory() as db:
        await db.execute(
            update(Machine)
            .where(Machine.id == machine_id, Machine.status != "suspended")
            .values(status="online", last_heartbeat_at=datetime.now(timezone.utc))
        )
        await db.commit()
