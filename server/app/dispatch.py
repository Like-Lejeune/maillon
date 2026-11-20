"""Ordonnanceur : attribue chaque tâche à une machine et suit son exécution.

Pour une tâche, l'ordonnanceur :
1. réserve une machine en ligne, libre, assez puissante pour le modèle et qui l'a
   déjà en cache ; la réservation (machines.current_job_id) est atomique, donc deux
   tâches ne peuvent pas prendre la même machine ;
2. s'il n'y en a aucune, attend qu'une se libère, au plus queue_wait_seconds ;
3. envoie « job.assign » à l'agent, attend « job.accepted » (job_ack_seconds) puis
   « job.result » ou « job.error » (job_timeout_seconds) ;
4. libère la machine et met à jour la tâche, l'essai et la fiabilité de la machine.

La durée facturable (compute_ms) est mesurée par le serveur, entre l'accusé de
réception et le résultat ; l'agent ne déclare jamais sa propre durée.

Reprise automatique : si la machine tombe, ne répond pas, est occupée ou échoue
d'une façon qui ne tient pas à la requête, la tâche est confiée à une autre
machine, jamais deux fois la même, job_max_attempts essais au plus. Une requête
invalide et un délai d'exécution dépassé ne sont pas réattribués : le client
reçoit l'erreur aussitôt.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings

logger = logging.getLogger("maillon.dispatch")


@dataclass
class JobOutcome:
    content: str
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int
    compute_ms: int


class JobFailed(Exception):
    """Échec d'une tâche, avec le statut HTTP et le type d'erreur à renvoyer au client."""

    def __init__(
        self,
        status: int,
        error_type: str,
        code: str,
        message: str,
        retryable: bool = False,
        reassign: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.error_type = error_type
        self.code = code
        self.message = message
        self.retryable = retryable  # le client peut réessayer plus tard
        self.reassign = reassign  # l'ordonnanceur peut tenter une autre machine


@dataclass
class _Running:
    machine_id: uuid.UUID
    attempt_id: uuid.UUID
    accepted: asyncio.Event
    result: asyncio.Future
    accepted_at: float | None = None


class Dispatcher:
    def __init__(self, session_factory: async_sessionmaker, hub, settings: Settings) -> None:
        self._session_factory = session_factory
        self._hub = hub
        self._settings = settings
        self._running: dict[uuid.UUID, _Running] = {}
        self._capacity = asyncio.Condition()

    # ------------------------------------------------------------ côté client
    async def run(self, job_id: uuid.UUID, model_slug: str, min_vram_mb: int, request: dict) -> JobOutcome:
        loop = asyncio.get_running_loop()
        tried: list[uuid.UUID] = []
        last_failure: JobFailed | None = None
        for _ in range(self._settings.job_max_attempts):
            # Chaque essai dispose de sa propre attente d'une machine libre.
            deadline = loop.time() + self._settings.queue_wait_seconds
            claim = await self._claim_until(job_id, model_slug, min_vram_mb, deadline, tried)
            if claim is None:
                break
            machine_id, attempt_id = claim
            tried.append(machine_id)
            try:
                return await self._attempt(job_id, machine_id, attempt_id, model_slug, request)
            except JobFailed as failure:
                last_failure = failure
                if not failure.reassign:
                    break
                logger.info("Tâche %s : essai %d en échec (%s), autre machine.", job_id, len(tried), failure.code)

        if last_failure is None:
            await self._finish_job(job_id, "failed", "no_capacity", "Aucune machine disponible.")
            raise JobFailed(
                503,
                "no_capacity",
                "no_capacity",
                "Aucune machine disponible pour ce modèle en ce moment. Réessayez dans quelques instants.",
                retryable=True,
            )
        status = "expired" if last_failure.code == "timeout" else "failed"
        await self._finish_job(job_id, status, last_failure.code, last_failure.message)
        raise last_failure

    async def _attempt(
        self, job_id: uuid.UUID, machine_id: uuid.UUID, attempt_id: uuid.UUID, model_slug: str, request: dict
    ) -> JobOutcome:
        loop = asyncio.get_running_loop()
        running = _Running(machine_id, attempt_id, asyncio.Event(), loop.create_future())
        self._running[job_id] = running
        try:
            sent = await self._hub.send(
                machine_id,
                _message(
                    "job.assign",
                    {
                        "job_id": str(job_id),
                        "model": model_slug,
                        "request": request,
                        "timeout_seconds": self._settings.job_timeout_seconds,
                    },
                ),
            )
            if not sent:
                await self._fail_attempt(running, job_id, "machine_lost", "Machine déconnectée avant l'envoi.")
                raise self._lost()
            try:
                await asyncio.wait_for(running.accepted.wait(), self._settings.job_ack_seconds)
            except asyncio.TimeoutError:
                if not running.result.done():
                    await self._fail_attempt(running, job_id, "timed_out", "L'agent n'a pas accusé réception.")
                    await self._hub.send(machine_id, _message("job.cancel", {"job_id": str(job_id), "reason": "no_ack"}))
                    raise JobFailed(
                        502, "server_error", "agent_unresponsive", "La machine n'a pas répondu. Réessayez.",
                        retryable=True, reassign=True,
                    )
            try:
                return await asyncio.wait_for(asyncio.shield(running.result), self._settings.job_timeout_seconds)
            except asyncio.TimeoutError:
                # Pas de nouvel essai : le délai est déjà consommé, le client attend depuis trop longtemps.
                await self._fail_attempt(running, job_id, "timed_out", "Délai d'exécution dépassé.")
                await self._hub.send(machine_id, _message("job.cancel", {"job_id": str(job_id), "reason": "timeout"}))
                raise JobFailed(504, "server_error", "timeout", "La génération a dépassé le délai autorisé.", retryable=True)
        finally:
            if self._running.get(job_id) is running:
                del self._running[job_id]
            # Une issue arrivée après l'abandon (machine perdue après un délai dépassé)
            # n'intéresse plus personne : on la marque comme lue.
            if running.result.done() and not running.result.cancelled():
                running.result.exception()

    # ----------------------------------------------------- messages de l'agent
    async def on_accepted(self, machine_id: uuid.UUID, data: dict) -> None:
        job_id = _job_id(data)
        running = self._running.get(job_id) if job_id else None
        if running is None or running.machine_id != machine_id or running.accepted.is_set():
            return
        running.accepted_at = time.monotonic()
        running.accepted.set()
        async with self._session_factory() as db:
            now = datetime.now(timezone.utc)
            await db.execute(
                text("UPDATE jobs SET status = 'running', started_at = :now WHERE id = :job"),
                {"now": now, "job": job_id},
            )
            await db.execute(
                text("UPDATE job_attempts SET status = 'running' WHERE id = :attempt"),
                {"attempt": running.attempt_id},
            )
            await db.commit()

    async def on_result(self, machine_id: uuid.UUID, data: dict) -> None:
        job_id = _job_id(data)
        running = self._running.get(job_id) if job_id else None
        # Un résultat pour une tâche terminée, expirée ou confiée à une autre machine est ignoré.
        if running is None or running.machine_id != machine_id or running.result.done():
            return
        started = running.accepted_at or time.monotonic()
        outcome = JobOutcome(
            content=str(data.get("content") or ""),
            finish_reason="length" if data.get("finish_reason") == "length" else "stop",
            prompt_tokens=_non_negative_int(data.get("prompt_tokens")),
            completion_tokens=_non_negative_int(data.get("completion_tokens")),
            compute_ms=int((time.monotonic() - started) * 1000),
        )
        async with self._session_factory() as db:
            now = datetime.now(timezone.utc)
            await db.execute(
                text(
                    "UPDATE jobs SET status = 'succeeded', finished_at = :now, prompt_tokens = :pt, "
                    "completion_tokens = :ct, compute_ms = :ms WHERE id = :job"
                ),
                {"now": now, "pt": outcome.prompt_tokens, "ct": outcome.completion_tokens, "ms": outcome.compute_ms, "job": job_id},
            )
            await db.execute(
                text("UPDATE job_attempts SET status = 'succeeded', finished_at = :now WHERE id = :attempt"),
                {"now": now, "attempt": running.attempt_id},
            )
            await self._release(db, machine_id, job_id, succeeded=True)
            await db.commit()
        running.result.set_result(outcome)
        await self._notify_capacity()

    async def on_error(self, machine_id: uuid.UUID, data: dict) -> None:
        job_id = _job_id(data)
        running = self._running.get(job_id) if job_id else None
        if running is None or running.machine_id != machine_id or running.result.done():
            return
        code = str(data.get("code") or "runtime_error")[:40]
        message = str(data.get("message") or "Erreur sur la machine.")[:300]
        await self._fail_attempt(running, job_id, "failed", message)
        running.accepted.set()
        if code == "invalid_request":
            # La requête elle-même est en cause : une autre machine échouerait de même.
            failure = JobFailed(400, "invalid_request_error", code, f"Requête refusée par le moteur : {message}")
        else:
            failure = JobFailed(
                502, "server_error", code, "La machine n'a pas pu traiter la requête. Réessayez.",
                retryable=True, reassign=True,
            )
        running.result.set_exception(failure)

    async def on_machine_lost(self, machine_id: uuid.UUID) -> None:
        for job_id, running in list(self._running.items()):
            if running.machine_id == machine_id and not running.result.done():
                await self._fail_attempt(running, job_id, "machine_lost", "Machine déconnectée pendant la tâche.")
                running.accepted.set()
                running.result.set_exception(self._lost())

    async def on_capacity_changed(self) -> None:
        await self._notify_capacity()

    # -------------------------------------------------------------- interne
    async def _claim_until(self, job_id, model_slug, min_vram_mb, deadline, exclude) -> tuple[uuid.UUID, uuid.UUID] | None:
        loop = asyncio.get_running_loop()
        while True:
            claim = await self._claim(job_id, model_slug, min_vram_mb, exclude)
            if claim is not None:
                return claim
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            async with self._capacity:
                try:
                    await asyncio.wait_for(self._capacity.wait(), timeout=min(remaining, 2.0))
                except asyncio.TimeoutError:
                    pass

    async def _claim(self, job_id, model_slug, min_vram_mb, exclude) -> tuple[uuid.UUID, uuid.UUID] | None:
        connected = [m for m in self._hub.connected_ids() if m not in exclude]
        if not connected:
            return None
        async with self._session_factory() as db:
            machine_id = (
                await db.execute(
                    text(
                        """
                        UPDATE machines SET current_job_id = :job
                        WHERE id = (
                            SELECT m.id FROM machines m
                            JOIN machine_models mm ON mm.machine_id = m.id
                            JOIN models md ON md.id = mm.model_id AND md.slug = :slug
                            WHERE m.status = 'online' AND m.removed_at IS NULL AND m.tier IS NOT NULL
                              AND m.current_job_id IS NULL AND m.vram_mb >= :min_vram
                              AND m.id = ANY(:connected)
                            ORDER BY m.reliability_score DESC, random()
                            LIMIT 1
                            FOR UPDATE OF m SKIP LOCKED
                        )
                        RETURNING id
                        """
                    ),
                    {"job": job_id, "slug": model_slug, "min_vram": min_vram_mb, "connected": connected},
                )
            ).scalar_one_or_none()
            if machine_id is None:
                await db.rollback()
                return None
            attempt_id = (
                await db.execute(
                    text(
                        """
                        WITH j AS (
                            UPDATE jobs SET status = 'assigned', machine_id = :machine,
                                   attempts = attempts + 1, assigned_at = now()
                            WHERE id = :job RETURNING attempts
                        )
                        INSERT INTO job_attempts (job_id, machine_id, attempt_no)
                        SELECT :job, :machine, attempts FROM j
                        RETURNING id
                        """
                    ),
                    {"job": job_id, "machine": machine_id},
                )
            ).scalar_one()
            await db.commit()
        return machine_id, attempt_id

    async def _fail_attempt(self, running: _Running, job_id, attempt_status: str, message: str) -> None:
        """Clôt l'essai en échec et libère la machine. Le statut final de la tâche est
        posé par run(), une fois tous les essais faits."""
        async with self._session_factory() as db:
            await db.execute(
                text(
                    "UPDATE job_attempts SET status = CAST(:status AS attempt_status), finished_at = now(), "
                    "error_message = :message WHERE id = :attempt AND finished_at IS NULL"
                ),
                {"status": attempt_status, "message": message, "attempt": running.attempt_id},
            )
            await self._release(db, running.machine_id, job_id, succeeded=False)
            await db.commit()
        await self._notify_capacity()

    async def _finish_job(self, job_id, status: str, error_code: str, error_message: str) -> None:
        async with self._session_factory() as db:
            await db.execute(
                text(
                    "UPDATE jobs SET status = CAST(:status AS job_status), finished_at = now(), "
                    "error_code = :code, error_message = :message WHERE id = :job"
                ),
                {"status": status, "code": error_code, "message": error_message, "job": job_id},
            )
            await db.commit()

    @staticmethod
    async def _release(db, machine_id, job_id, succeeded: bool) -> None:
        column = "jobs_succeeded" if succeeded else "jobs_failed"
        await db.execute(
            text(
                f"""
                UPDATE machines SET
                    current_job_id = CASE WHEN current_job_id = :job THEN NULL ELSE current_job_id END,
                    {column} = {column} + 1,
                    reliability_score = round(
                        (jobs_succeeded + {1 if succeeded else 0})::numeric
                        / greatest(jobs_succeeded + jobs_failed + 1, 1), 3)
                WHERE id = :machine
                """
            ),
            {"job": job_id, "machine": machine_id},
        )

    @staticmethod
    def _lost() -> JobFailed:
        return JobFailed(
            503, "no_capacity", "machine_lost", "La machine s'est déconnectée. Réessayez.",
            retryable=True, reassign=True,
        )

    async def _notify_capacity(self) -> None:
        async with self._capacity:
            self._capacity.notify_all()


def _message(kind: str, data: dict) -> dict:
    return {"type": kind, "id": str(uuid.uuid4()), "ts": datetime.now(timezone.utc).isoformat(), "data": data}


def _job_id(data: dict) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(data.get("job_id")))
    except (ValueError, TypeError):
        return None


def _non_negative_int(value) -> int:
    try:
        return max(int(value), 0)
    except (TypeError, ValueError):
        return 0

