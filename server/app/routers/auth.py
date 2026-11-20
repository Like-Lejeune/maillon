"""Inscription, connexion, déconnexion, profil."""

from fastapi import APIRouter, Depends, Response, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.deps import get_current_user, get_db, get_redis, get_session_token
from app.errors import ApiError
from app.models import Balance, User
from app.schemas import LoginRequest, RegisterRequest, SessionOut, UserOut
from app.security import hash_password, password_needs_rehash, verify_password
from app.sessions import (
    clear_login_failures,
    create_session,
    delete_session,
    lockout_seconds,
    record_login_failure,
)

router = APIRouter(prefix="/api", tags=["Authentification"])


@router.post("/auth/register", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def register(
    payload: RegisterRequest,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings),
) -> SessionOut:
    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        name=payload.name,
        role=payload.role,
    )
    db.add(user)
    try:
        await db.flush()
        # Chaque compte a une ligne de solde dès sa création.
        db.add(Balance(user_id=user.id))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ApiError(
            409,
            "invalid_request_error",
            "Un compte existe déjà avec cette adresse e-mail.",
            code="email_taken",
            param="email",
        )
    await db.refresh(user)
    token, expires_at = await create_session(redis, settings, user.id)
    return SessionOut(token=token, expires_at=expires_at, user=UserOut.model_validate(user))


@router.post("/auth/login", response_model=SessionOut)
async def login(
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    settings: Settings = Depends(get_settings),
) -> SessionOut:
    wait = await lockout_seconds(redis, settings, payload.email)
    if wait:
        raise ApiError(
            429,
            "rate_limit_error",
            "Trop d'essais de connexion. Réessayez plus tard.",
            code="login_locked",
            headers={"Retry-After": str(wait)},
        )

    user = (await db.execute(select(User).where(User.email == payload.email))).scalar_one_or_none()
    # Le mot de passe est vérifié même si le compte n'existe pas, pour que la
    # réponse prenne le même temps et porte le même message dans les deux cas.
    if not verify_password(payload.password, user.password_hash if user else None):
        await record_login_failure(redis, settings, payload.email)
        raise ApiError(
            401,
            "authentication_error",
            "Adresse e-mail ou mot de passe incorrect.",
            code="invalid_credentials",
        )
    if user.status != "active":
        raise ApiError(403, "permission_error", "Ce compte est suspendu.", code="account_suspended")

    await clear_login_failures(redis, payload.email)
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)
        await db.commit()
        await db.refresh(user)

    token, expires_at = await create_session(redis, settings, user.id)
    return SessionOut(token=token, expires_at=expires_at, user=UserOut.model_validate(user))


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    token: str = Depends(get_session_token),
    redis: Redis = Depends(get_redis),
) -> Response:
    await delete_session(redis, token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> User:
    return user
