"""Retry card scheduling rotates past temporarily undeliverable cards."""

import asyncio
import os
from types import SimpleNamespace
from uuid import uuid4

import pytest
from a13n_service.infra.db import Storage, transaction
from a13n_service.tenancy.tables import OrganizationRow, WorkspaceRow
from redis.asyncio import Redis
from sqlalchemy import delete

from a13n_claw.service_app import lark_retry, supervisor
from a13n_claw.service_app.models import Claw, LarkRetryCard


def test_retry_queue_rotates_past_failed_and_disabled_cards(monkeypatch):
    url = os.environ.get("CLAW_TEST_DATABASE_URL")
    redis_url = os.environ.get("CLAW_TEST_REDIS_URL") or os.environ.get("A13N_REDIS__URL")
    if not url or not redis_url:
        pytest.skip("Requires disposable PostgreSQL and Redis")
    seen = []

    async def discover(*args):
        pass

    async def deliver(_runtime, _workspace, _claw, card_id):
        seen.append(card_id)
        # Simulate both a provider failure and an ineligible connector.
        if len(seen) == 1:
            raise ValueError("provider unavailable")

    monkeypatch.setattr(lark_retry, "discover", discover)
    monkeypatch.setattr(lark_retry, "deliver", deliver)

    async def run():
        storage = Storage(url, pool_size=4)
        redis = Redis.from_url(redis_url)
        runtime = SimpleNamespace(storage=storage, redis=redis)
        org, workspace, claw = (uuid4().hex for _ in range(3))
        ids = [f"lrc_{uuid4().hex}" for _ in range(5)]
        try:
            async with transaction(storage) as session:
                session.add(OrganizationRow(id=org, name="Test"))
                await session.flush()
                session.add(WorkspaceRow(id=workspace, organization_id=org, name="Test"))
                await session.flush()
                session.add(
                    Claw(
                        id=claw,
                        workspace_id=workspace,
                        name="Test",
                        agent_id="agent",
                        memory_space_id="space",
                        service_account_id="account",
                    )
                )
                await session.flush()
                session.add_all(
                    LarkRetryCard(
                        id=card_id,
                        claw_id=claw,
                        connector_id=f"connector-{index}",
                        run_id="run",
                        chat_id="chat",
                        source_message_id="message",
                        token={"synthetic": "not-a-secret"},
                    )
                    for index, card_id in enumerate(ids)
                )
            await lark_retry.reconcile(runtime, workspace, claw)
            assert len(seen) == 4
            await lark_retry.reconcile(runtime, workspace, claw)
            assert set(seen) == set(ids)
        finally:
            async with transaction(storage) as session:
                await session.execute(delete(Claw).where(Claw.id == claw))
                await session.execute(delete(WorkspaceRow).where(WorkspaceRow.id == workspace))
                await session.execute(delete(OrganizationRow).where(OrganizationRow.id == org))
            await redis.aclose()
            await storage.close()

    asyncio.run(run())


def test_provider_reconciliation_is_background_bounded_and_deduplicated(monkeypatch):
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def reconcile(_runtime, workspace, claw):
            calls.append((workspace, claw))
            entered.set()
            await release.wait()

        monkeypatch.setattr(lark_retry, "reconcile", reconcile)
        listeners = supervisor.Listeners.__new__(supervisor.Listeners)
        listeners.runtime = object()
        listeners._closing = False
        listeners.card_tasks = {}
        listeners._card_slots = asyncio.Semaphore(1)
        listeners.queue_cards("ws", "one")
        listeners.queue_cards("ws", "one")
        listeners.queue_cards("ws", "two")
        await entered.wait()
        assert calls == [("ws", "one")]
        assert len(listeners.card_tasks) == 2
        tasks = list(listeners.card_tasks.values())
        release.set()
        await asyncio.gather(*tasks)
        assert calls == [("ws", "one"), ("ws", "two")]
        assert not listeners.card_tasks

    asyncio.run(run())
