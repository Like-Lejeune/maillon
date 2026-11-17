"""Dépendances FastAPI : base de données, Redis, utilisateur connecté, rôles."""

from collections.abc import AsyncIterator, Callable

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ApiError
from app.models import ApiKey, Machine, User
from app.security import (
    AGENT_TOKEN_PREFIX,
    API_KEY_PREFIX,
    SESSION_TOKEN_PREFIX,
    token_digest,
)
from app.sessions import get_session_user_id

_bearer = HTTPBearer(auto_error=False)


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


def _unauthorized(message: str = "Session absente, invalide ou expirée.") -> ApiError:
    return ApiError(401, "authentication_error", message, headers={"WWW-Authenticate": "Bearer"})


async def get_session_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    if credentials is None or not credentials.credentials.startswith(SESSION_TOKEN_PREFIX):
        raise _unauthorized()
    return credentials.credentials


async def get_current_user(
    token: str = Depends(get_session_token),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> User:
    user_id = await get_session_user_id(redis, token)
    if user_id is None:
        raise _unauthorized()
    user = await db.get(User, user_id)
    if user is None:
        raise _unauthorized()
    if user.status != "active":
        raise ApiError(403, "permission_error", "Ce compte est suspendu.", code="account_suspended")
    return user


def require_role(*roles: str) -> Callable:
    """Réserve une route à certains rôles : Depends(require_role("admin"))."""

    async def _check(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise ApiError(403, "permission_error", "Rôle insuffisant pour cette action.")
        return user

    return _check


async def _bearer_with_prefix(
    credentials: HTTPAuthorizationCredentials | None, prefix: str, message: str
) -> str:
    if credentials is None or not credentials.credentials.startswith(prefix):
        raise _unauthorized(message)
    return credentials.credentials


async def get_api_key_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> tuple[User, ApiKey]:
    """Client authentifié par clé API (routes /v1). Une clé révoquée est refusée
    dès la requête suivante : elle est vérifiée en base à chaque appel."""
    message = "Clé API absente, invalide ou révoquée."
    key = await _bearer_with_prefix(credentials, API_KEY_PREFIX, message)
    api_key = (
        await db.execute(select(ApiKey).where(ApiKey.key_hash == token_digest(key)))
    ).scalar_one_or_none()
    if api_key is None or api_key.revoked_at is not None:
        raise _unauthorized(message)
    user = await db.get(User, api_key.user_id)
    if user is None or user.status != "active":
        raise ApiError(403, "permission_error", "Ce compte est suspendu.", code="account_suspended")
    await db.execute(
        update(ApiKey).where(ApiKey.id == api_key.id).values(last_used_at=func.now())
    )
    await db.commit()
    return user, api_key


async def get_agent_machine(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> Machine:
    """Machine authentifiée par son jeton d'agent (routes /agent)."""
    message = "Jeton d'agent absent, invalide ou révoqué."
    token = await _bearer_with_prefix(credentials, AGENT_TOKEN_PREFIX, message)
    machine = (
        await db.execute(
            select(Machine).where(
                Machine.agent_token_hash == token_digest(token), Machine.removed_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if machine is None:
        raise _unauthorized(message)
    return machine
