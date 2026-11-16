"""Sessions des interfaces web et protection de la connexion, stockées dans Redis.

Une session est un jeton opaque. Redis ne garde que son empreinte, avec une
durée de vie : la déconnexion la supprime, donc le jeton cesse de fonctionner
immédiatement.
"""

import uuid
from datetime import datetime, timedelta, timezone

from redis.asyncio import Redis

from app.config import Settings
from app.security import new_session_token, token_digest


def _session_key(token: str) -> str:
    return f"session:{token_digest(token)}"


def _user_sessions_key(user_id: uuid.UUID) -> str:
    return f"user_sessions:{user_id}"


def _failures_key(email: str) -> str:
    return f"login_failures:{token_digest(email.strip().lower())}"


async def create_session(redis: Redis, settings: Settings, user_id: uuid.UUID) -> tuple[str, datetime]:
    token = new_session_token()
    ttl = timedelta(hours=settings.session_ttl_hours)
    expires_at = datetime.now(timezone.utc) + ttl
    seconds = int(ttl.total_seconds())
    async with redis.pipeline(transaction=True) as pipe:
        pipe.set(_session_key(token), str(user_id), ex=seconds)
        pipe.sadd(_user_sessions_key(user_id), token_digest(token))
        pipe.expire(_user_sessions_key(user_id), seconds)
        await pipe.execute()
    return token, expires_at


async def get_session_user_id(redis: Redis, token: str) -> uuid.UUID | None:
    value = await redis.get(_session_key(token))
    if value is None:
        return None
    return uuid.UUID(value.decode() if isinstance(value, bytes) else value)


async def delete_session(redis: Redis, token: str) -> None:
    user_id = await get_session_user_id(redis, token)
    async with redis.pipeline(transaction=True) as pipe:
        pipe.delete(_session_key(token))
        if user_id is not None:
            pipe.srem(_user_sessions_key(user_id), token_digest(token))
        await pipe.execute()


async def delete_all_sessions(redis: Redis, user_id: uuid.UUID) -> None:
    """Ferme toutes les sessions d'un utilisateur (suspension, changement de mot de passe)."""
    digests = await redis.smembers(_user_sessions_key(user_id))
    keys = [f"session:{d.decode() if isinstance(d, bytes) else d}" for d in digests]
    if keys:
        await redis.delete(*keys)
    await redis.delete(_user_sessions_key(user_id))


async def lockout_seconds(redis: Redis, settings: Settings, email: str) -> int:
    """Secondes restantes de blocage pour cet e-mail, 0 si la connexion est permise."""
    key = _failures_key(email)
    failures = await redis.get(key)
    if failures is None or int(failures) < settings.login_max_failures:
        return 0
    remaining = await redis.ttl(key)
    return max(remaining, 1)


async def record_login_failure(redis: Redis, settings: Settings, email: str) -> None:
    key = _failures_key(email)
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, settings.login_lockout_minutes * 60)


async def clear_login_failures(redis: Redis, email: str) -> None:
    await redis.delete(_failures_key(email))
