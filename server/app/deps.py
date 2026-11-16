"""Dépendances FastAPI : base de données, Redis, utilisateur connecté, rôles."""

from collections.abc import AsyncIterator, Callable

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import ApiError
from app.models import User
from app.security import SESSION_TOKEN_PREFIX
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
