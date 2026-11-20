"""Machines d'un fournisseur : déclaration, liste, détail, retrait."""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent_hub import CLOSE_UNAUTHORIZED
from app.deps import get_db, require_role
from app.errors import ApiError
from app.models import Machine, User
from app.schemas import MachineCreate, MachineCreated, MachineOut
from app.security import new_enrollment_token, token_digest

router = APIRouter(prefix="/api/machines", tags=["Fournisseur"])

MAX_MACHINES_PER_PROVIDER = 50
ENROLLMENT_TOKEN_TTL = timedelta(hours=24)


async def _own_machine(db: AsyncSession, provider: User, machine_id: uuid.UUID) -> Machine:
    machine = await db.get(Machine, machine_id)
    # La machine d'un autre fournisseur répond comme une machine inexistante.
    if machine is None or machine.provider_id != provider.id or machine.removed_at is not None:
        raise ApiError(404, "not_found_error", "Machine introuvable.")
    return machine


@router.get("", response_model=list[MachineOut])
async def list_machines(
    provider: User = Depends(require_role("provider")),
    db: AsyncSession = Depends(get_db),
) -> list[MachineOut]:
    result = await db.execute(
        select(Machine)
        .where(Machine.provider_id == provider.id, Machine.removed_at.is_(None))
        .order_by(Machine.created_at.desc())
    )
    return [MachineOut.from_machine(m) for m in result.scalars()]


@router.post("", response_model=MachineCreated, status_code=status.HTTP_201_CREATED)
async def create_machine(
    payload: MachineCreate,
    provider: User = Depends(require_role("provider")),
    db: AsyncSession = Depends(get_db),
) -> MachineCreated:
    count = await db.scalar(
        select(func.count())
        .select_from(Machine)
        .where(Machine.provider_id == provider.id, Machine.removed_at.is_(None))
    )
    if count >= MAX_MACHINES_PER_PROVIDER:
        raise ApiError(
            409,
            "invalid_request_error",
            f"Limite de {MAX_MACHINES_PER_PROVIDER} machines atteinte.",
            code="too_many_machines",
        )
    token = new_enrollment_token()
    expires_at = datetime.now(timezone.utc) + ENROLLMENT_TOKEN_TTL
    machine = Machine(
        provider_id=provider.id,
        name=payload.name,
        enrollment_token_hash=token_digest(token),
        enrollment_expires_at=expires_at,
    )
    db.add(machine)
    await db.commit()
    await db.refresh(machine)
    return MachineCreated(
        machine=MachineOut.from_machine(machine),
        enrollment_token=token,
        enrollment_expires_at=expires_at,
    )


@router.get("/{machine_id}", response_model=MachineOut)
async def get_machine(
    machine_id: uuid.UUID,
    provider: User = Depends(require_role("provider")),
    db: AsyncSession = Depends(get_db),
) -> MachineOut:
    return MachineOut.from_machine(await _own_machine(db, provider, machine_id))


@router.delete("/{machine_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_machine(
    machine_id: uuid.UUID,
    request: Request,
    provider: User = Depends(require_role("provider")),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Retire la machine : ses jetons cessent de fonctionner immédiatement.
    La ligne reste en base pour l'historique des tâches et des gains."""
    machine = await _own_machine(db, provider, machine_id)
    machine.removed_at = datetime.now(timezone.utc)
    machine.agent_token_hash = None
    machine.enrollment_token_hash = None
    machine.status = "offline"
    await db.commit()
    await request.app.state.agent_hub.kick(machine.id, CLOSE_UNAUTHORIZED, "Machine retirée par le fournisseur.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
