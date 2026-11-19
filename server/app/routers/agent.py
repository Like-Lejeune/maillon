"""Routes utilisées par l'agent installé sur les machines."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_agent_machine, get_db
from app.errors import ApiError
from app.models import Machine, Model
from app.schemas import EnrollRequest, EnrollResponse, MachineOut
from app.security import ENROLLMENT_TOKEN_PREFIX, new_agent_token, token_digest
from app.tiers import compute_tier, ineligibility_reason

router = APIRouter(prefix="/agent", tags=["Agent"])


def _websocket_url(request: Request) -> str:
    base = str(request.base_url).rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://") :]
    return f"{base}/agent/ws"


@router.post("/enroll", response_model=EnrollResponse, status_code=status.HTTP_201_CREATED)
async def enroll(
    payload: EnrollRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> EnrollResponse:
    """Échange le jeton d'inscription (usage unique, 24 h) contre un jeton d'agent."""
    invalid = ApiError(
        401,
        "authentication_error",
        "Jeton d'inscription invalide, déjà utilisé ou expiré. "
        "Créez-en un nouveau depuis l'espace fournisseur.",
        code="invalid_enrollment_token",
    )
    if not payload.enrollment_token.startswith(ENROLLMENT_TOKEN_PREFIX):
        raise invalid
    machine = (
        await db.execute(
            select(Machine)
            .where(
                Machine.enrollment_token_hash == token_digest(payload.enrollment_token),
                Machine.removed_at.is_(None),
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if machine is None or machine.enrollment_expires_at is None or machine.enrollment_expires_at < now:
        raise invalid

    hw = payload.hardware
    agent_token = new_agent_token()
    machine.enrollment_token_hash = None
    machine.enrollment_expires_at = None
    machine.agent_token_hash = token_digest(agent_token)
    machine.agent_version = payload.agent_version
    machine.os = hw.os
    machine.gpu_model = hw.gpu_model
    machine.vram_mb = hw.vram_mb
    machine.cpu_cores = hw.cpu_cores
    machine.ram_mb = hw.ram_mb
    machine.disk_free_gb = hw.disk_free_gb
    machine.tier = compute_tier(hw.vram_mb, hw.ram_mb, hw.disk_free_gb)
    # « online » seulement quand le canal temps réel sera ouvert (tâche du 18).
    machine.status = "offline"
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ApiError(500, "server_error", "Inscription impossible, réessayez.")

    reason = ineligibility_reason(hw.vram_mb, hw.ram_mb, hw.disk_free_gb)
    return EnrollResponse(
        machine_id=machine.id,
        agent_token=agent_token,
        websocket_url=_websocket_url(request),
        tier=machine.tier,
        eligible=reason is None,
        ineligibility_reason=reason,
    )


@router.get("/me", response_model=MachineOut)
async def agent_me(machine: Machine = Depends(get_agent_machine)) -> MachineOut:
    """Permet à l'agent de vérifier que son jeton est toujours valide."""
    return MachineOut.from_machine(machine)


@router.get("/models/{slug}/download")
async def model_download(
    slug: str,
    machine: Machine = Depends(get_agent_machine),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Où télécharger un modèle, et l'empreinte à vérifier après téléchargement.

    Les fichiers des modèles ouverts sont publics (par exemple sur Hugging Face) :
    l'agent les télécharge directement, sans passer par le VPS.
    """
    model = (
        await db.execute(select(Model).where(Model.slug == slug, Model.is_active.is_(True)))
    ).scalar_one_or_none()
    if model is None:
        raise ApiError(404, "not_found_error", f"Modèle inconnu : {slug}.")
    if machine.tier is None or (machine.vram_mb or 0) < model.min_vram_mb:
        raise ApiError(403, "permission_error", "Cette machine ne peut pas servir ce modèle.")
    return {
        "url": model.file_url,
        "sha256": model.file_sha256,
        "size_bytes": model.file_size_bytes,
        "expires_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
    }
