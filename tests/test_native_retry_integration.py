"""Exercise native retry admission against disposable Service 0.4.1 and PostgreSQL."""

import asyncio
import json
import os
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Lock, Thread
from uuid import uuid4

import pytest
from a13n_service.app import build_app
from a13n_service.app_sdk import threads as app_threads
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.migrations.scratch import scratch_database
from a13n_service.providers import envd
from a13n_service.resources.egress_credentials.tables import CredentialRow
from a13n_service.runs.environments import external
from a13n_service.runs.environments.mounts import adopt_mounts
from a13n_service.runs.environments.tables import ThreadEnvironmentRow, ThreadSessionBindingRow
from a13n_service.runs.schemas import EnvironmentMount
from a13n_service.runs.tables import InboxEntryRow, RunRow, SessionRow, ThreadRow
from a13n_service.runs.threads import new_thread
from a13n_service.settings import Database, Settings
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from test_service_app_lifecycle import FixtureEnvironment

from a13n_claw.service_app import connectors, egress, inbox, lark_retry, main_thread, project_cli
from a13n_claw.service_app.feishu import LarkSettings
from a13n_claw.service_app.models import APP_ID, Connector, ConnectorEgress, LarkRetryCard


def test_native_retry_concurrent_admission_and_lineage(monkeypatch, tmp_path):
    url = os.environ.get("CLAW_TEST_DATABASE_URL")
    redis = os.environ.get("A13N_REDIS__URL")
    if not url or not redis:
        pytest.skip("Requires disposable PostgreSQL and Redis")

    retry_entered, release_retry = Event(), Event()
    request_lock = Lock()
    requests = 0
    scripted = {"spawn": False, "tools": []}

    class FailingModel(BaseHTTPRequestHandler):
        def do_POST(self):
            nonlocal requests
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if scripted["spawn"]:
                tools = {tool["function"]["name"] for tool in body.get("tools", [])}
                scripted["tools"].append(tools)
                used = {
                    call["function"]["name"]
                    for message in body["messages"]
                    for call in message.get("tool_calls", [])
                }
                spawn = "spawn_subagent" in tools and "spawn_subagent" not in used
                delta = {"role": "assistant", "content": "Done"}
                if spawn:
                    delta = {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_spawn_fixture",
                                "type": "function",
                                "function": {
                                    "name": "spawn_subagent",
                                    "arguments": json.dumps(
                                        {
                                            "name": "fixture-worker",
                                            "instructions": "Complete the delegated work.",
                                            "prompt": "Report the fixture result.",
                                        }
                                    ),
                                },
                            }
                        ]
                    }
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for part, reason in [(delta, None), ({}, "tool_calls" if spawn else "stop")]:
                    chunk = {
                        "id": "chatcmpl-test",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": "scripted",
                        "choices": [{"index": 0, "delta": part, "finish_reason": reason}],
                    }
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                self.wfile.write(b"data: [DONE]\n\n")
                return
            with request_lock:
                requests += 1
                number = requests
            if number == 2:
                retry_entered.set()
                release_retry.wait(timeout=30)
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                b'{"error":{"message":"fixture failure","type":"invalid_request_error"}}'
            )

        def log_message(self, *_args):
            pass

    async def verify(*_args, **_kwargs):
        return "test-device"

    async def connect(*_args, environment_id, **_kwargs):
        return FixtureEnvironment(environment_id)

    async def provision(*_args, **_kwargs):
        return project_cli.Maintenance({"gh": "1", "lark-cli": "1"}, False)

    async def listener(self, receive):
        await asyncio.Event().wait()

    async def no_card_delivery(*_args, **_kwargs):
        # Provider HTTP is tested separately with MockTransport.
        pass

    monkeypatch.setattr(lark_retry, "deliver", no_card_delivery)
    monkeypatch.setattr(external, "verify", verify)
    monkeypatch.setattr(envd, "connect", connect)
    monkeypatch.setattr(project_cli, "provision", provision)
    monkeypatch.setattr(connectors.LarkAdapter, "run", listener)
    # Drive credential reconciliation explicitly to make the fixture deterministic.
    monkeypatch.setattr(connectors.Listeners, "queue_egress", lambda *_args: None)
    with ThreadingHTTPServer(("127.0.0.1", 0), FailingModel) as server:
        server_thread = Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        try:
            with scratch_database(Database(url=url)) as scratch:
                model_url = f"http://127.0.0.1:{server.server_port}"
                settings = Settings.model_validate(
                    {
                        "server": {"public_url": "https://claw.test", "readiness_timeout": 10},
                        "database": scratch.model_copy(update={"auto_migrate": True}),
                        "redis": {"url": redis},
                        "encryption": {"key_file": tmp_path / "key"},
                        "objects": {"root": tmp_path / "objects"},
                        "providers": {"http_origins": [model_url]},
                        "plugins": {"bootstrap_enabled_apps": ["a13n.claw"]},
                    }
                )
                app = build_app(role="all", settings=settings)
                with TestClient(
                    app, base_url="https://claw.test", headers={"Origin": "https://claw.test"}
                ) as client:
                    exercise_retry(
                        client, model_url, retry_entered, release_retry, scripted, monkeypatch
                    )
        finally:
            release_retry.set()
            server.shutdown()
            server_thread.join()


