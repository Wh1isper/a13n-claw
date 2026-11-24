"""Real PostgreSQL row locks fence concurrent retry callbacks."""

import asyncio
import base64
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.resources.agents.tables import AgentRow
from a13n_service.runs.tables import RunRow
from a13n_service.tenancy.tables import OrganizationRow, WorkspaceRow
from pydantic import SecretStr
from sqlalchemy import delete

from a13n_claw.service_app import connectors, lark_retry
from a13n_claw.service_app.feishu import LarkSettings
from a13n_claw.service_app.models import Claw, Connector, LarkRetryCard


def test_concurrent_retry_callbacks_share_one_postgres_admission(monkeypatch):
    url = os.environ.get("CLAW_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires disposable PostgreSQL")
    asyncio.run(exercise_concurrent_callbacks(url, monkeypatch))


async def exercise_concurrent_callbacks(url, monkeypatch):
    storage = Storage(url, pool_size=4)
    org, workspace, claw_id, connector_id, card_id = (uuid4().hex for _ in range(5))
    agent_id = "agt_" + uuid4().hex[:24]
    run_id = "run_" + uuid4().hex[:24]
    thread_id = "thr_" + uuid4().hex[:24]
    token = "synthetic-card-token"
    keys = KeyRing(
        active_key_id="fixture",
        keys={"fixture": SecretStr(base64.b64encode(b"x" * 32).decode())},
    )
    settings = LarkSettings(
        app_id="app", bot_open_id="bot", senders=("allowed",), groups=("group",)
    )
    intake = connectors.Intake(org, workspace, claw_id, connector_id, settings, "credential")
    runtime = SimpleNamespace(storage=storage, keys=keys)
    thread = SimpleNamespace(id=thread_id, current_run_id=None, last_run_id=run_id)
    failed = SimpleNamespace(id=run_id, status="failed", principal_id="account")
    agent = SimpleNamespace(id=agent_id)
    calls = []
    original_transaction = transaction

    class SessionProxy:
        def __init__(self, session):
            self.session = session

        def __getattr__(self, name):
            return getattr(self.session, name)

        async def get(self, model, identity, **kwargs):
            if model is RunRow:
                return failed
            return await self.session.get(model, identity, **kwargs)

        async def scalar(self, query, **kwargs):
            if query.column_descriptions[0]["entity"] is AgentRow:
                return agent
            return await self.session.scalar(query, **kwargs)

    @asynccontextmanager
    async def retry_transaction(storage):
        async with original_transaction(storage) as session:
            yield SessionProxy(session)

    async def enabled(*_args, **_kwargs):
        return True

    async def prepare(*_args, **_kwargs):
        return object()

    async def get_thread(*_args, **_kwargs):
        return thread

    async def options(*_args, **_kwargs):
        return {}

    async def submit(_session, *_args, **kwargs):
        calls.append(kwargs["request_key"])
        await asyncio.sleep(0.05)
        return SimpleNamespace(
            entry=SimpleNamespace(status="assigned", assigned_run_id="new-run")
        ), True

    monkeypatch.setattr(lark_retry, "transaction", retry_transaction)
    monkeypatch.setattr(lark_retry, "get_thread", get_thread)
    monkeypatch.setattr(lark_retry.app_threads, "prepare_admission", prepare)
    monkeypatch.setattr(lark_retry.app_threads, "submit_message_in_transaction", submit)
    monkeypatch.setattr(lark_retry.main_thread, "selected_revision", lambda *_: "rev_" + "b" * 24)
    monkeypatch.setattr(lark_retry.main_thread, "options", options)
    monkeypatch.setattr(lark_retry.main_thread, "automation_account", prepare)
    monkeypatch.setattr(connectors, "intake_enabled", enabled)
    monkeypatch.setattr(connectors, "credential", lambda *_: "credential")
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
                    agent_id=agent_id,
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
                    id=card_id,
                    claw_id=claw_id,
                    connector_id=connector_id,
                    run_id=run_id,
                    chat_id="group",
                    source_message_id="source",
                    is_group=True,
                    token=keys.protect(
                        token.encode(), lark_retry.location(org, card_id)
                    ).model_dump(),
                    state="sent",
                    card_message_id="message",
                )
            )

        async def click():
            return await lark_retry.retry(
                runtime, intake, card_id, token, "allowed", "group", "message"
            )

        assert await asyncio.gather(click(), click()) == [True, True]
        assert calls == [f"claw:retry:{run_id}"]
        async with short_session(storage) as session:
            card = await session.get(LarkRetryCard, card_id)
            assert card.state == "admitted" and card.patch_pending
    finally:
        async with original_transaction(storage) as session:
            await session.execute(delete(Claw).where(Claw.id == claw_id))
            await session.execute(delete(WorkspaceRow).where(WorkspaceRow.id == workspace))
            await session.execute(delete(OrganizationRow).where(OrganizationRow.id == org))
        await storage.close()
