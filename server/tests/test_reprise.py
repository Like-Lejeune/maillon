"""Reprise automatique : une tâche interrompue est réattribuée sans intervention.

Les faux agents et les utilitaires viennent de test_dispatch.
"""

import asyncio
from contextlib import AsyncExitStack

from sqlalchemy import text

from tests.test_dispatch import add_model, agent_token, answer, client_key, complete, fake_agent


async def attempts(running_app) -> list[tuple[int, str]]:
    async with running_app.state.engine.connect() as c:
        rows = await c.execute(text("SELECT attempt_no, status::text FROM job_attempts ORDER BY attempt_no"))
        return [tuple(r) for r in rows]


async def job(running_app):
    async with running_app.state.engine.connect() as c:
        return (await c.execute(text("SELECT * FROM jobs"))).mappings().one()


# Comportements d'agents défaillants
async def crashes(agent, job_data):
    await agent.send("job.accepted", {"job_id": job_data["job_id"]})
    await agent.send("job.error", {"job_id": job_data["job_id"], "code": "runtime_error", "message": "plantage", "retryable": True})


async def drops(agent, job_data):
    await agent.send("job.accepted", {"job_id": job_data["job_id"]})
    await asyncio.sleep(0.1)
    await agent.ws.close()


async def mute(agent, job_data):
    await asyncio.sleep(10)


async def busy(agent, job_data):
    await agent.send("job.error", {"job_id": job_data["job_id"], "code": "busy", "message": "occupée", "retryable": True})


async def rejects_request(agent, job_data):
    await agent.send("job.accepted", {"job_id": job_data["job_id"]})
    await agent.send("job.error", {"job_id": job_data["job_id"], "code": "invalid_request", "message": "contexte trop long"})


async def slow(agent, job_data):
    await agent.send("job.accepted", {"job_id": job_data["job_id"]})
    for _ in range(20):
        await agent.send("heartbeat", {"busy": True, "job_id": job_data["job_id"]})
        await asyncio.sleep(0.5)


async def setup(client, running_app):
    await add_model(running_app)
    return await client_key(client)


async def first_fails_then_second_serves(client, live_server, running_app, behaviour):
    """La machine défaillante est seule au départ, la saine arrive ensuite : le premier essai
    tombe forcément sur la défaillante, le second sur la saine."""
    key = await setup(client, running_app)
    bad_token, good_token = await agent_token(client), await agent_token(client)
    async with fake_agent(live_server, bad_token, behaviour) as bad:
        request = asyncio.create_task(complete(client, key, "Bonjour"))
        while not bad.assigned:
            await asyncio.sleep(0.02)
        async with fake_agent(live_server, good_token) as good:
            response = await request
    return response, bad, good


# ===================================================================== tests
async def test_machine_lost_mid_job_is_reassigned(client, live_server, running_app):
    """Critère du planning : une tâche interrompue est réattribuée sans intervention."""
    response, bad, good = await first_fails_then_second_serves(client, live_server, running_app, drops)
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "Écho : Bonjour"
    assert len(bad.assigned) == 1 and len(good.assigned) == 1
    assert await attempts(running_app) == [(1, "machine_lost"), (2, "succeeded")]
    row = await job(running_app)
    assert row["status"] == "succeeded" and row["attempts"] == 2 and row["error_code"] is None


async def test_runtime_error_is_reassigned(client, live_server, running_app):
    response, _, _ = await first_fails_then_second_serves(client, live_server, running_app, crashes)
    assert response.status_code == 200
    assert await attempts(running_app) == [(1, "failed"), (2, "succeeded")]


async def test_unresponsive_machine_is_reassigned_and_told_to_cancel(client, live_server, running_app):
    response, bad, _ = await first_fails_then_second_serves(client, live_server, running_app, mute)
    assert response.status_code == 200
    assert await attempts(running_app) == [(1, "timed_out"), (2, "succeeded")]
    assert any(m["type"] == "job.cancel" for m in bad.received)


async def test_busy_machine_is_reassigned(client, live_server, running_app):
    response, _, _ = await first_fails_then_second_serves(client, live_server, running_app, busy)
    assert response.status_code == 200


async def test_invalid_request_is_not_reassigned(client, live_server, running_app):
    response, bad, good = await first_fails_then_second_serves(client, live_server, running_app, rejects_request)
    assert response.status_code == 400
    assert response.json()["error"]["type"] == "invalid_request_error"
    assert "contexte trop long" in response.json()["error"]["message"]
    assert good.assigned == []
    assert (await job(running_app))["status"] == "failed"


async def test_timeout_is_not_reassigned(client, live_server, running_app):
    response, _, good = await first_fails_then_second_serves(client, live_server, running_app, slow)
    assert response.status_code == 504
    assert good.assigned == []
    assert (await job(running_app))["status"] == "expired"


async def test_three_attempts_at_most_on_three_different_machines(client, live_server, running_app):
    key = await setup(client, running_app)
    async with AsyncExitStack() as stack:
        agents = [await stack.enter_async_context(fake_agent(live_server, await agent_token(client), crashes)) for _ in range(4)]
        response = await complete(client, key)
    assert response.status_code == 502 and response.json()["error"]["code"] == "runtime_error"
    used = [len(a.assigned) for a in agents]
    assert sum(used) == 3 and max(used) == 1  # trois machines distinctes, la quatrième épargnée
    assert [s for _, s in await attempts(running_app)] == ["failed", "failed", "failed"]
    row = await job(running_app)
    assert row["status"] == "failed" and row["attempts"] == 3 and row["error_code"] == "runtime_error"


async def test_a_failed_machine_is_not_retried_for_the_same_job(client, live_server, running_app):
    """Seule machine du réseau et en échec : pas de second essai sur elle, le client reçoit l'erreur."""
    key = await setup(client, running_app)
    async with fake_agent(live_server, await agent_token(client), crashes) as agent:
        response = await complete(client, key)
    assert response.status_code == 502
    assert len(agent.assigned) == 1


async def test_late_result_from_the_first_machine_is_ignored(client, live_server, running_app):
    async def late(agent, job_data):
        await asyncio.sleep(1.5)  # passe le délai d'accusé de réception (1 s en test)
        await answer(agent, {**job_data, "request": {"messages": [{"content": "trop tard"}]}})

    response, bad, good = await first_fails_then_second_serves(client, live_server, running_app, late)
    await asyncio.sleep(0.5)
    assert response.json()["choices"][0]["message"]["content"] == "Écho : Bonjour"
    assert (await job(running_app))["status"] == "succeeded"
