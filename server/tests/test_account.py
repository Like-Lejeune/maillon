from datetime import datetime, timezone

from sqlalchemy import text


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def register(client, email: str, role: str = "client") -> tuple[str, str]:
    body = (
        await client.post(
            "/api/auth/register",
            json={"email": email, "password": "motdepasse-solide-1", "name": "X", "role": role},
        )
    ).json()
    return body["token"], body["user"]["id"]


async def test_balance_starts_at_zero(client):
    token, _ = await register(client, "c@exemple.com")
    assert (await client.get("/api/balance", headers=bearer(token))).json() == {"available": 0, "pending": 0}


async def test_balance_reflects_the_stored_amounts(client, running_app):
    token, user_id = await register(client, "c@exemple.com")
    async with running_app.state.engine.begin() as c:
        await c.execute(text("UPDATE balances SET available = 12400000 WHERE user_id = :u"), {"u": user_id})
    assert (await client.get("/api/balance", headers=bearer(token))).json()["available"] == 12_400_000


async def insert_ledger(running_app, user_id: str, count: int):
    async with running_app.state.engine.begin() as c:
        for i in range(count):
            await c.execute(
                text(
                    "INSERT INTO ledger_entries (account, user_id, type, amount, balance_after, note) "
                    "VALUES ('user', :u, 'topup', :a, :b, :n)"
                ),
                {"u": user_id, "a": 1_000_000, "b": (i + 1) * 1_000_000, "n": f"recharge {i + 1}"},
            )


async def test_ledger_is_newest_first_and_paginated(client, running_app):
    token, user_id = await register(client, "c@exemple.com")
    await insert_ledger(running_app, user_id, 5)
    first = (await client.get("/api/ledger?limit=2", headers=bearer(token))).json()
    assert [e["note"] for e in first["data"]] == ["recharge 5", "recharge 4"]
    assert first["next_cursor"]
    second = (
        await client.get(f"/api/ledger?limit=2&cursor={first['next_cursor']}", headers=bearer(token))
    ).json()
    assert [e["note"] for e in second["data"]] == ["recharge 3", "recharge 2"]
    last = (
        await client.get(f"/api/ledger?limit=2&cursor={second['next_cursor']}", headers=bearer(token))
    ).json()
    assert [e["note"] for e in last["data"]] == ["recharge 1"] and last["next_cursor"] is None


async def test_ledger_only_shows_own_entries(client, running_app):
    alice, alice_id = await register(client, "alice@exemple.com")
    bob, _ = await register(client, "bob@exemple.com")
    await insert_ledger(running_app, alice_id, 2)
    assert (await client.get("/api/ledger", headers=bearer(bob))).json()["data"] == []
    assert len((await client.get("/api/ledger", headers=bearer(alice))).json()["data"]) == 2


async def test_ledger_rejects_a_bad_cursor(client):
    token, _ = await register(client, "c@exemple.com")
    assert (await client.get("/api/ledger?cursor=abc", headers=bearer(token))).status_code == 400


async def test_usage_groups_succeeded_jobs_by_day_and_model(client, running_app):
    token, user_id = await register(client, "c@exemple.com")
    other, other_id = await register(client, "autre@exemple.com")
    async with running_app.state.engine.begin() as c:
        model_id = (
            await c.execute(
                text(
                    "INSERT INTO models (slug, display_name, min_vram_mb, context_length, file_url, "
                    "file_sha256, file_size_bytes, price_input_per_mtok, price_output_per_mtok) "
                    "VALUES ('modele-test', 'Test', 6000, 8192, 'x', 'x', 1, 100, 200) RETURNING id"
                )
            )
        ).scalar_one()
        jobs = [
            (user_id, "succeeded", datetime(2026, 11, 17, 10, tzinfo=timezone.utc), 100, 50, 30),
            (user_id, "succeeded", datetime(2026, 11, 17, 23, 30, tzinfo=timezone.utc), 10, 5, 3),
            (user_id, "succeeded", datetime(2026, 11, 18, 1, tzinfo=timezone.utc), 1, 1, 1),
            (user_id, "failed", datetime(2026, 11, 17, 11, tzinfo=timezone.utc), None, None, None),
            (other_id, "succeeded", datetime(2026, 11, 17, 12, tzinfo=timezone.utc), 999, 999, 999),
        ]
        for client_id, status, created, pt, ct, cost in jobs:
            await c.execute(
                text(
                    "INSERT INTO jobs (client_id, model_id, status, request, created_at, "
                    "prompt_tokens, completion_tokens, cost) "
                    "VALUES (:c, :m, CAST(:s AS job_status), '{}', :t, :pt, :ct, :cost)"
                ),
                {"c": client_id, "m": model_id, "s": status, "t": created, "pt": pt, "ct": ct, "cost": cost},
            )
    rows = (await client.get("/api/usage?from=2026-11-17&to=2026-11-18", headers=bearer(token))).json()
    assert rows == [
        {"date": "2026-11-17", "model": "modele-test", "requests": 2, "prompt_tokens": 110,
         "completion_tokens": 55, "cost": 33},
        {"date": "2026-11-18", "model": "modele-test", "requests": 1, "prompt_tokens": 1,
         "completion_tokens": 1, "cost": 1},
    ]
    only_17 = (await client.get("/api/usage?from=2026-11-17&to=2026-11-17", headers=bearer(token))).json()
    assert len(only_17) == 1


async def test_usage_validates_the_period_and_the_role(client):
    token, _ = await register(client, "c@exemple.com")
    assert (await client.get("/api/usage?from=2026-11-18&to=2026-11-17", headers=bearer(token))).status_code == 400
    assert (await client.get("/api/usage?from=2025-01-01&to=2026-11-17", headers=bearer(token))).status_code == 400
    assert (await client.get("/api/usage?to=2026-11-17", headers=bearer(token))).status_code == 422
    provider, _ = await register(client, "p@exemple.com", "provider")
    assert (await client.get("/api/usage?from=2026-11-17&to=2026-11-17", headers=bearer(provider))).status_code == 403
    assert (await client.get("/api/balance", headers=bearer(provider))).status_code == 200
