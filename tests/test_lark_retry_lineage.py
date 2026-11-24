"""Retry discovery follows the Service Inbox assignment without rewriting deliveries."""

import asyncio
import base64
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.runs.tables import RunRow, ThreadRow
from a13n_service.tenancy.tables import OrganizationRow, WorkspaceRow
from pydantic import SecretStr
from sqlalchemy import delete, select, text

from a13n_claw.service_app import lark_retry
from a13n_claw.service_app.feishu import LarkSettings
from a13n_claw.service_app.models import Claw, Connector, LarkRetryCard


def test_retry_failure_recovers_its_lark_conversation(monkeypatch):
    url = os.environ.get("CLAW_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires disposable PostgreSQL")
    asyncio.run(exercise_lineage(url, monkeypatch))


async def exercise_lineage(url, monkeypatch):
    storage = Storage(url, pool_size=3)
    org, workspace, claw_id, connector_id, previous_id = (uuid4().hex for _ in range(5))
    thread_id, old_run, new_run = (uuid4().hex for _ in range(3))
    settings = LarkSettings(app_id="app", bot_open_id="bot", senders=("user",), groups=("chat",))
    keys = KeyRing(
        active_key_id="fixture",
        keys={"fixture": SecretStr(base64.b64encode(b"x" * 32).decode())},
    )
    runtime = SimpleNamespace(storage=storage, keys=keys)
    original_transaction = transaction

    class SessionProxy:
        def __init__(self, session):
            self.session = session

        def __getattr__(self, name):
            return getattr(self.session, name)

        async def get(self, model, identity, **kwargs):
            if model is ThreadRow:
                return SimpleNamespace(id=thread_id, last_run_id=new_run, current_run_id=None)
            if model is RunRow:
                return SimpleNamespace(id=new_run, status="failed", principal_id="account")
            return await self.session.get(model, identity, **kwargs)

    @asynccontextmanager
    async def discover_transaction(storage):
        async with original_transaction(storage) as session:
            # A transaction-scoped shadow models the exact Service 0.4.1 Inbox
            # columns used by the join without fabricating a full native Run.
            await session.execute(
                text(
                    "CREATE TEMP TABLE inbox_entries (request_key text, workspace_id text, "
                    "thread_id text, principal_id text, assigned_run_id text) ON COMMIT DROP"
                )
            )
            await session.execute(
                text(
                    "INSERT INTO inbox_entries VALUES (:key, :workspace, :thread, :principal, :run)"
                ),
                {
                    "key": f"claw:retry:{old_run}",
                    "workspace": workspace,
                    "thread": thread_id,
                    "principal": "account",
                    "run": new_run,
                },
            )
            yield SessionProxy(session)

    try:
        async with original_transaction(storage) as session:
            session.add(OrganizationRow(id=org, name="Test"))
            await session.flush()
            session.add(WorkspaceRow(id=workspace, organization_id=org, name="Test"))
            await session.flush()
            session.add(
                Claw(
                    id=claw_id,
                    workspace_id=workspace,
                    name="Test",
                    agent_id="agent",
                    memory_space_id="space",
                    service_account_id="account",
                    main_thread_id=thread_id,
                )
            )
            await session.flush()
            session.add(
                Connector(
                    id=connector_id,
                    claw_id=claw_id,
                    name="Lark",
                    kind="lark",
                    config=settings.model_dump(mode="json"),
                    enabled=True,
                )
            )
            session.add(
                LarkRetryCard(
                    id=previous_id,
                    claw_id=claw_id,
                    connector_id=connector_id,
                    run_id=old_run,
                    chat_id="chat",
                    source_message_id="source",
                    is_group=True,
                    reply_in_thread=True,
                    token={"synthetic": "unused"},
                    state="admitted",
                )
            )
        monkeypatch.setattr(lark_retry, "transaction", discover_transaction)
        await lark_retry.discover(runtime, workspace, claw_id)
        await lark_retry.discover(runtime, workspace, claw_id)
        async with short_session(storage) as session:
            cards = (
                await session.scalars(select(LarkRetryCard).where(LarkRetryCard.claw_id == claw_id))
            ).all()
            assert len(cards) == 2
            current = next(card for card in cards if card.run_id == new_run)
            assert (current.connector_id, current.chat_id, current.source_message_id) == (
                connector_id,
                "chat",
                "source",
            )
            assert current.is_group and current.reply_in_thread
    finally:
        async with original_transaction(storage) as session:
            await session.execute(delete(Claw).where(Claw.id == claw_id))
            await session.execute(delete(WorkspaceRow).where(WorkspaceRow.id == workspace))
            await session.execute(delete(OrganizationRow).where(OrganizationRow.id == org))
        await storage.close()
