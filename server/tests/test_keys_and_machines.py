from datetime import datetime, timedelta, timezone

from sqlalchemy import select, text, update

from app.models import ApiKey, Machine


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def session_for(client, role: str, email: str) -> str:
    response = await client.post(
        "/api/auth/register",
        json={"email": email, "password": "motdepasse-solide-1", "name": role, "role": role},
    )
    assert response.status_code == 201
    return response.json()["token"]


HARDWARE_B = {
    "os": "Ubuntu 24.04",
    "gpu_model": "NVIDIA GeForce RTX 4070",
    "vram_mb": 12282,
    "cpu_cores": 12,
    "ram_mb": 32000,
    "disk_free_gb": 400,
}


# ================================================================== clés API
async def test_create_key_shows_secret_once_and_stores_only_its_digest(client, running_app):
    token = await session_for(client, "client", "c@exemple.com")
    response = await client.post("/api/api-keys", json={"name": "Production"}, headers=bearer(token))
    assert response.status_code == 201
    body = response.json()
    key = body["key"]
    assert key.startswith("mk_live_") and body["key_prefix"] == key[:16]

    listed = (await client.get("/api/api-keys", headers=bearer(token))).json()
    assert len(listed) == 1 and "key" not in listed[0]

    async with running_app.state.session_factory() as db:
        stored = (await db.execute(select(ApiKey))).scalar_one()
        assert stored.key_hash != key and key not in stored.key_hash


async def test_key_works_on_v1_and_is_refused_right_after_revocation(client):
    token = await session_for(client, "client", "c@exemple.com")
    created = (
        await client.post("/api/api-keys", json={"name": "Prod"}, headers=bearer(token))
    ).json()
    models = await client.get("/v1/models", headers=bearer(created["key"]))
    assert models.status_code == 200
    assert models.json() == {"object": "list", "data": []}

    revoked = await client.delete(f"/api/api-keys/{created['id']}", headers=bearer(token))
    assert revoked.status_code == 204
    refused = await client.get("/v1/models", headers=bearer(created["key"]))
    assert refused.status_code == 401
    assert refused.json()["error"]["type"] == "authentication_error"

    listed = (await client.get("/api/api-keys", headers=bearer(token))).json()
    assert listed[0]["revoked_at"] is not None


async def test_key_use_updates_last_used_at(client):
    token = await session_for(client, "client", "c@exemple.com")
    created = (
        await client.post("/api/api-keys", json={"name": "Prod"}, headers=bearer(token))
    ).json()
    assert created["last_used_at"] is None
    await client.get("/v1/models", headers=bearer(created["key"]))
    listed = (await client.get("/api/api-keys", headers=bearer(token))).json()
    assert listed[0]["last_used_at"] is not None


async def test_v1_refuses_session_tokens_and_invented_keys(client):
    token = await session_for(client, "client", "c@exemple.com")
    for headers in ({}, bearer(token), bearer("mk_live_inventee"), bearer("ma_jeton")):
        assert (await client.get("/v1/models", headers=headers)).status_code == 401


async def test_cannot_revoke_or_see_another_clients_key(client):
    alice = await session_for(client, "client", "alice@exemple.com")
    bob = await session_for(client, "client", "bob@exemple.com")
    created = (
        await client.post("/api/api-keys", json={"name": "Alice"}, headers=bearer(alice))
    ).json()
    assert (await client.delete(f"/api/api-keys/{created['id']}", headers=bearer(bob))).status_code == 404
    assert (await client.get("/api/api-keys", headers=bearer(bob))).json() == []
    assert (await client.get("/v1/models", headers=bearer(created["key"]))).status_code == 200


async def test_suspended_client_key_is_refused(client, running_app):
    token = await session_for(client, "client", "c@exemple.com")
    created = (
        await client.post("/api/api-keys", json={"name": "Prod"}, headers=bearer(token))
    ).json()
    async with running_app.state.engine.begin() as connection:
        await connection.execute(text("UPDATE users SET status = 'suspended'"))
    assert (await client.get("/v1/models", headers=bearer(created["key"]))).status_code == 403


async def test_providers_cannot_create_api_keys(client):
    provider = await session_for(client, "provider", "p@exemple.com")
    response = await client.post("/api/api-keys", json={"name": "X"}, headers=bearer(provider))
    assert response.status_code == 403


async def test_active_key_limit(client):
    token = await session_for(client, "client", "c@exemple.com")
    for i in range(20):
        r = await client.post("/api/api-keys", json={"name": f"k{i}"}, headers=bearer(token))
        assert r.status_code == 201
    over = await client.post("/api/api-keys", json={"name": "trop"}, headers=bearer(token))
    assert over.status_code == 409 and over.json()["error"]["code"] == "too_many_keys"


# ================================================================== machines
async def declare(client, provider_token: str, name: str = "Salle 1, poste 3") -> dict:
    response = await client.post("/api/machines", json={"name": name}, headers=bearer(provider_token))
    assert response.status_code == 201
    return response.json()


async def enroll(client, enrollment_token: str, hardware: dict = HARDWARE_B):
    return await client.post(
        "/agent/enroll",
        json={"enrollment_token": enrollment_token, "agent_version": "0.1.0", "hardware": hardware},
    )