def exercise_retry(client, model_url, retry_entered, release_retry, scripted, monkeypatch):
    runtime = client.app.state.runtime

    def post(path, body, status=201, headers=None):
        result = client.post("/api/v1" + path, json=body, headers=headers)
        assert result.status_code == status, result.text
        return result.json()

    def read(path):
        result = client.get("/api/v1" + path)
        assert result.status_code == 200, result.text
        return result.json()

    auth = post(
        "/auth/bootstrap",
        {"email": "admin@example.com", "name": "Admin", "password": "claw-test-password"},
        200,
    )
    client.headers["X-CSRF-Token"] = auth["csrf_token"]
    org = read("/organizations")["items"][0]["id"]
    ws = read(f"/organizations/{org}/workspaces")["items"][0]["id"]
    client.headers["X-Workspace-ID"] = ws
    account = post(f"/workspaces/{ws}/service-accounts", {"name": "Claw", "role": "builder"})
    key = post(f"/workspaces/{ws}/service-accounts/{account['id']}/keys", {"name": "Fixture"})
    provider = post(
        "/model-providers",
        {
            "name": "Fixture",
            "type": "openai",
            "config": {"base_url": f"{model_url}/v1"},
            "credential": {"api_key": "fixture"},
        },
    )
    model = post(
        "/models",
        {
            "name": "Fixture",
            "key": "scripted",
            "provider_id": provider["id"],
            "config": {"model_api": "openai.chat_completions", "model_name": "scripted"},
        },
    )
    agent = post("/agents", {"name": "Fixture", "config": {"model": model["key"]}})
    environment = post(
        "/environments",
        {"name": "Fixture", "endpoint": "https://fixture.example.test", "token": "fixture"},
        headers={"Authorization": f"Bearer {key['secret']}"},
    )
    base = "/apps/a13n.claw/claws"
    claw = post(
        base,
        {
            "name": "Fixture",
            "agent_id": agent["id"],
            "service_account_id": account["id"],
            "environment_id": environment["id"],
            "heartbeat_seconds": 0,
        },
    )
    path = f"{base}/{claw['id']}"
    config = {"app_id": "fixture", "bot_open_id": "bot", "senders": ["user"], "groups": ["chat"]}
    connector = post(
        path + "/connectors",
        {
            "name": "Fixture",
            "kind": "lark",
            "enabled": True,
            "config": config,
            "credential": "secret",
        },
    )
    delivery = post(
        path + "/input", {"content": "Fail this run"}, headers={"Idempotency-Key": uuid4().hex}
    )

    def wait_for(read_value, condition):
        until = time.monotonic() + 35
        while time.monotonic() < until:
            value = read_value()
            if condition(value):
                return value
            time.sleep(0.1)
        pytest.fail(f"Timed out: {value!r}")

    delivery = wait_for(
        lambda: read(path + "/deliveries/" + delivery["delivery_id"]), lambda row: row["run_id"]
    )
    failed = wait_for(lambda: read("/runs/" + delivery["run_id"]), lambda row: row["sealed_at"])
    assert failed["status"] == "failed", failed
    token, card_id = "synthetic-retry-token", "lrc_" + uuid4().hex

    async def setup_card():
        async with transaction(runtime.storage) as session:
            session.add(
                LarkRetryCard(
                    id=card_id,
                    claw_id=claw["id"],
                    connector_id=connector["id"],
                    run_id=failed["id"],
                    chat_id="chat",
                    source_message_id="source",
                    is_group=True,
                    reply_in_thread=True,
                    token=runtime.keys.protect(
                        token.encode(), lark_retry.location(org, card_id)
                    ).model_dump(),
                    state="sent",
                    card_message_id="message",
                )
            )
            row = await session.get_one(Connector, connector["id"])
            secret = connectors.credential(runtime, org, row)
            return connectors.Intake(
                org,
                ws,
                claw["id"],
                connector["id"],
                LarkSettings.model_validate(row.config),
                secret,
            )

    intake = client.portal.call(setup_card)

    replacement = post(
        "/environments",
        {"name": "Replacement", "endpoint": "https://fixture.example.test", "token": "fixture"},
        headers={"Authorization": f"Bearer {key['secret']}"},
    )
    main_thread_id = failed["thread_id"]
    before = read(f"/threads/{main_thread_id}/environments")
    original_submit = lark_retry.app_threads.submit_message_in_transaction

    async def admission_racing_rebind():
        admission_locked, allow_commit = asyncio.Event(), asyncio.Event()
        owner_pid = None

        async def held_submit(*args, **kwargs):
            nonlocal owner_pid
            result = await original_submit(*args, **kwargs)
            owner_pid = await args[0].scalar(text("SELECT pg_backend_pid()"))
            admission_locked.set()
            await allow_commit.wait()
            return result

        async def click():
            return await lark_retry.retry(
                runtime, intake, card_id, token, "user", "chat", "message"
            )

        def rebind():
            return client.patch(
                "/api/v1" + path + "/project", json={"environment_id": replacement["id"]}
            )

        with monkeypatch.context() as patch:
            patch.setattr(lark_retry.app_threads, "submit_message_in_transaction", held_submit)
            first = asyncio.create_task(click())
            await asyncio.wait_for(admission_locked.wait(), timeout=10)
            competing = asyncio.create_task(asyncio.to_thread(rebind))
            try:
                # Observe a real PostgreSQL lock wait before releasing admission;
                # a merely scheduled HTTP call would not establish the interleaving.
                blocked = False
                for _ in range(100):
                    async with short_session(runtime.storage) as observer:
                        blocked = bool(
                            await observer.scalar(
                                text(
                                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                                    "WHERE datname = current_database() "
                                    "AND wait_event_type = 'Lock' "
                                    "AND :owner = ANY(pg_blocking_pids(pid)))"
                                ),
                                {"owner": owner_pid},
                            )
                        )
                    if blocked:
                        break
                    await asyncio.sleep(0.05)
                assert blocked and not competing.done()
                second = asyncio.create_task(click())
            finally:
                allow_commit.set()
            one, two, response = await asyncio.gather(first, second, competing)
            return one, two, response.status_code, response.text

    one, two, code, message = client.portal.call(admission_racing_rebind)
    assert one and two and code == 409, message
    assert "Finish or cancel" in message
    assert read(f"/threads/{main_thread_id}/environments") == before
    assert read(path)["claw"]["environment_id"] == environment["id"]

    async def assigned():
        async with short_session(runtime.storage) as session:
            rows = (
                await session.scalars(
                    select(InboxEntryRow).where(
                        InboxEntryRow.request_key == f"claw:retry:{failed['id']}"
                    )
                )
            ).all()
            assert len(rows) == 1 and rows[0].status == "assigned"
            return rows[0].assigned_run_id

    retry_run = client.portal.call(assigned)
    assert retry_entered.wait(timeout=20)
    release_retry.set()
    retried = wait_for(lambda: read("/runs/" + retry_run), lambda row: row["sealed_at"])
    assert retried["status"] == "failed", retried
    client.portal.call(lark_retry.discover, runtime, ws, claw["id"])

    async def check_lineage():
        async with short_session(runtime.storage) as session:
            card = await session.scalar(
                select(LarkRetryCard).where(
                    LarkRetryCard.claw_id == claw["id"], LarkRetryCard.run_id == retry_run
                )
            )
            assert card is not None
            assert (
                card.connector_id,
                card.chat_id,
                card.source_message_id,
                card.reply_in_thread,
            ) == (connector["id"], "chat", "source", True)

    client.portal.call(check_lineage)
    changed = client.patch(
        "/api/v1" + path + "/project", json={"environment_id": replacement["id"]}
    )
    assert changed.status_code == 200, changed.text
    mounts = read(f"/threads/{main_thread_id}/environments")["items"]
    assert (
        next(m["environment_id"] for m in mounts if m["name"] == "workspace") == replacement["id"]
    )
    assert (
        next(m["environment_id"] for m in retried["environment_mounts"] if m["name"] == "workspace")
        == environment["id"]
    )

    # Exercise Service's actual dynamic child and parent Thread Inbox, not the
    # retired Claw-managed Worker mechanism.
    scripted["spawn"] = True
    native_claw = post(
        base,
        {
            "name": "Native child fixture",
            "agent_id": agent["id"],
            "service_account_id": account["id"],
            "environment_id": replacement["id"],
            "heartbeat_seconds": 0,
            "worker_models": [{"model": model["key"], "description": "Fixture worker"}],
        },
    )
    native_path = f"{base}/{native_claw['id']}"
    native_input = post(
        native_path + "/input",
        {"content": "Delegate the fixture task"},
        headers={"Idempotency-Key": uuid4().hex},
    )
    native_delivery = wait_for(
        lambda: read(native_path + "/deliveries/" + native_input["delivery_id"]),
        lambda row: row["run_id"],
    )
    parent = wait_for(
        lambda: read("/runs/" + native_delivery["run_id"]), lambda row: row["sealed_at"]
    )
    assert parent["status"] == "completed", parent
    workers = wait_for(
        lambda: read(native_path + "/workers")["items"],
        lambda items: items and items[0]["activity"]["last_status"] in {"completed", "failed"},
    )
    assert len(workers) == 1
    worker = workers[0]
    assert worker["activity"]["last_status"] == "completed", worker
    child = read("/runs/" + worker["activity"]["last_run_id"])

    async def captured_child_authority():
        async with short_session(runtime.storage) as session:
            row = await session.get_one(RunRow, child["id"])
            return row.principal_id, row.authority

    child_principal, child_authority = client.portal.call(captured_child_authority)
    assert child_principal == child_authority["principal_id"] == account["id"]
    assert set(child_authority["verbs"]) == {"read", "run", "write"}
    assert child["session_id"] == parent["session_id"]
    assert child["thread_id"] != parent["thread_id"]
    assert (
        next(
            mount["environment_id"]
            for mount in child["environment_mounts"]
            if mount["name"] == "workspace"
        )
        == replacement["id"]
    )
    assert child["browser_binding"] is None
    assert any("spawn_subagent" in tools for tools in scripted["tools"])
    assert all(
        "claw_assign" not in tools and "claw_connector_reply" not in tools
        for tools in scripted["tools"]
    )

    async def child_results():
        async with short_session(runtime.storage) as session:
            return list(
                await session.scalars(
                    select(InboxEntryRow.id).where(
                        InboxEntryRow.thread_id == parent["thread_id"],
                        InboxEntryRow.kind == "child_result",
                    )
                )
            )

    assert wait_for(lambda: client.portal.call(child_results), bool)
    wait_for(lambda: read("/threads/" + parent["thread_id"]), lambda row: not row["current_run_id"])

    async def transactional_credential():
        generation_id = "ceg_" + uuid4().hex
        credential_id = None
        try:
            async with transaction(runtime.storage) as session:
                await app_threads.prepare_admission(
                    session, runtime, ws, account["id"], app_id=APP_ID
                )
                claw_row = await inbox.lock_claw(session, ws, native_claw["id"])
                generation = ConnectorEgress(
                    id=generation_id,
                    workspace_id=ws,
                    claw_id=claw_row.id,
                    connector_id="fixture",
                    kind="github",
                    actor_id=account["id"],
                    state="preparing",
                )
                session.add(generation)
                await egress.commit_generation(
                    session, runtime, claw_row, generation, "fixture-token", None
                )
                credential_id = generation.credential_id
                for thread_id in (parent["thread_id"], child["thread_id"]):
                    mount = await session.get_one(ThreadEnvironmentRow, (thread_id, "workspace"))
                    binding = await session.get_one(
                        ThreadSessionBindingRow,
                        (thread_id, mount.environment_id, mount.session_binding),
                    )
                    assert binding.configuration["egress_mode"] == "controlled"
                    assert binding.configuration["egress"]["destinations"]["mode"] == "public"
                    assert (
                        binding.configuration["egress"]["secrets"][0]["credential_id"]
                        == credential_id
                    )
                generation.state = "preparing"
                await egress.commit_generation(
                    session, runtime, claw_row, generation, "fixture-rotated", None
                )
                credential = await session.get_one(CredentialRow, credential_id)
                assert credential.credential_version == 2
                await egress.revoke_generation(session, runtime, claw_row, generation)
                assert credential.state == "revoked" and generation.state == "revoked"
                raise RuntimeError("rollback fixture")
        except RuntimeError as error:
            assert str(error) == "rollback fixture"
        async with short_session(runtime.storage) as session:
            assert await session.get(ConnectorEgress, generation_id) is None
            assert await session.get(CredentialRow, credential_id) is None

    def try_transactional_credential():
        try:
            client.portal.call(transactional_credential)
            return True
        except ServiceError as error:
            if error.details.get("reason") != "thread_resources_busy":
                raise
            return False

    wait_for(try_transactional_credential, bool)

    async def background_egress():
        connector_id = "con_" + uuid4().hex
        first_id = "ceg_" + uuid4().hex
        second_id = "ceg_" + uuid4().hex
        async with transaction(runtime.storage) as session:
            connector_row = Connector(
                id=connector_id,
                claw_id=native_claw["id"],
                name="Synthetic egress",
                kind="github",
                config={},
                credential=connectors.protect(runtime, org, connector_id, "synthetic-token"),
                enabled=True,
                egress_primary=True,
            )
            session.add(connector_row)
            session.add(
                ConnectorEgress(
                    id=first_id,
                    workspace_id=ws,
                    claw_id=native_claw["id"],
                    connector_id=connector_id,
                    kind="github",
                    actor_id=account["id"],
                    state="pending",
                )
            )
        for _ in range(100):
            await egress.process_generation(runtime, ws, native_claw["id"], first_id)
            async with transaction(runtime.storage) as session:
                first = await session.get_one(ConnectorEgress, first_id)
                if first.state == "ready":
                    break
                if first.state == "preparing" and first.failure:
                    first.attempt_started_at = datetime.now(UTC) - timedelta(seconds=46)
            await asyncio.sleep(0.1)
        else:
            pytest.fail("Credential binding did not become ready")
        async with transaction(runtime.storage) as session:
            await app_threads.prepare_admission(session, runtime, ws, account["id"], app_id=APP_ID)
            claw_row = await inbox.lock_claw(session, ws, native_claw["id"])
            first = await session.get_one(ConnectorEgress, first_id)
            assert first.state == "ready" and first.credential_id
            old_credential = first.credential_id
            first.state = "preparing"
            await session.flush()
            initial = await egress.initial_configuration(session, claw_row)
            assert initial.credential_references[0].credential_id == old_credential
            first.state = "revoke_pending"
            second = ConnectorEgress(
                id=second_id,
                workspace_id=ws,
                claw_id=claw_row.id,
                connector_id=connector_id,
                kind="github",
                actor_id=account["id"],
                state="preparing",
            )
            session.add(second)
            await egress.commit_generation(session, runtime, claw_row, second, "new-token", None)
            new_credential = second.credential_id
        # Delayed cleanup of the old generation must leave the replacement bound.
        await egress.process_generation(runtime, ws, native_claw["id"], first_id)
        await egress.process_generation(runtime, ws, native_claw["id"], first_id)
        async with transaction(runtime.storage) as session:
            first = await session.get_one(ConnectorEgress, first_id)
            assert first.state == "revoked"
            assert (await session.get_one(CredentialRow, old_credential)).state == "revoked"
            mount = await session.get_one(ThreadEnvironmentRow, (parent["thread_id"], "workspace"))
            binding = await session.get_one(
                ThreadSessionBindingRow,
                (parent["thread_id"], mount.environment_id, mount.session_binding),
            )
            assert binding.configuration["egress"]["secrets"][0]["credential_id"] == new_credential
            second = await session.get_one(ConnectorEgress, second_id)
            second.state = "revoke_pending"
        await egress.process_generation(runtime, ws, native_claw["id"], second_id)
        async with short_session(runtime.storage) as session:
            assert (await session.get_one(ConnectorEgress, second_id)).state == "unbinding"
            assert (await session.get_one(CredentialRow, new_credential)).state == "revoked"

        # A real pending native entry makes Service reject an update to the
        # shared subtree; withdrawing it then permits durable cleanup.
        busy_id = "entry_" + uuid4().hex
        async with transaction(runtime.storage) as session:
            source = await session.get_one(RunRow, parent["id"])
            session.add(
                InboxEntryRow(
                    id=busy_id,
                    organization_id=org,
                    workspace_id=ws,
                    thread_id=parent["thread_id"],
                    kind="child_message",
                    delivery="next_run",
                    position=999999,
                    principal_id=account["id"],
                    authority=source.authority,
                    payload={},
                    size=0,
                    options={},
                    origin_run_id=parent["id"],
                    status="pending",
                )
            )
        await egress.process_generation(runtime, ws, native_claw["id"], second_id)
        async with transaction(runtime.storage) as session:
            generation = await session.get_one(ConnectorEgress, second_id)
            assert (generation.state, generation.failure) == ("unbinding", "credential_sync_failed")
            busy_entry = await session.get_one(InboxEntryRow, busy_id)
            busy_entry.status = "withdrawn"
            busy_entry.finished_at = datetime.now(UTC)
        await egress.process_generation(runtime, ws, native_claw["id"], second_id)
        async with short_session(runtime.storage) as session:
            assert (await session.get_one(ConnectorEgress, second_id)).state == "revoked"

        lark_connector, lark_generation = "con_" + uuid4().hex, "ceg_" + uuid4().hex
        async with transaction(runtime.storage) as session:
            session.add(
                Connector(
                    id=lark_connector,
                    claw_id=native_claw["id"],
                    name="Synthetic Lark egress",
                    kind="lark",
                    config=LarkSettings(
                        app_id="app", bot_open_id="bot", senders=("user",)
                    ).model_dump(mode="json"),
                    credential=connectors.protect(
                        runtime, org, lark_connector, "synthetic-app-secret"
                    ),
                    enabled=True,
                    egress_primary=True,
                )
            )
            session.add(
                ConnectorEgress(
                    id=lark_generation,
                    workspace_id=ws,
                    claw_id=native_claw["id"],
                    connector_id=lark_connector,
                    kind="lark",
                    domain="https://open.feishu.cn",
                    actor_id=account["id"],
                    state="pending",
                )
            )
        exchanges = []

        async def exchange(_self):
            exchanges.append(True)
            return f"synthetic-tenant-{len(exchanges)}", 7200

        with monkeypatch.context() as patch:
            patch.setattr(connectors.LarkAdapter, "exchange_token", exchange)
            await egress.process_generation(runtime, ws, native_claw["id"], lark_generation)
            async with transaction(runtime.storage) as session:
                generation = await session.get_one(ConnectorEgress, lark_generation)
                assert generation.state == "ready"
                assert (generation.token_expires_at - generation.refresh_at).total_seconds() == 2700
                tenant_credential = generation.credential_id
                generation.refresh_at = generation.created_at
                snapshots = {}
                for thread_id in (parent["thread_id"], child["thread_id"]):
                    mount = await session.get_one(ThreadEnvironmentRow, (thread_id, "workspace"))
                    binding = await session.get_one(
                        ThreadSessionBindingRow,
                        (thread_id, mount.environment_id, mount.session_binding),
                    )
                    snapshots[thread_id] = (binding.configuration, binding.version)
                # The shared child is busy, but value-only rotation must still commit.
                source = await session.get_one(RunRow, parent["id"])
                rotation_busy_id = "entry_" + uuid4().hex
                session.add(
                    InboxEntryRow(
                        id=rotation_busy_id,
                        organization_id=org,
                        workspace_id=ws,
                        thread_id=child["thread_id"],
                        kind="child_message",
                        delivery="next_run",
                        position=999998,
                        principal_id=account["id"],
                        authority=source.authority,
                        payload={},
                        size=0,
                        options={},
                        origin_run_id=parent["id"],
                        status="pending",
                    )
                )
            await egress.process_generation(runtime, ws, native_claw["id"], lark_generation)
        async with transaction(runtime.storage) as session:
            generation = await session.get_one(ConnectorEgress, lark_generation)
            assert generation.state == "ready" and generation.credential_id == tenant_credential
            assert (await session.get_one(CredentialRow, tenant_credential)).credential_version == 2
            assert len(exchanges) == 2
            for thread_id in (parent["thread_id"], child["thread_id"]):
                mount = await session.get_one(ThreadEnvironmentRow, (thread_id, "workspace"))
                binding = await session.get_one(
                    ThreadSessionBindingRow,
                    (thread_id, mount.environment_id, mount.session_binding),
                )
                assert (binding.configuration, binding.version) == snapshots[thread_id]
            busy_entry = await session.get_one(InboxEntryRow, rotation_busy_id)
            assert busy_entry.status == "pending"
            busy_entry.status = "withdrawn"
            busy_entry.finished_at = datetime.now(UTC)

        # Fresh scheduled Threads use current Project mounts without connector credentials.
        async with transaction(runtime.storage) as session:
            await app_threads.prepare_admission(session, runtime, ws, account["id"], app_id=APP_ID)
            claw_row = await inbox.lock_claw(session, ws, native_claw["id"])
            current = await egress.initial_configuration(session, claw_row)
            assert current.credential_references[0].credential_id == tenant_credential
            mounts = main_thread.project_mounts(claw_row)
            assert mounts[0].environment_id == claw_row.environment_id
            assert mounts[0].session_configuration is None

        policies = (
            ({"egress_mode": "deny"}, False),
            ({"egress_mode": "inherit"}, False),
            (
                {
                    "egress_mode": "controlled",
                    "egress": {
                        "destinations": {"mode": "allowlist", "hosts": ["example.com"]},
                        "secrets": [],
                    },
                },
                False,
            ),
            (
                {
                    "egress_mode": "controlled",
                    "egress": {
                        "destinations": {"mode": "allowlist", "hosts": list(egress.GITHUB_HOSTS)},
                        "secrets": [],
                    },
                },
                True,
            ),
        )
        for policy, allowed in policies:
            try:
                async with transaction(runtime.storage) as session:
                    await app_threads.prepare_admission(
                        session, runtime, ws, account["id"], app_id=APP_ID
                    )
                    claw_row = await inbox.lock_claw(session, ws, native_claw["id"])
                    for thread_id in (parent["thread_id"], child["thread_id"]):
                        mount = await session.get_one(
                            ThreadEnvironmentRow, (thread_id, "workspace")
                        )
                        binding = await session.get_one(
                            ThreadSessionBindingRow,
                            (thread_id, mount.environment_id, mount.session_binding),
                        )
                        binding.configuration = policy
                    await session.flush()
                    generation = ConnectorEgress(
                        id="ceg_" + uuid4().hex,
                        workspace_id=ws,
                        claw_id=claw_row.id,
                        connector_id=connector_id,
                        kind="github",
                        actor_id=account["id"],
                        state="preparing",
                    )
                    session.add(generation)
                    await egress.commit_generation(
                        session, runtime, claw_row, generation, "policy-token", None
                    )
                    assert allowed
                    for thread_id in (parent["thread_id"], child["thread_id"]):
                        mount = await session.get_one(
                            ThreadEnvironmentRow, (thread_id, "workspace")
                        )
                        binding = await session.get_one(
                            ThreadSessionBindingRow,
                            (thread_id, mount.environment_id, mount.session_binding),
                            populate_existing=True,
                        )
                        assert (
                            binding.configuration["egress"]["destinations"]
                            == policy["egress"]["destinations"]
                        )
                    raise RuntimeError("policy fixture rollback")
            except ServiceError:
                assert not allowed
            except RuntimeError as error:
                assert allowed and str(error) == "policy fixture rollback"

        stale_id = "ceg_" + uuid4().hex
        async with transaction(runtime.storage) as session:
            session.add(
                ConnectorEgress(
                    id=stale_id,
                    workspace_id=ws,
                    claw_id=native_claw["id"],
                    connector_id=lark_connector,
                    kind="lark",
                    domain="https://open.feishu.cn",
                    actor_id=account["id"],
                    state="pending",
                )
            )

        async def invalidate_during_exchange(_self):
            async with transaction(runtime.storage) as session:
                await inbox.lock_claw(session, ws, native_claw["id"])
                await egress.invalidate_generations(session, lark_connector)
            return "obsolete-token", 7200

        with monkeypatch.context() as patch:
            patch.setattr(connectors.LarkAdapter, "exchange_token", invalidate_during_exchange)
            await egress.process_generation(runtime, ws, native_claw["id"], stale_id)
        async with short_session(runtime.storage) as session:
            stale = await session.get_one(ConnectorEgress, stale_id)
            assert stale.state == "revoked" and stale.credential_id is None
        await egress.process_generation(runtime, ws, native_claw["id"], lark_generation)
        await egress.process_generation(runtime, ws, native_claw["id"], lark_generation)

    client.portal.call(background_egress)
    child_thread_url = "/api/v1/threads/" + child["thread_id"]
    before_child = client.get(child_thread_url)
    assert before_child.status_code == 200, before_child.text

    # Child completion can wake Main after its first Run has sealed. Conflict
    # attempts have no effects; wait until that native follow-up finishes.
    async def make_descendant():
        # Construct a multi-level native graph with inverse lexical IDs to
        # exercise the Service parent-before-child locking rule.
        async with transaction(runtime.storage) as session:
            ancestor = await session.get_one(ThreadRow, child["thread_id"])
            owner = await session.get_one(SessionRow, ancestor.session_id)
            row = new_thread(
                owner,
                mcp_headers={},
                origin="child",
                origin_thread_id=ancestor.id,
                origin_run_id=child["id"],
                origin_tool_call_id="fixture-grandchild",
                subagent="grandchild",
            )
            row.id = "0" + row.id
            row.agent_snapshot = {**ancestor.agent_snapshot, "definition_id": row.id}
            session.add(row)
            await session.flush()
            await adopt_mounts(
                session,
                row,
                [EnvironmentMount.model_validate(m) for m in child["environment_mounts"]],
            )
            return row.id

    grandchild_id = client.portal.call(make_descendant)

    async def rebind_with_parent_locked():
        async with transaction(runtime.storage) as holder:
            await holder.scalar(
                select(ThreadRow).where(ThreadRow.id == child["thread_id"]).with_for_update()
            )
            owner_pid = await holder.scalar(text("SELECT pg_backend_pid()"))
            task = asyncio.create_task(
                asyncio.to_thread(
                    client.patch,
                    "/api/v1" + native_path + "/project",
                    json={"environment_id": environment["id"]},
                )
            )
            try:
                blocked = False
                for _ in range(100):
                    async with short_session(runtime.storage) as observer:
                        blocked = bool(
                            await observer.scalar(
                                text(
                                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity WHERE "
                                    ":owner = ANY(pg_blocking_pids(pid)))"
                                ),
                                {"owner": owner_pid},
                            )
                        )
                    if blocked:
                        break
                    await asyncio.sleep(0.05)
                assert blocked and not task.done()
                # A child-to-parent operation holding this parent must still be
                # able to lock its child; reverse ordering would deadlock here.
                await asyncio.wait_for(
                    holder.scalar(
                        select(ThreadRow).where(ThreadRow.id == grandchild_id).with_for_update()
                    ),
                    timeout=2,
                )
            finally:
                pass
        return await task

    rebound = client.portal.call(rebind_with_parent_locked)
    if rebound.status_code == 409:
        # The parent may have been woken by the child result while this lock
        # interleaving was in progress. A rejected rebind has no side effects.
        rebound = wait_for(
            lambda: client.patch(
                "/api/v1" + native_path + "/project",
                json={"environment_id": environment["id"]},
            ),
            lambda response: response.status_code != 409,
        )
    assert rebound.status_code == 200, rebound.text
    grandchild_mounts = read("/threads/" + grandchild_id + "/environments")["items"]
    assert (
        next(m["environment_id"] for m in grandchild_mounts if m["name"] == "workspace")
        == environment["id"]
    )
    child_mounts = read("/threads/" + child["thread_id"] + "/environments")["items"]
    assert (
        next(m["environment_id"] for m in child_mounts if m["name"] == "workspace")
        == environment["id"]
    )
    after_child = client.get(child_thread_url)
    assert after_child.headers["ETag"] != before_child.headers["ETag"]
    assert (
        next(m["environment_id"] for m in child["environment_mounts"] if m["name"] == "workspace")
        == replacement["id"]
    )
