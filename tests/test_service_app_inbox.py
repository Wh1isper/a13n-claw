"""PostgreSQL checks for the installed App schema and concurrent admission."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.tenancy.tables import OrganizationRow, WorkspaceRow
from sqlalchemy import delete, func, select

from a13n_claw.service_app import inbox
from a13n_claw.service_app.models import Claw, Delivery, InboxItem


def test_admission_and_retention(monkeypatch):
    url = os.environ.get("CLAW_TEST_DATABASE_URL")
    if url is None:
        pytest.skip(
            "Set CLAW_TEST_DATABASE_URL to a disposable migrated Service PostgreSQL database"
        )
    monkeypatch.setattr(inbox, "DELIVERY_LIMIT", 3)
    asyncio.run(exercise_inbox(url))


async def exercise_inbox(url: str) -> None:
    storage = Storage(url, pool_size=4)
    org_id, workspace_id, claw_id = (uuid4().hex for _ in range(3))
    try:
        async with transaction(storage) as session:
            session.add(OrganizationRow(id=org_id, name="Claw test"))
            await session.flush()
            session.add(WorkspaceRow(id=workspace_id, organization_id=org_id, name="Claw test"))
            await session.flush()
            session.add(
                Claw(
                    id=claw_id,
                    workspace_id=workspace_id,
                    name="Test Claw",
                    agent_id="agent_test",
                    environment_id="env_test",
                    memory_space_id="space_test",
                    service_account_id="account_test",
                    unread_limit=1,
                    read_limit=1,
                )
            )

        def offer(key):
            return inbox.Offer("user", "test", key, {}, f"full {key}", f"preview {key}")

        async def deliver(key):
            return await inbox.deliver(storage, workspace_id, claw_id, offer(key))

        first, second = await asyncio.gather(deliver("first"), deliver("second"))
        assert sorted((first.status, second.status)) == ["accepted", "full"]
        accepted, key = (first, "first") if first.status == "accepted" else (second, "second")
        assert (await deliver(key)).status == "duplicate"
        enriched = inbox.Offer(
            "user",
            "test",
            key,
            {"previous_messages_snapshot": [{"content": "history"}]},
            f"full {key}",
            f"preview {key}",
            ("previous_messages_snapshot",),
        )
        assert (await inbox.deliver(storage, workspace_id, claw_id, enriched)).status == "duplicate"
        changed = inbox.Offer(
            "user",
            "test",
            key,
            {"sender": "different", "previous_messages_snapshot": []},
            f"full {key}",
            f"preview {key}",
            ("previous_messages_snapshot",),
        )
        with pytest.raises(ServiceError, match="different message"):
            await inbox.deliver(storage, workspace_id, claw_id, changed)
        with pytest.raises(inbox.ClawNotFound):
            await inbox.pop(storage, "another_workspace", claw_id, accepted.item_id)
        assert await inbox.unread_previews(storage, "another_workspace", claw_id) == []
        (preview,) = await inbox.unread_previews(storage, workspace_id, claw_id)
        assert preview["preview"] == f"preview {key}"
        assert "content" not in preview
        opened = await inbox.pop(storage, workspace_id, claw_id, accepted.item_id)
        assert opened is not None and opened.content == f"full {key}" and opened.read_at
        reopened = await inbox.pop(storage, workspace_id, claw_id, accepted.item_id)
        assert reopened is not None and reopened.read_at == opened.read_at

        next_item = await deliver("next")
        assert next_item.status == "accepted"
        assert await inbox.pop(storage, workspace_id, claw_id, next_item.item_id) is not None
        assert await inbox.pop(storage, workspace_id, claw_id, accepted.item_id) is None
        assert (await deliver("additional")).status == "accepted"
        async with short_session(storage) as session:
            assert (
                await session.scalar(
                    select(func.count()).select_from(Delivery).where(Delivery.claw_id == claw_id)
                )
                == 3
            )
        async with transaction(storage) as session:
            old = datetime.now(UTC) - timedelta(days=8)
            retained = await session.get(InboxItem, next_item.item_id)
            assert retained is not None
            retained.read_at = old
        await inbox.cleanup(storage, workspace_id, claw_id)
        assert await inbox.pop(storage, workspace_id, claw_id, next_item.item_id) is None
    finally:
        async with transaction(storage) as session:
            await session.execute(delete(Claw).where(Claw.id == claw_id))
            await session.execute(delete(WorkspaceRow).where(WorkspaceRow.id == workspace_id))
            await session.execute(delete(OrganizationRow).where(OrganizationRow.id == org_id))
        await storage.close()
