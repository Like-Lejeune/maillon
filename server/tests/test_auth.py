from sqlalchemy import select, text

from app.models import Balance, User
from app.security import hash_password


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def register(client, account):
    return await client.post("/api/auth/register", json=account)


# ---------------------------------------------------------------- inscription
async def test_register_creates_account_session_and_balance(client, account, running_app):
    response = await register(client, account)
    assert response.status_code == 201
    body = response.json()
    assert body["token"].startswith("ms_")
    assert body["user"]["email"] == account["email"]
    assert body["user"]["role"] == "client"
    assert body["user"]["status"] == "active"
    assert "password" not in str(body) and "password_hash" not in str(body)

    async with running_app.state.session_factory() as db:
        user = (await db.execute(select(User))).scalar_one()
        assert user.password_hash != account["password"]
        assert user.password_hash.startswith("$argon2id$")
        balance = await db.get(Balance, user.id)
        assert balance.available == 0 and balance.pending == 0


async def test_register_rejects_duplicate_email_whatever_the_case(client, account):
    assert (await register(client, account)).status_code == 201
    response = await register(client, {**account, "email": "AWA@Exemple.com"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "email_taken"


async def test_register_rejects_short_password(client, account):
    response = await register(client, {**account, "password": "court"})
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["type"] == "invalid_request_error"
    assert error["param"] == "password"


async def test_register_cannot_create_an_admin(client, account):
    response = await register(client, {**account, "role": "admin"})
    assert response.status_code == 422
    assert response.json()["error"]["param"] == "role"


async def test_register_rejects_invalid_email(client, account):
    response = await register(client, {**account, "email": "pas-un-email"})
    assert response.status_code == 422


# ------------------------------------------------------------------ connexion
async def test_login_returns_a_working_session(client, account):
    await register(client, account)
    response = await client.post(
        "/api/auth/login", json={"email": account["email"], "password": account["password"]}
    )
    assert response.status_code == 200
    token = response.json()["token"]
    me = await client.get("/api/me", headers=bearer(token))
    assert me.status_code == 200
    assert me.json()["email"] == account["email"]


async def test_login_is_case_insensitive_on_email(client, account):
    await register(client, account)
    response = await client.post(
        "/api/auth/login", json={"email": "AWA@EXEMPLE.COM", "password": account["password"]}
    )
    assert response.status_code == 200


async def test_login_gives_same_answer_for_wrong_password_and_unknown_email(client, account):
    await register(client, account)
    wrong = await client.post(
        "/api/auth/login", json={"email": account["email"], "password": "mauvais-mot-de-passe"}
    )
    unknown = await client.post(
        "/api/auth/login", json={"email": "inconnu@exemple.com", "password": "mauvais-mot-de-passe"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


async def test_login_locks_after_five_failures_even_with_right_password(client, account):
    await register(client, account)
    for _ in range(5):
        response = await client.post(
            "/api/auth/login", json={"email": account["email"], "password": "mauvais-mot-de-passe"}
        )
        assert response.status_code == 401
    locked = await client.post(
        "/api/auth/login", json={"email": account["email"], "password": account["password"]}
    )
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0
    assert locked.json()["error"]["code"] == "login_locked"


async def test_successful_login_resets_the_failure_count(client, account):
    await register(client, account)
    good = {"email": account["email"], "password": account["password"]}
    bad = {"email": account["email"], "password": "mauvais-mot-de-passe"}
    for _ in range(4):
        await client.post("/api/auth/login", json=bad)
    assert (await client.post("/api/auth/login", json=good)).status_code == 200
    for _ in range(4):
        await client.post("/api/auth/login", json=bad)
    assert (await client.post("/api/auth/login", json=good)).status_code == 200


async def test_suspended_account_cannot_login_and_loses_access(client, account, running_app):
    token = (await register(client, account)).json()["token"]
    async with running_app.state.engine.begin() as connection:
        await connection.execute(text("UPDATE users SET status = 'suspended'"))
    assert (await client.get("/api/me", headers=bearer(token))).status_code == 403
    response = await client.post(
        "/api/auth/login", json={"email": account["email"], "password": account["password"]}
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "account_suspended"


# ------------------------------------------------------- session et déconnexion
async def test_me_requires_a_session(client):
    for headers in ({}, bearer("ms_jeton-invente"), bearer("mk_live_cle-api"), {"Authorization": "Basic abc"}):
        response = await client.get("/api/me", headers=headers)
        assert response.status_code == 401
        assert response.json()["error"]["type"] == "authentication_error"


async def test_logout_invalidates_the_token_immediately(client, account):
    token = (await register(client, account)).json()["token"]
    assert (await client.get("/api/me", headers=bearer(token))).status_code == 200
    assert (await client.post("/api/auth/logout", headers=bearer(token))).status_code == 204
    assert (await client.get("/api/me", headers=bearer(token))).status_code == 401


async def test_logout_only_closes_its_own_session(client, account):
    first = (await register(client, account)).json()["token"]
    second = (
        await client.post(
            "/api/auth/login", json={"email": account["email"], "password": account["password"]}
        )
    ).json()["token"]
    await client.post("/api/auth/logout", headers=bearer(first))
    assert (await client.get("/api/me", headers=bearer(second))).status_code == 200


async def test_session_token_is_not_stored_in_clear(client, account, running_app):
    token = (await register(client, account)).json()["token"]
    keys = [key.decode() for key in await running_app.state.redis.keys("*")]
    values = [
        (await running_app.state.redis.get(key)) for key in keys if key.startswith("session:")
    ]
    assert keys and all(token not in key for key in keys)
    assert all(token.encode() != value for value in values)


# ------------------------------------------------------------------------ rôles
async def test_role_guard_refuses_other_roles_and_accepts_admin(client, account, running_app):
    token = (await register(client, account)).json()["token"]
    refused = await client.get("/api/_test/admin-only", headers=bearer(token))
    assert refused.status_code == 403
    assert refused.json()["error"]["type"] == "permission_error"

    async with running_app.state.session_factory() as db:
        db.add(
            User(
                email="admin@exemple.com",
                name="Admin",
                role="admin",
                password_hash=hash_password("motdepasse-admin-1"),
            )
        )
        await db.commit()
    admin_token = (
        await client.post(
            "/api/auth/login", json={"email": "admin@exemple.com", "password": "motdepasse-admin-1"}
        )
    ).json()["token"]
    assert (await client.get("/api/_test/admin-only", headers=bearer(admin_token))).status_code == 200


async def test_unknown_route_uses_the_contract_error_shape(client):
    response = await client.get("/api/inexistant")
    assert response.status_code == 404
    assert response.json()["error"]["type"] == "not_found_error"
