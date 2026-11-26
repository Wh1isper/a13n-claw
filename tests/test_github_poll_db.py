"""PostgreSQL admission, replay and feedback recovery for GitHub polling."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from a13n_service.infra.db import Storage, short_session, transaction
from a13n_service.tenancy.tables import OrganizationRow, WorkspaceRow
from sqlalchemy import delete, select

from a13n_claw.service_app import connectors, github_intake, inbox
from a13n_claw.service_app.github_poll import Event, GitHubSettings, Scan
from a13n_claw.service_app.models import Claw, Connector, GitHubPollState, GitHubReceipt, InboxItem


def test_github_durable_admission_and_feedback(monkeypatch):
    url = os.environ.get("CLAW_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set CLAW_TEST_DATABASE_URL to a disposable Service PostgreSQL database")

    async def enabled(*args, **kwargs):
        return True

    monkeypatch.setattr(connectors, "intake_enabled", enabled)
    monkeypatch.setattr(connectors, "credential", lambda *args: "fixture-token")
    asyncio.run(exercise_database(url))


async def exercise_database(url):
    storage = Storage(url, pool_size=4)
    runtime = SimpleNamespace(storage=storage)
    org, workspace, claw_id, connector_id = (uuid4().hex for _ in range(4))
    config = GitHubSettings(mode="polling", senders=("*",), mark_read=True)
    intake = github_intake.Intake(org, workspace, claw_id, connector_id, config, "fixture-token")
    try:
        async with transaction(storage) as session:
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
                    unread_limit=1,
                )
            )
            await session.flush()
            session.add(
                Connector(
                    id=connector_id,
                    claw_id=claw_id,
                    name="github",
                    kind="github",
                    config=config.model_dump(mode="json"),
                    enabled=True,
                )
            )
        cursor, modified, due = await github_intake.checkpoint(runtime, intake)
        assert cursor and modified is None and due
        event1 = Event(
            "github:comment:1:issues:10:date",
            "1",
            "owner/repo",
            "alice",
            "hello @bot",
            {"comment_id": 10},
            "https://api.github.com/repos/owner/repo/issues/comments/10/reactions",
        )
        event2 = Event(
            "github:comment:1:issues:11:date",
            "2",
            "owner/repo",
            "bob",
            "again @bot",
            {"comment_id": 11},
            "https://api.github.com/repos/owner/repo/issues/comments/11/reactions",
        )
        stamp = datetime.now(UTC) + timedelta(seconds=1)
        scan = Scan((event1, event2), stamp, "provider-checkpoint", 60)
        complete, deliveries = await github_intake.accept_scan(
            runtime, intake, scan, cursor, modified
        )
        assert not complete and len(deliveries) == 1
        async with short_session(storage) as session:
            state = await session.get(GitHubPollState, connector_id)
            assert state.cursor == cursor and state.last_modified is None
            items = (
                await session.scalars(select(InboxItem).where(InboxItem.claw_id == claw_id))
            ).all()
            assert len(items) == 1
            first_id = items[0].id
        await inbox.pop(storage, workspace, claw_id, first_id)
        complete, deliveries = await github_intake.accept_scan(
            runtime, intake, scan, cursor, modified
        )
        assert complete and len(deliveries) == 1
        # A stale competing scan cannot advance or re-admit after the checkpoint changes.
        assert await github_intake.accept_scan(runtime, intake, scan, cursor, modified) == (
            False,
            [],
        )
        calls = []

        def handler(request):
            calls.append(request)
            if (
                request.method == "POST"
                and len([call for call in calls if call.method == "POST"]) == 1
            ):
                return httpx.Response(503)
            if request.url.path.endswith("/11/reactions"):
                return httpx.Response(410)
            return httpx.Response(200, json={})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await github_intake.feedback(runtime, intake, client)
            async with short_session(storage) as session:
                receipts = (
                    await session.scalars(
                        select(GitHubReceipt).where(GitHubReceipt.connector_id == connector_id)
                    )
                ).all()
                assert sorted(row.reaction_state for row in receipts) == ["pending", "unavailable"]
                assert all(row.mark_read_state == "sent" for row in receipts)
            await github_intake.feedback(runtime, intake, client)
        async with short_session(storage) as session:
            state = await session.get(GitHubPollState, connector_id)
            receipts = (
                await session.scalars(
                    select(GitHubReceipt).where(GitHubReceipt.connector_id == connector_id)
                )
            ).all()
            assert state.cursor == stamp and state.last_modified == "provider-checkpoint"
            assert sorted(row.reaction_state for row in receipts) == ["sent", "unavailable"]
            assert all(call.headers["authorization"] == "Bearer fixture-token" for call in calls)

        # Provider intervals survive failed scans and same-provider configuration changes.
        await github_intake.record_interval(runtime, intake, 600)
        async with transaction(storage) as session:
            state = await session.get(GitHubPollState, connector_id)
            deadline = state.next_poll_at
            assert state.server_poll_interval == 600
            assert deadline >= datetime.now(UTC) + timedelta(seconds=590)
            await github_intake.reconfigure(
                session,
                connector_id,
                config,
                config.model_copy(update={"poll_interval_seconds": 300}),
            )
        async with short_session(storage) as session:
            state = await session.get(GitHubPollState, connector_id)
            assert state.cursor == stamp and state.last_modified is None
            assert state.next_poll_at == deadline
        assert (await github_intake.checkpoint(runtime, intake))[2] is False

        # Failed early receipts must not starve later ones indefinitely.
        async with transaction(storage) as session:
            for number in range(11):
                session.add(
                    GitHubReceipt(
                        id=f"batch-{number:02}",
                        connector_id=connector_id,
                        source_key=f"batch-{number}",
                        reaction_url=f"https://api.github.com/reactions/{number}",
                        reaction_state="pending",
                        thread_id=str(number),
                        mark_read_state="disabled",
                    )
                )
        attempted = []

        def fail(request):
            attempted.append(request.url.path)
            return httpx.Response(403)

        async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
            await github_intake.feedback(runtime, intake, client)
            assert len(attempted) == 10
            await github_intake.feedback(runtime, intake, client)
        assert "/reactions/10" in attempted[10:]
        assert github_intake.receipt_id(connector_id, "https://one.example", event1.key) != (
            github_intake.receipt_id(connector_id, "https://two.example", event1.key)
        )
        async with transaction(storage) as session:
            await github_intake.reconfigure(
                session,
                connector_id,
                config,
                config.model_copy(update={"api_url": "https://other.example"}),
            )
        async with short_session(storage) as session:
            assert await session.get(GitHubPollState, connector_id) is None
            pending = (
                await session.scalars(
                    select(GitHubReceipt).where(
                        GitHubReceipt.connector_id == connector_id, GitHubReceipt.id.like("batch-%")
                    )
                )
            ).all()
            assert all(row.reaction_state == "unavailable" for row in pending)
    finally:
        async with transaction(storage) as session:
            await session.execute(delete(Claw).where(Claw.id == claw_id))
            await session.execute(delete(WorkspaceRow).where(WorkspaceRow.id == workspace))
            await session.execute(delete(OrganizationRow).where(OrganizationRow.id == org))
        await storage.close()


def test_full_poll_recovers_http_failure_without_advancing(monkeypatch):
    url = os.environ.get("CLAW_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set CLAW_TEST_DATABASE_URL to a disposable Service PostgreSQL database")

    async def enabled(*args, **kwargs):
        return True

    async def wake(*args, **kwargs):
        pass

    monkeypatch.setattr(connectors, "intake_enabled", enabled)
    monkeypatch.setattr(connectors, "credential", lambda *args: "fixture-token")
    monkeypatch.setattr(github_intake.main_thread, "notify", wake)
    asyncio.run(exercise_full_poll(url))


async def exercise_full_poll(url):
    from email.utils import format_datetime

    storage = Storage(url, pool_size=4)
    runtime = SimpleNamespace(storage=storage)
    org, workspace, claw_id, connector_id = (uuid4().hex for _ in range(4))
    config = GitHubSettings(mode="polling", senders=("alice",), poll_interval_seconds=60)
    intake = github_intake.Intake(org, workspace, claw_id, connector_id, config, "fixture-token")
    failure = True
    date = datetime.now(UTC).replace(microsecond=0)
    notice = {
        "id": "123",
        "reason": "mention",
        "repository": {"id": 1, "full_name": "org/repo"},
        "subject": {"type": "Issue", "url": "https://api.github.com/repos/org/repo/issues/7"},
    }
    subject = {
        "number": 7,
        "title": "Issue",
        "body": "hello @bot",
        "user": {"login": "Alice"},
        "created_at": date.isoformat(),
        "updated_at": date.isoformat(),
    }
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path == "/user":
            return httpx.Response(200, json={"login": "bot"})
        if request.url.path == "/notifications":
            return httpx.Response(
                200,
                json=[notice],
                headers={
                    "Date": format_datetime(date, usegmt=True),
                    "Last-Modified": format_datetime(date, usegmt=True),
                    "X-Poll-Interval": "600",
                },
            )
        if request.url.path == "/repos/org/repo/issues/7":
            return httpx.Response(503) if failure else httpx.Response(200, json=subject)
        if request.url.path == "/repos/org/repo/issues/7/comments":
            return httpx.Response(200, json=[])
        if request.url.path == "/repos/org/repo/issues/7/reactions":
            return httpx.Response(201, json={})
        raise AssertionError(str(request.url))

    try:
        async with transaction(storage) as session:
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
                )
            )
            await session.flush()
            session.add(
                Connector(
                    id=connector_id,
                    claw_id=claw_id,
                    name="github",
                    kind="github",
                    config=config.model_dump(mode="json"),
                    enabled=True,
                )
            )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await github_intake.poll(runtime, intake, client)
            async with transaction(storage) as session:
                state = await session.get(GitHubPollState, connector_id)
                initial = state.cursor
                assert state.last_modified is None and state.server_poll_interval == 600
                assert state.next_poll_at >= datetime.now(UTC) + timedelta(seconds=590)
                assert not (
                    await session.scalars(select(InboxItem).where(InboxItem.claw_id == claw_id))
                ).all()
                # Simulate waiting for the provider interval in this disposable database.
                state.next_poll_at = datetime.now(UTC) - timedelta(seconds=1)
            failure = False
            await github_intake.poll(runtime, intake, client)
        async with short_session(storage) as session:
            state = await session.get(GitHubPollState, connector_id)
            assert initial < state.cursor == date
            items = (
                await session.scalars(select(InboxItem).where(InboxItem.claw_id == claw_id))
            ).all()
            receipts = (
                await session.scalars(
                    select(GitHubReceipt).where(GitHubReceipt.connector_id == connector_id)
                )
            ).all()
            assert len(items) == len(receipts) == 1
            assert items[0].source["sender"] == "Alice" and receipts[0].reaction_state == "sent"
            assert len([call for call in calls if call.url.path == "/notifications"]) == 2
    finally:
        async with transaction(storage) as session:
            await session.execute(delete(Claw).where(Claw.id == claw_id))
            await session.execute(delete(WorkspaceRow).where(WorkspaceRow.id == workspace))
            await session.execute(delete(OrganizationRow).where(OrganizationRow.id == org))
        await storage.close()


def test_egress_generation_batch_and_orphan_invalidation():
    from a13n_claw.service_app import egress
    from a13n_claw.service_app.models import ConnectorEgress
    from a13n_claw.service_app.routes.connectors import connector_generations

    url = os.environ.get("CLAW_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set CLAW_TEST_DATABASE_URL to a disposable Service PostgreSQL database")

    async def exercise():
        storage = Storage(url, pool_size=2)
        claw, connector = uuid4().hex, uuid4().hex
        ids = [uuid4().hex for _ in range(4)]
        try:
            async with transaction(storage) as session:
                for index, (state, failure, credential) in enumerate(
                    (
                        ("ready", None, "key"),
                        ("pending", "failed", None),
                        ("revoked", "old", None),
                        ("ready", "orphan", "key"),
                    )
                ):
                    session.add(
                        ConnectorEgress(
                            id=ids[index],
                            workspace_id="workspace",
                            claw_id=claw,
                            connector_id=connector if index < 3 else "deleted-" + connector,
                            kind="github",
                            actor_id="account",
                            state=state,
                            failure=failure,
                            credential_id=credential,
                        )
                    )
                await session.flush()
                rows = await connector_generations(session, [connector, "deleted-" + connector])
                assert rows[connector].id == ids[1]  # failure takes precedence over newer rows
                assert rows["deleted-" + connector].id == ids[3]
                await egress.invalidate_claw(session, claw)
                orphan = await session.get_one(ConnectorEgress, ids[3])
                assert orphan.state == "revoke_pending" and orphan.failure is None
                assert (await session.get_one(ConnectorEgress, ids[1])).state == "revoked"
        finally:
            async with transaction(storage) as session:
                await session.execute(
                    delete(ConnectorEgress).where(ConnectorEgress.claw_id == claw)
                )
            await storage.close()

    asyncio.run(exercise())
