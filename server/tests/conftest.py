"""Tests d'intégration : ils tournent contre un vrai PostgreSQL et un vrai Redis.

Variables attendues (une base et un Redis réservés aux tests, vidés à chaque test) :
DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, REDIS_HOST, REDIS_PORT, REDIS_PASSWORD.
Le schéma doit être appliqué avant : `alembic upgrade head`.
"""

import pytest
import pytest_asyncio
from fastapi import Depends
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.config import get_settings
from app.deps import require_role
from app.main import app
from app.models import User


# Route réservée aux tests, pour vérifier le contrôle des rôles.
@app.get("/api/_test/admin-only", include_in_schema=False)
async def _admin_only(user: User = Depends(require_role("admin"))) -> dict:
    return {"ok": True}


@pytest_asyncio.fixture(scope="session")
async def running_app():
    settings = get_settings()
    if settings.app_env != "test":
        pytest.exit("Refus de lancer les tests : APP_ENV doit valoir « test ».")
    async with app.router.lifespan_context(app):
        yield app


@pytest_asyncio.fixture(autouse=True)
async def clean_state(running_app):
    async with running_app.state.engine.begin() as connection:
        await connection.execute(text("TRUNCATE users, api_keys, machines, models RESTART IDENTITY CASCADE"))
    await running_app.state.redis.flushdb()
    yield


@pytest_asyncio.fixture
async def client(running_app):
    transport = ASGITransport(app=running_app)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        yield http


@pytest.fixture
def account() -> dict:
    return {
        "email": "awa@exemple.com",
        "password": "motdepasse-solide-1",
        "name": "Awa",
        "role": "client",
    }


@pytest_asyncio.fixture(scope="session")
async def live_server(running_app):
    """Vrai serveur HTTP/WebSocket sur un port libre, qui partage l'état de running_app."""
    import asyncio
    import socket

    import uvicorn

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(running_app, host="127.0.0.1", port=port, lifespan="off", log_level="warning")
    )
    task = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.05)
    yield f"127.0.0.1:{port}"
    server.should_exit = True
    await task
