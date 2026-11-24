"""Retry authorization checks and idempotent submission without provider I/O."""

import asyncio
import base64
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from a13n_service.infra.crypto import KeyRing
from a13n_service.infra.errors import ServiceError
from a13n_service.resources.agents.tables import AgentRow
from a13n_service.runs.tables import RunRow
from pydantic import SecretStr

from a13n_claw.service_app import connectors, lark_retry
from a13n_claw.service_app.feishu import LarkSettings
from a13n_claw.service_app.models import Connector, LarkRetryCard


def test_retry_checks_current_authority_and_submits_once(monkeypatch):
    asyncio.run(exercise_retry(monkeypatch))


async def exercise_retry(monkeypatch):
    keys = KeyRing(
        active_key_id="fixture",
        keys={"fixture": SecretStr(base64.b64encode(b"x" * 32).decode())},
    )
    settings = LarkSettings(
        app_id="app", bot_open_id="bot", senders=("allowed",), groups=("group",)
    )
    intake = connectors.Intake("org", "workspace", "claw", "connector", settings, "credential")
    token = "random-token"
    card = SimpleNamespace(
        id="card",
        claw_id="claw",
        connector_id="connector",
        run_id="run",
        chat_id="group",
        is_group=True,
        token=keys.protect(token.encode(), lark_retry.location("org", "card")).model_dump(),
        state="sent",
        card_message_id="message",
        patch_pending=False,
        admitted_at=None,
    )
    claw = SimpleNamespace(
        id="claw",
        workspace_id="workspace",
        service_account_id="account",
        main_thread_id="thread",
        agent_id="agent",
    )
    connector = SimpleNamespace(
        id="connector", claw_id="claw", kind="lark", enabled=True, config=settings.model_dump()
    )
    thread = SimpleNamespace(id="thread", current_run_id=None, last_run_id="run")
    run = SimpleNamespace(id="run", status="failed", principal_id="account")
    agent = SimpleNamespace(id="agt_" + "a" * 24)
    submitted = []
    receipt_status = "assigned"

    class Session:
        async def scalar(self, query):
            if query.column_descriptions[0]["entity"] is AgentRow:
                return agent
            return "account"

        async def get(self, model, _id, **_kwargs):
            return {Connector: connector, LarkRetryCard: card, RunRow: run}[model]

    @asynccontextmanager
    async def transaction(_storage):
        yield Session()

    async def enabled(*_args, **_kwargs):
        return True

    async def lock_claw(*_args):
        return claw

    async def prepare(*_args, **_kwargs):
        return object()

    async def get_thread(*_args, **_kwargs):
        return thread

    async def options(*_args, **_kwargs):
        return {}

    async def submit(*_args, **kwargs):
        submitted.append(kwargs["request_key"])
        return SimpleNamespace(
            entry=SimpleNamespace(
                status=receipt_status,
                assigned_run_id="new-run" if receipt_status == "assigned" else None,
            )
        ), True

    async def now(_session):
        return datetime.now(UTC)

    monkeypatch.setattr(lark_retry, "transaction", transaction)
    monkeypatch.setattr(lark_retry.inbox, "lock_claw", lock_claw)
    monkeypatch.setattr(lark_retry, "get_thread", get_thread)
    monkeypatch.setattr(lark_retry, "now", now)
    monkeypatch.setattr(lark_retry.app_threads, "prepare_admission", prepare)
    monkeypatch.setattr(lark_retry.app_threads, "submit_message_in_transaction", submit)
    monkeypatch.setattr(
        lark_retry.main_thread, "selected_revision", lambda *_args: "rev_" + "b" * 24
    )
    monkeypatch.setattr(lark_retry.main_thread, "options", options)
    monkeypatch.setattr(lark_retry.main_thread, "automation_account", prepare)
    monkeypatch.setattr(connectors, "intake_enabled", enabled)
    monkeypatch.setattr(connectors, "credential", lambda *_args: "credential")
    runtime = SimpleNamespace(storage=None, keys=keys)

    async def click(user="allowed", chat="group", message="message", secret=token):
        return await lark_retry.retry(runtime, intake, "card", secret, user, chat, message)

    assert not await click(user="outsider")
    assert not await click(chat="other")
    assert not await click(message="other")
    assert not await click(secret="wrong")
    connector.enabled = False
    assert not await click()
    connector.enabled = True
    thread.last_run_id = "newer"
    assert not await click()
    thread.last_run_id = "run"
    receipt_status = "pending"
    with pytest.raises(ServiceError):
        await click()
    assert card.state == "sent" and not card.patch_pending
    receipt_status = "assigned"
    assert await click()
    assert card.state == "admitted" and card.patch_pending
    assert await click()
    assert submitted == ["claw:retry:run", "claw:retry:run"]
