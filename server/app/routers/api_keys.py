"""Clés API des clients : création, liste, révocation."""

import uuid

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db, require_role
from app.errors import ApiError
from app.models import ApiKey, User
from app.schemas import ApiKeyCreate, ApiKeyCreated, ApiKeyOut
from app.security import new_api_key, token_digest

router = APIRouter(prefix="/api/api-keys", tags=["Client"])

MAX_ACTIVE_KEYS = 20


@router.get("", response_model=list[ApiKeyOut])
async def list_api_keys(
    user: User = Depends(require_role("client")),
    db: AsyncSession = Depends(get_db),
) -> list[ApiKey]:
    result = await db.execute(
        select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at.desc())
    )
    return list(result.scalars())


@router.post("", response_model=ApiKeyCreated, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    payload: ApiKeyCreate,
    user: User = Depends(require_role("client")),
    db: AsyncSession = Depends(get_db),
) -> ApiKeyCreated:
    active = await db.scalar(
        select(func.count())
        .select_from(ApiKey)
        .where(ApiKey.user_id == user.id, ApiKey.revoked_at.is_(None))
    )
    if active >= MAX_ACTIVE_KEYS:
        raise ApiError(
            409,
            "invalid_request_error",
            f"Limite de {MAX_ACTIVE_KEYS} clés actives atteinte : révoquez-en une d'abord.",
            code="too_many_keys",
        )
    # Une collision de préfixe est très improbable ; on réessaie au cas où.
    for _ in range(3):
        key, prefix = new_api_key()
        api_key = ApiKey(
            user_id=user.id, name=payload.name, key_prefix=prefix, key_hash=token_digest(key)
        )
        db.add(api_key)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            continue
        await db.refresh(api_key)
        return ApiKeyCreated(**ApiKeyOut.model_validate(api_key).model_dump(), key=key)
    raise ApiError(500, "server_error", "Impossible de créer la clé, réessayez.")


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    key_id: uuid.UUID,
    user: User = Depends(require_role("client")),
    db: AsyncSession = Depends(get_db),
) -> Response:
    api_key = await db.get(ApiKey, key_id)
    # La clé d'un autre compte répond comme une clé inexistante.
    if api_key is None or api_key.user_id != user.id:
        raise ApiError(404, "not_found_error", "Clé introuvable.")
    if api_key.revoked_at is None:
        await db.execute(
            update(ApiKey).where(ApiKey.id == key_id).values(revoked_at=func.now())
        )
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
