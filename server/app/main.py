"""Point d'entrée du serveur Maillon.

Les routes du contrat (docs/openapi.yaml) s'ajoutent tâche par tâche.
"""

import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from app.agent_hub import AgentHub
from app.config import get_settings
from app.dispatch import Dispatcher
from app.errors import install_error_handlers
from app.routers import account, agent, agent_ws, api_keys, auth, inference, machines


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    app.state.engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    app.state.session_factory = async_sessionmaker(app.state.engine, expire_on_commit=False)
    app.state.redis = Redis.from_url(settings.redis_url, socket_connect_timeout=2)
    app.state.agent_hub = AgentHub(app.state.session_factory)
    app.state.dispatcher = Dispatcher(app.state.session_factory, app.state.agent_hub, settings)
    sweeper = asyncio.create_task(
        app.state.agent_hub.run_sweeper(
            settings.presence_sweep_seconds, settings.heartbeat_timeout_seconds
        )
    )
    yield
    sweeper.cancel()
    with suppress(asyncio.CancelledError):
        await sweeper
    await app.state.agent_hub.close_all()
    await app.state.redis.aclose()
    await app.state.engine.dispose()


app = FastAPI(
    title="API Maillon",
    version="0.4.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
install_error_handlers(app)
app.include_router(auth.router)
app.include_router(api_keys.router)
app.include_router(account.router)
app.include_router(machines.router)
app.include_router(agent.router)
app.include_router(agent_ws.router)
app.include_router(inference.router)


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
