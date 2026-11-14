"""Point d'entrée du serveur Maillon.

Ce socle n'expose que les contrôles de santé. Les routes du contrat
(docs/openapi.yaml) s'ajoutent tâche par tâche.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    app.state.redis = Redis.from_url(settings.redis_url, socket_connect_timeout=2)
    yield
    await app.state.redis.aclose()
    await app.state.engine.dispose()


app = FastAPI(
    title="API Maillon",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/health/live", include_in_schema=False)
async def live() -> dict:
    """Le processus répond. Utilisé par Docker pour redémarrer le conteneur."""
    return {"status": "ok"}


async def _check_database(engine: AsyncEngine) -> bool:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


async def _check_redis(redis: Redis) -> bool:
    try:
        return bool(await redis.ping())
    except Exception:
        return False


@app.get("/health", include_in_schema=False)
async def health() -> JSONResponse:
    """Le serveur et ses dépendances répondent. Utilisé par le déploiement."""
    checks = {
        "database": await _check_database(app.state.engine),
        "redis": await _check_redis(app.state.redis),
    }
    healthy = all(checks.values())
    return JSONResponse(
        status_code=200 if healthy else 503,
        content={
            "status": "ok" if healthy else "degraded",
            "env": get_settings().app_env,
            "checks": checks,
        },
    )