async def test_declare_then_enroll_gives_agent_token_and_tier(client):
    provider = await session_for(client, "provider", "p@exemple.com")
    declared = await declare(client, provider)
    assert declared["enrollment_token"].startswith("me_")
    assert declared["machine"]["status"] == "pending"
    assert declared["machine"]["hardware"] is None

    response = await enroll(client, declared["enrollment_token"])
    assert response.status_code == 201
    body = response.json()
    assert body["agent_token"].startswith("ma_")
    assert body["tier"] == "B" and body["eligible"] is True
    assert body["websocket_url"].startswith("ws://") and body["websocket_url"].endswith("/agent/ws")

    me = await client.get("/agent/me", headers=bearer(body["agent_token"]))
    assert me.status_code == 200
    assert me.json()["hardware"]["gpu_model"] == "NVIDIA GeForce RTX 4070"

    listed = (await client.get("/api/machines", headers=bearer(provider))).json()
    assert listed[0]["tier"] == "B" and listed[0]["status"] == "offline"


async def test_enrollment_token_is_single_use(client):
    provider = await session_for(client, "provider", "p@exemple.com")
    declared = await declare(client, provider)
    assert (await enroll(client, declared["enrollment_token"])).status_code == 201
    second = await enroll(client, declared["enrollment_token"])
    assert second.status_code == 401
    assert second.json()["error"]["code"] == "invalid_enrollment_token"


async def test_expired_enrollment_token_is_refused(client, running_app):
    provider = await session_for(client, "provider", "p@exemple.com")
    declared = await declare(client, provider)
    async with running_app.state.session_factory() as db:
        await db.execute(
            update(Machine).values(enrollment_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
        )
        await db.commit()
    assert (await enroll(client, declared["enrollment_token"])).status_code == 401


async def test_removing_a_machine_revokes_its_agent_token_immediately(client):
    provider = await session_for(client, "provider", "p@exemple.com")
    declared = await declare(client, provider)
    agent_token = (await enroll(client, declared["enrollment_token"])).json()["agent_token"]
    assert (await client.get("/agent/me", headers=bearer(agent_token))).status_code == 200

    machine_id = declared["machine"]["id"]
    assert (await client.delete(f"/api/machines/{machine_id}", headers=bearer(provider))).status_code == 204
    assert (await client.get("/agent/me", headers=bearer(agent_token))).status_code == 401
    assert (await client.get("/api/machines", headers=bearer(provider))).json() == []
    assert (await client.get(f"/api/machines/{machine_id}", headers=bearer(provider))).status_code == 404


async def test_removing_a_pending_machine_kills_its_enrollment_token(client):
    provider = await session_for(client, "provider", "p@exemple.com")
    declared = await declare(client, provider)
    await client.delete(f"/api/machines/{declared['machine']['id']}", headers=bearer(provider))
    assert (await enroll(client, declared["enrollment_token"])).status_code == 401


async def test_provider_cannot_see_or_remove_another_providers_machine(client):
    alice = await session_for(client, "provider", "alice@exemple.com")
    bob = await session_for(client, "provider", "bob@exemple.com")
    declared = await declare(client, alice)
    machine_id = declared["machine"]["id"]
    assert (await client.get(f"/api/machines/{machine_id}", headers=bearer(bob))).status_code == 404
    assert (await client.delete(f"/api/machines/{machine_id}", headers=bearer(bob))).status_code == 404
    assert (await client.get("/api/machines", headers=bearer(bob))).json() == []


async def test_clients_cannot_declare_machines(client):
    token = await session_for(client, "client", "c@exemple.com")
    response = await client.post("/api/machines", json={"name": "X"}, headers=bearer(token))
    assert response.status_code == 403


async def test_tiers_and_ineligible_hardware(client):
    provider = await session_for(client, "provider", "p@exemple.com")
    cases = [
        ({**HARDWARE_B, "vram_mb": 24564}, "A", None),
        ({**HARDWARE_B, "vram_mb": 8188}, "C", None),
        ({**HARDWARE_B, "vram_mb": 6144}, None, "8 Go"),
        ({**HARDWARE_B, "vram_mb": 0, "gpu_model": None}, None, "GPU"),
        ({**HARDWARE_B, "ram_mb": 8000}, None, "mémoire vive"),
        ({**HARDWARE_B, "disk_free_gb": 20}, None, "disque"),
    ]
    for i, (hardware, tier, reason) in enumerate(cases):
        declared = await declare(client, provider, f"machine {i}")
        body = (await enroll(client, declared["enrollment_token"], hardware)).json()
        assert body["tier"] == tier, (hardware, body)
        assert body["eligible"] is (reason is None)
        if reason:
            assert reason in body["ineligibility_reason"]


async def test_agent_routes_refuse_other_token_kinds(client):
    token = await session_for(client, "provider", "p@exemple.com")
    for headers in ({}, bearer(token), bearer("ma_invente"), bearer("mk_live_x")):
        assert (await client.get("/agent/me", headers=headers)).status_code == 401


async def test_enroll_rejects_invalid_hardware(client):
    provider = await session_for(client, "provider", "p@exemple.com")
    declared = await declare(client, provider)
    response = await enroll(client, declared["enrollment_token"], {**HARDWARE_B, "cpu_cores": 0})
    assert response.status_code == 422
    # Une requête refusée ne consomme pas le jeton.
    assert (await enroll(client, declared["enrollment_token"])).status_code == 201
