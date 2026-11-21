"""Run Service and its native worker against disposable PostgreSQL and Redis."""

import asyncio
import json
import os
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace
from uuid import uuid4

import pytest
from a13n_harness.environment.execution import EnvironmentExecution
from a13n_harness.environment.models import (
    EnvironmentAvailability,
    EnvironmentDescriptor,
    EnvironmentPermissionSet,
)
from a13n_harness.environment.operations import EnvironmentOperations
from a13n_service.app import build_app
from a13n_service.infra.db import short_session, transaction
from a13n_service.infra.errors import ServiceError
from a13n_service.migrations.scratch import scratch_database
from a13n_service.providers import envd
from a13n_service.runs.environments import external
from a13n_service.runs.inbox import find_request
from a13n_service.runs.seal_events import RunSealEvent
from a13n_service.runs.tables import InboxEntryRow, RunRow, ThreadRow
from a13n_service.settings import Database, Settings
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from a13n_claw.service_app import (
    app,
    connectors,
    inbox,
    main_thread,
    project_cli,
    schedules,
    workers,
)
from a13n_claw.service_app.models import Claw, InboxItem, Schedule, ScheduleRequest, Worker


@pytest.fixture
def model_server():
    requests = []
    unbound_tools = []
    reply = []
    tool_results = []

    class ModelHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            tools = {tool["function"]["name"] for tool in body.get("tools", [])}
            requests.append(tools)
            if "Native usage probe" in json.dumps(body["messages"]):
                unbound_tools.append(tools)
            tool_results.extend(
                message["content"] for message in body["messages"] if message["role"] == "tool"
            )
            used = {
                call["function"]["name"]
                for message in body["messages"]
                for call in message.get("tool_calls", [])
            }
            if reply and "claw_connector_reply" in tools:
                tool, args = "claw_connector_reply", reply.pop(0)
            elif "claw_assign" in tools - used:
                tool, args = "claw_assign", {"task": "Collect a result", "name": "Test Worker"}
            elif "claw_schedule" in tools - used:
                tool, args = (
                    "claw_schedule",
                    {
                        "target": "fresh",
                        "body": {
                            "name": "Fresh follow-up",
                            "prompt": "Collect a result and report it to Main.",
                            "rule": {
                                "type": "once",
                                "run_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
                            },
                        },
                    },
                )
            elif "claw_report" in tools - used:
                tool, args = "claw_report", {"message": "Worker result"}
            else:
                tool, args = None, None
            delta = {"role": "assistant", "content": "Done"}
            if tool:
                delta = {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": f"call_{tool}_{len(requests)}",
                            "type": "function",
                            "function": {"name": tool, "arguments": json.dumps(args)},
                        }
                    ]
                }
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for part, reason in [(delta, None), ({}, "tool_calls" if tool else "stop")]:
                chunk = {
                    "id": "chatcmpl-claw-test",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "scripted",
                    "choices": [{"index": 0, "delta": part, "finish_reason": reason}],
                }
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")

        def log_message(self, *_args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler) as server:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield (
                f"http://127.0.0.1:{server.server_port}",
                requests,
                reply,
                tool_results,
                unbound_tools,
            )
        finally:
            server.shutdown()
            thread.join()


def eventually(read, ready, *, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = read()
        if ready(value):
            return value
        time.sleep(0.1)
    pytest.fail(f"Last observed value: {value!r}")


class FixtureEnvironment(EnvironmentExecution):
    """One inert ready target; this test does not execute commands in it."""

    provider_key = "fixture"
    state = None
    descriptor = EnvironmentDescriptor(
        generation="fixture",
        working_directory="/workspace",
        operation_families=frozenset(),
        permissions=EnvironmentPermissionSet(operations=frozenset()),
    )
    availability = EnvironmentAvailability(status="available")
    operations = EnvironmentOperations()

    def __init__(self, environment_id):
        self._environment_id = environment_id
        self._execution_id = f"exec-{uuid4().hex}"

    @property
    def environment_id(self):
        return self._environment_id

    @property
    def execution_id(self):
        return self._execution_id

    async def open(self):
        return self

    async def check_ready(self, operations):
        pass

    async def close(self):
        pass


def test_first_setup_and_native_runs(monkeypatch, tmp_path, model_server):
    database_url = os.environ.get("CLAW_TEST_DATABASE_URL")
    redis_url = os.environ.get("A13N_REDIS__URL")
    if not database_url or not redis_url:
        pytest.skip("Set CLAW_TEST_DATABASE_URL and A13N_REDIS__URL to disposable services")

    async def verify(*_args, **_kwargs):
        return "test-device"

    async def connect(*_args, environment_id, **_kwargs):
        return FixtureEnvironment(environment_id)

    maintenance = []

    async def provision(_environment, *, update=False):
        maintenance.append(update)
        return project_cli.Maintenance({"gh": "2.105.0", "lark-cli": "1.0.100"}, False)

    async def check(kind, connector_id, config, token):
        assert token == "synthetic-github-credential"

    async def access_token(self):
        return "synthetic-lark-access-token"

    async def lark_run(self, receive):
        await self._transition("connected")
        await asyncio.Event().wait()

    monkeypatch.setattr(connectors.LarkAdapter, "access_token", access_token)
    monkeypatch.setattr(connectors.LarkAdapter, "run", lark_run)
    monkeypatch.setattr(connectors, "check", check)
    # Provider-side verification and CLI downloads are not part of this lifecycle test.
    monkeypatch.setattr(external, "verify", verify)
    monkeypatch.setattr(envd, "connect", connect)
    monkeypatch.setattr(project_cli, "provision", provision)
    outbound = []

    async def command(_environment, script, arguments, *, variables, timeout):
        assert script == project_cli.REPLY_CLI and timeout == 40
        outbound.append(arguments)
        if arguments[0] == "gh":
            assert variables["GH_TOKEN"] == "synthetic-github-credential"
        else:
            assert variables["LARKSUITE_CLI_TENANT_ACCESS_TOKEN"] == "synthetic-lark-access-token"
        error = b""
        if "--text=fail" in arguments:
            error = b"auth rejected synthetic-lark-access-token and synthetic-lark-app-secret"
        return SimpleNamespace(
            status=SimpleNamespace(exit_code=1 if error else 0),
            output=SimpleNamespace(
                stdout=SimpleNamespace(inline=b"https://example.com/reply"),
                stderr=SimpleNamespace(inline=error),
            ),
        )

    monkeypatch.setattr(project_cli, "command", command)
    model_url, requests, reply, tool_results, unbound_tools = model_server
    origin = "https://claw.test"
    with scratch_database(Database(url=database_url)) as database:
        settings = Settings.model_validate(
            {
                "server": {"public_url": origin, "readiness_timeout": 10},
                "database": database.model_copy(update={"auto_migrate": True}),
                "redis": {"url": redis_url},
                "encryption": {"key_file": tmp_path / "key"},
                "objects": {"root": tmp_path / "objects"},
                "providers": {"http_origins": [model_url]},
                "plugins": {"bootstrap_enabled_apps": ["a13n.claw"]},
            }
        )
        app = build_app(role="all", settings=settings)
        with TestClient(app, base_url=origin, headers={"Origin": origin}) as client:
            exercise_lifecycle(
                client,
                model_url,
                requests,
                reply,
                outbound,
                tool_results,
                unbound_tools,
                monkeypatch,
            )
        assert maintenance == [False, True]


def exercise_lifecycle(
    client, model_url, requests, reply, outbound, tool_results, unbound_tools, monkeypatch
):
    runtime = client.app.state.runtime

    def read(path):
        result = client.get(f"/api/v1{path}")
        assert result.status_code == 200, result.text
        return result.json()

    def post(path, body, status=201, **kwargs):
        result = client.post(f"/api/v1{path}", json=body, **kwargs)
        assert result.status_code == status, result.text
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
    apps = read(f"/organizations/{org}/app-plugins")["items"]
    assert next(app for app in apps if app["app_id"] == "a13n.claw")["enabled"]
    accounts = f"/workspaces/{ws}/service-accounts"
    account = post(accounts, {"name": "Claw test", "role": "builder"})
    key = post(f"{accounts}/{account['id']}/keys", {"name": "Fixture"})
    provider = post(
        "/model-providers",
        {
            "name": "Scripted provider",
            "type": "openai",
            "config": {"base_url": f"{model_url}/v1"},
            "credential": {"api_key": "fixture-not-a-secret"},
        },
    )
    model = post(
        "/models",
        {
            "name": "Scripted model",
            "key": "scripted",
            "provider_id": provider["id"],
            "config": {"model_api": "openai.chat_completions", "model_name": "scripted"},
        },
    )
    agent = post(
        "/agents",
        {"name": "Test Agent", "config": {"model": model["key"], "user_questions": True}},
    )
    target = {"endpoint": "https://fixture.example.test", "token": "fixture-not-a-secret"}
    private = post("/environments", {**target, "name": "Operator device"})
    shared = post(
        "/environments",
        {**target, "name": "Claw account device"},
        headers={"Authorization": f"Bearer {key['secret']}"},
    )
    base = "/apps/a13n.claw"
    choices = read(f"{base}/project-resources/environment?account_id={account['id']}")["items"]
    assert [item["id"] for item in choices] == [shared["id"]]
    config = {
        "name": "Test Claw",
        "agent_id": agent["id"],
        "service_account_id": account["id"],
        "heartbeat_seconds": 0,
    }
    post(f"{base}/claws", {**config, "environment_id": private["id"]}, 403)
    claw = post(f"{base}/claws", config)
    assert claw["environment_id"] is None
    path = f"{base}/claws/{claw['id']}"
    agent_path = f"/agents/{agent['id']}"
    head = client.get(f"/api/v1{agent_path}")
    assert head.json()["default_revision_id"] == agent["default_revision_id"]
    assert claw["agent_revision_id"] != agent["default_revision_id"]
    revision = read(f"{agent_path}/revisions/{claw['agent_revision_id']}")
    assert revision["config"]["summarize"]
    assert not read(path)["agent_revision_outdated"]
    post(
        f"{agent_path}/revisions",
        {"config": {"model": model["key"], "instructions": "New default"}},
        headers={"If-Match": head.headers["ETag"]},
    )
    assert read(f"{path}/schedules")["items"] == []
    headers = {"Idempotency-Key": uuid4().hex}
    message = {"content": "Test Inbox message"}
    received = post(f"{path}/input", message, headers=headers)
    replayed = post(f"{path}/input", message, 200, headers=headers)
    assert replayed == {**received, "status": "duplicate"}
    post(f"{path}/input", {"content": "Changed"}, 409, headers=headers)
    setup = read(path)
    assert setup["main"] is None and setup["unread"] == 1
    assert setup["project"]["environment"] == "not configured"
    assert setup["agent_revision_outdated"]
    (unread,) = read(f"{path}/deliveries?inbox_state=unread")["items"]
    assert unread["preview"] == message["content"] and unread["kind"] == "user"
    assert unread["read_at"] is None
    post(f"{path}/project/cli-update", {}, 409)
    connector_body = {
        "name": "Fixture GitHub",
        "kind": "github",
        "enabled": True,
        "config": {
            "principal_id": account["id"],
            "channels": ["team/repo"],
            "senders": ["operator"],
        },
        "credential": "synthetic-github-credential",
    }
    connector = post(f"{path}/connectors", connector_body)
    connector_path = f"{path}/connectors/{connector['id']}"
    assert connector["credential_configured"] and "credential" not in connector
    assert "synthetic-github-credential" not in json.dumps(read(connector_path))
    bad = client.put(f"/api/v1{connector_path}", json={**connector_body, "body-error": True})
    assert bad.status_code == 400 and "synthetic-github-credential" not in bad.text
    assert post(f"{connector_path}/check", {}, 200) == {"state": "verified"}
    assert post(
        f"{connector_path}/check", {}, 403, headers={"Authorization": f"Bearer {key['secret']}"}
    )

    refused = client.patch(f"/api/v1{path}/project", json={"environment_id": private["id"]})
    assert refused.status_code == 403, refused.text

    # Commit the binding, but fail inside the native admission transaction at the
    # Claw writeback. Service shares this transaction: no orphan Run can commit.
    class WritebackFailed(Exception):
        pass

    original_link = main_thread.link_delivery
    rejected_threads = []

    async def fail_link(session, current, delivery_id, run_id):
        if current.id == claw["id"] and current.main_thread_id and not rejected_threads:
            rejected_threads.append(current.main_thread_id)
            assert await session.get(RunRow, run_id) is not None
            async with short_session(runtime.storage) as observer:
                assert await observer.get(ThreadRow, current.main_thread_id) is None
                assert await observer.get(RunRow, run_id) is None
            raise WritebackFailed
        return await original_link(session, current, delivery_id, run_id)

    with monkeypatch.context() as patch:
        patch.setattr(main_thread, "link_delivery", fail_link)
        with pytest.raises(WritebackFailed):
            client.patch(f"/api/v1{path}/project", json={"environment_id": shared["id"]})
    assert rejected_threads
    assert read(path)["claw"]["main_thread_id"] is None
    assert client.get(f"/api/v1/threads/{rejected_threads[0]}").status_code == 404
    assert read(f"{path}/deliveries/{received['delivery_id']}")["run_id"] is None

    original_assign = workers.assign
    replayed_assignments = []

    async def replay_assign(run_runtime, binding, task, call_key, **kwargs):
        original_accept = workers.accept
        rolled_back = []

        async def fail_worker_writeback(session, service, thread, *, explicit):
            await original_accept(session, service, thread, explicit=explicit)
            assert explicit.entry is not None and explicit.entry.assigned_run_id is not None
            assert await session.get(Worker, thread.id) is not None
            rolled_back.append(thread.id)
            raise WritebackFailed

        with monkeypatch.context() as patch:
            patch.setattr(workers, "accept", fail_worker_writeback)
            with pytest.raises(WritebackFailed):
                await original_assign(run_runtime, binding, task, call_key, **kwargs)
        async with short_session(run_runtime.storage) as session:
            (rejected_worker,) = rolled_back
            assert await session.get(Worker, rejected_worker) is None
            assert await session.get(ThreadRow, rejected_worker) is None
            assert await find_request(session, ws, account["id"], call_key) is None
        first = await original_assign(run_runtime, binding, task, call_key, **kwargs)
        async with transaction(run_runtime.storage) as session:
            row = await session.get_one(Claw, binding.claw.id)
            pinned = row.agent_revision_id
            row.agent_revision_id = agent["default_revision_id"]
        try:
            replay = await original_assign(run_runtime, binding, task, call_key, **kwargs)
            assert replay["entry_id"] == first["entry_id"]
            assert replay["thread_id"] == first["thread_id"]
            async with short_session(run_runtime.storage) as session:
                entry = await find_request(session, ws, account["id"], call_key)
                assert entry is not None and entry.id == replay["entry_id"]
            replayed_assignments.append(replay["entry_id"])
        finally:
            async with transaction(run_runtime.storage) as session:
                row = await session.get_one(Claw, binding.claw.id)
                row.agent_revision_id = pinned
        return replay

    monkeypatch.setattr(workers, "assign", replay_assign)
    recorded = workers._record_notices
    worker_seals = []

    async def record_from_seal(runtime, workspace_id, claw_id, thread_id, *, event=None):
        if claw_id == claw["id"] and event is None:
            return False  # The real Redis callback, not reconciliation, must record this fixture.
        admitted = await recorded(runtime, workspace_id, claw_id, thread_id, event=event)
        if claw_id == claw["id"] and event is not None and admitted:
            worker_seals.append(event)
        return admitted

    monkeypatch.setattr(workers, "_record_notices", record_from_seal)
    client.portal.call(
        lambda: main_thread.notify(runtime, ws, claw["id"], delivery_id=received["delivery_id"])
    )
    assert read(path)["claw"]["memory_space_id"] == claw["memory_space_id"]
    overview = eventually(
        lambda: read(path), lambda row: row["main"] and row["main"]["last_run_id"]
    )
    main_id = read(f"{path}/deliveries/{received['delivery_id']}")["run_id"]
    assert main_id
    main = eventually(lambda: read(f"/runs/{main_id}"), lambda run: run["sealed_at"])
    assert main["thread_id"] == overview["claw"]["main_thread_id"]
    assert main["session_id"] == overview["claw"]["main_session_id"]
    assert main["status"] == "completed", main.get("failure")
    assert main["agent_revision_id"] == claw["agent_revision_id"]
    assert len(replayed_assignments) == 1
    (schedule,) = read(f"{path}/fresh-schedules")["items"]
    assert schedule["name"] == "Fresh follow-up" and schedule["next_at"]
    schedule_path = f"/schedules/{schedule['id']}"
    native = client.get(f"/api/v1{schedule_path}")
    assert native.status_code == 200, native.text
    assert native.json()["invocation"]["agent_revision_id"] == claw["agent_revision_id"]
    occ = post(
        f"{schedule_path}/dispatch",
        {},
        202,
        headers={
            "Idempotency-Key": uuid4().hex,
            "Authorization": f"Bearer {key['secret']}",
            "If-Match": native.headers["ETag"],
        },
    )
    scheduled = eventually(
        lambda: read(f"{schedule_path}/occurrences/{occ['id']}"),
        lambda row: row["execution_outcome"],
    )
    assert scheduled["execution_outcome"] == "succeeded", scheduled
    assert scheduled["completion_run_id"] != main["id"]
    observed = read(path)["project"]
    assert observed["environment"] == "ready"
    assert observed["automation_account"] == "active"
    assert observed["cli_versions"] == {"gh": "2.105.0", "lark-cli": "1.0.100"}
    assert observed["cli_update_state"] is None
    worker_rows = eventually(
        lambda: read(f"{path}/workers")["items"],
        lambda items: items and items[0]["activity"]["last_status"] in {"completed", "failed"},
    )
    (worker,) = worker_rows
    assert worker["thread_id"] != main["thread_id"]
    assert worker["activity"]["last_status"] == "completed"
    worker_run = read(f"/runs/{worker['activity']['last_run_id']}")
    assert worker_run["agent_revision_id"] == claw["agent_revision_id"]
    worker_seal = eventually(lambda: list(worker_seals), bool)[0]
    assert worker_seal.run_id == worker_run["id"]
    main_seal = RunSealEvent(
        organization_id=worker_seal.organization_id,
        workspace_id=ws,
        session_id=main["session_id"],
        thread_id=main["thread_id"],
        run_id=main_id,
        principal_id=account["id"],
        status="completed",
        labels={"claw": claw["id"], "claw_role": "main"},
    )

    async def followups():
        async with short_session(runtime.storage) as session:
            return list(
                await session.scalars(
                    select(InboxEntryRow.request_key).where(
                        InboxEntryRow.thread_id == main["thread_id"],
                        InboxEntryRow.request_key.startswith(f"claw:seal:{main_id}:"),
                    )
                )
            )

    # Both subscriptions came through the real Redis consumer. Replaying older
    # or duplicate events cannot start an additional Run or duplicate a notice.
    assert len(eventually(lambda: client.portal.call(followups), bool)) == 1
    client.portal.call(app.on_run_seal, runtime, main_seal)
    client.portal.call(app.on_run_seal, runtime, worker_seal)
    assert len(client.portal.call(followups)) == 1

    async def terminal_notices():
        async with short_session(runtime.storage) as session:
            return list(
                await session.scalars(
                    select(InboxItem.id).where(
                        InboxItem.source_key == f"terminal:{worker_seal.run_id}"
                    )
                )
            )

    assert len(client.portal.call(terminal_notices)) == 1
    monkeypatch.setattr(workers, "_record_notices", recorded)
    reports = [item for item in read(f"{path}/deliveries")["items"] if item["origin"] == "worker"]
    assert any(
        read(f"{path}/deliveries/{item['id']}")["content"] == "Worker result" for item in reports
    )
    assert read(f"{path}/usage")[0]["usage"]["requests"] > 0
    assert any("ask_user_question" in tools for tools in requests if "claw_assign" in tools)
    assert all("ask_user_question" not in tools for tools in requests if "claw_report" in tools)

    replacement = post(
        "/environments",
        {**target, "name": "Replacement device"},
        headers={"Authorization": f"Bearer {key['secret']}"},
    )
    update_url = f"/api/v1{path}/project"
    fresh_body = {
        "name": "Operator schedule",
        "prompt": "Later work",
        "rule": {"type": "once", "run_at": (datetime.now(UTC) + timedelta(minutes=15)).isoformat()},
    }
    fresh_headers = {"Idempotency-Key": uuid4().hex}
    fresh_path = f"{path}/fresh-schedules"
    # Fail after native creation commits, then force the recovery by exact native_key.
    original_create = schedules.native_service.create
    committed = []

    async def lose_receipt(*args, **kwargs):
        if not committed:
            native, _ = await original_create(*args, **kwargs)
            committed.append(native.id)
            raise ServiceError("unavailable", "Fixture lost the creation response")
        raise ServiceError("conflict", "Fixture refuses native replay; reconcile the receipt")

    with monkeypatch.context() as patch:
        patch.setattr(schedules.native_service, "create", lose_receipt)
        post(fresh_path, fresh_body, 503, headers=fresh_headers)

        async def pending_receipt():
            async with short_session(runtime.storage) as session:
                row = await session.scalar(
                    select(ScheduleRequest).where(
                        ScheduleRequest.claw_id == claw["id"],
                        ScheduleRequest.completed_at.is_(None),
                    )
                )
                return row and row.schedule_id is None

        assert client.portal.call(pending_receipt)
        operator_schedule = post(fresh_path, fresh_body, headers=fresh_headers)
        assert operator_schedule["id"] == committed[0]
    assert post(fresh_path, fresh_body, headers=fresh_headers)["id"] == operator_schedule["id"]
    post(fresh_path, {**fresh_body, "prompt": "Different work"}, 409, headers=fresh_headers)
    service_headers = {"Authorization": f"Bearer {key['secret']}"}
    operator_path = f"/api/v1/schedules/{operator_schedule['id']}"
    latest = client.get(operator_path)
    renamed = client.patch(
        operator_path,
        json={"labels": {}},
        headers={**service_headers, "If-Match": latest.headers["ETag"]},
    )
    assert renamed.status_code == 200, renamed.text
    assert read(path)["project"]["fresh_schedule_count"] == 2
    assert client.delete(f"/api/v1{path}").status_code == 409
    refused = client.patch(update_url, json={"environment_id": replacement["id"]})
    assert refused.status_code == 409, refused.text
    latest = client.get(f"/api/v1{schedule_path}")
    archived = client.delete(
        f"/api/v1{schedule_path}",
        headers={"Authorization": f"Bearer {key['secret']}", "If-Match": latest.headers["ETag"]},
    )
    assert archived.status_code == 204, archived.text
    blocked_by_operator_schedule = client.patch(
        update_url, json={"environment_id": replacement["id"]}
    )
    assert blocked_by_operator_schedule.status_code == 409, blocked_by_operator_schedule.text
    archived = client.delete(
        operator_path, headers={**service_headers, "If-Match": renamed.headers["ETag"]}
    )
    assert archived.status_code == 204, archived.text
    updated = client.patch(update_url, json={"environment_id": replacement["id"]})
    assert updated.status_code == 200, updated.text
    assert updated.json()["main_thread_id"] == main["thread_id"]
    assert read(path)["project"]["environment_name"] == replacement["name"]
    assert read(path)["project"]["fresh_schedule_count"] == 0
    rejected = client.put(
        f"/api/v1{connector_path}", json={**connector_body, "reply_environment_id": shared["id"]}
    )
    assert rejected.status_code == 409, rejected.text
    enabled = client.put(
        f"/api/v1{connector_path}",
        json={
            **{key: value for key, value in connector_body.items() if key != "credential"},
            "reply_environment_id": replacement["id"],
        },
    )
    assert enabled.status_code == 200 and enabled.json()["credential_configured"], enabled.text
    assert enabled.json()["reply_environment_id"] == replacement["id"]
    assert post(f"{path}/project/cli-update", {}, 202) == {"state": "queued"}
    assert read(path)["project"]["cli_update_state"] == "queued"

    for thread_id, run_id in (
        (main["thread_id"], main["id"]),
        (worker["thread_id"], worker_run["id"]),
    ):
        mounts = read(f"/threads/{thread_id}/environments")["items"]
        assert (
            next(m["environment_id"] for m in mounts if m["name"] == "workspace")
            == replacement["id"]
        )
        old = read(f"/runs/{run_id}")
        assert (
            next(m["environment_id"] for m in old["environment_mounts"] if m["name"] == "workspace")
            == shared["id"]
        )

    provider = post(
        "/browser-providers",
        {
            "name": "Reserved test Browser provider",
            "type": "browserbase",
            "config": {"project_id": "test-project"},
            "credential": {"api_key": "fixture-not-a-secret"},
        },
    )
    browser = post("/browsers", {"name": "Project Browser", "provider_id": provider["id"]})
    assert browser["status"] == "reserved"
    attached = client.patch(update_url, json={"browser_id": browser["id"]})
    assert attached.status_code == 200, attached.text
    assert read(path)["project"]["browser_name"] == browser["name"]
    assert post(fresh_path, fresh_body, headers=fresh_headers)["id"] == operator_schedule["id"]
    scheduled_browser = post(fresh_path, fresh_body, headers={"Idempotency-Key": uuid4().hex})
    browser_schedule_path = f"/schedules/{scheduled_browser['id']}"
    native_browser_schedule = client.get(f"/api/v1{browser_schedule_path}")
    assert native_browser_schedule.status_code == 200, native_browser_schedule.text
    invocation = native_browser_schedule.json()["invocation"]
    assert invocation["browser_binding"]["browser_id"] == browser["id"]
    assert invocation["environments"][0]["environment_id"] == replacement["id"]
    browser_occurrence = post(
        f"{browser_schedule_path}/dispatch",
        {},
        202,
        headers={
            **service_headers,
            "Idempotency-Key": uuid4().hex,
            "If-Match": native_browser_schedule.headers["ETag"],
        },
    )
    browser_result = eventually(
        lambda: read(f"{browser_schedule_path}/occurrences/{browser_occurrence['id']}"),
        lambda row: row["execution_outcome"],
    )
    assert browser_result["execution_outcome"] == "succeeded", browser_result
    browser_run = read(f"/runs/{browser_result['completion_run_id']}")
    assert browser_run["browser_binding"]["browser_id"] == browser["id"]
    assert browser_run["environment_mounts"][0]["environment_id"] == replacement["id"]
    native_browser_schedule = client.get(f"/api/v1{browser_schedule_path}")
    blocked_detach = client.patch(update_url, json={"browser_id": None})
    assert blocked_detach.status_code == 409, blocked_detach.text
    archived = client.delete(
        f"/api/v1{browser_schedule_path}",
        headers={**service_headers, "If-Match": native_browser_schedule.headers["ETag"]},
    )
    assert archived.status_code == 204, archived.text
    for thread_id in (main["thread_id"], worker["thread_id"]):
        assert read(f"/threads/{thread_id}/browser")["browser_id"] == browser["id"]

    async def subscribers():
        return (await runtime.redis.pubsub_numsub(connectors.LARK_CHANGES))[0][1]

    eventually(lambda: client.portal.call(subscribers), lambda count: count >= 1, timeout=4)
    lark_body = {
        "name": "Fixture Lark",
        "kind": "lark",
        "enabled": True,
        "config": {
            "app_id": f"cli_{uuid4().hex}",
            "bot_open_id": "ou_bot",
            "senders": ["ou_operator"],
        },
        "credential": "synthetic-lark-app-secret",
        "reply_environment_id": replacement["id"],
    }

    lark = post(f"{path}/connectors", lark_body)
    lark_path = f"{path}/connectors/{lark['id']}"
    eventually(
        lambda: read(lark_path), lambda row: row["listener_status"] == "connected", timeout=4
    )
    replica = client.portal.call(lambda: connectors.Listeners(runtime))
    eventually(lambda: client.portal.call(subscribers), lambda count: count >= 2, timeout=4)
    duplicate = post(
        f"{path}/connectors", {**lark_body, "name": "Duplicate Lark", "reply_environment_id": None}
    )
    duplicate_path = f"{path}/connectors/{duplicate['id']}"
    eventually(
        lambda: read(duplicate_path), lambda row: row["listener_status"] == "conflict", timeout=4
    )
    assert read(lark_path)["listener_status"] == "connected"
    assert duplicate["credential_configured"]
    client.portal.call(replica.close)

    async def admit_opened_lark():
        async with transaction(client.app.state.runtime.storage) as session:
            row = await inbox.lock_claw(session, ws, claw["id"])
            admission = await inbox.admit(
                session,
                row,
                inbox.Offer(
                    "user",
                    "lark",
                    connectors.event_key(lark["id"], "evt-fixture"),
                    {
                        "platform": "lark",
                        "message_id": "om_fixture",
                        "attachments": [
                            {"key": "file_fixture", "kind": "file", "name": "fixture.txt"}
                        ],
                    },
                    "Fixture source",
                    "Fixture source",
                ),
            )
            item = await session.get(InboxItem, admission.item_id)
            item.read_at = datetime.now(UTC)
            return item.id

    item_id = client.portal.call(admit_opened_lark)
    reply.extend(
        [
            dict(
                connector_id=duplicate["id"],
                destination="ou_operator",
                content="refuse",
                operation="lark_send",
            ),
            dict(
                connector_id=connector["id"],
                destination="team/repo#123",
                content="Fixture reply",
                operation="issue_comment",
            ),
            dict(
                connector_id=lark["id"],
                destination="om_fixture",
                content="Fixture reply",
                operation="lark_reply",
                source_item_id=item_id,
            ),
            dict(
                connector_id=lark["id"],
                destination="om_fixture",
                content="file_fixture",
                content_type="file",
                operation="lark_download",
                source_item_id=item_id,
            ),
            dict(
                connector_id=lark["id"],
                destination="ou_operator",
                content="fail",
                operation="lark_send",
            ),
        ]
    )

    followup = post(
        f"{path}/input",
        {"content": "Run with the replacement"},
        headers={"Idempotency-Key": uuid4().hex},
    )
    next_delivery = eventually(
        lambda: read(f"{path}/deliveries/{followup['delivery_id']}"),
        lambda item: item["run_id"] is not None,
    )
    next_run = eventually(
        lambda: read(f"/runs/{next_delivery['run_id']}"), lambda run: run["sealed_at"]
    )
    assert next_run["status"] == "completed", next_run.get("failure")
    assert outbound[0] == (
        "gh",
        "issue",
        "comment",
        "123",
        "--repo",
        "github.com/team/repo",
        "--body=Fixture reply",
    )
    assert [argv[2] for argv in outbound[1:]] == [
        "+messages-reply",
        "+messages-resources-download",
        "+messages-send",
    ]
    diagnostics = " ".join(str(value) for value in tool_results)
    assert "auth rejected [redacted] and [redacted]" in diagnostics
    assert "synthetic-lark-" not in diagnostics
    assert "An admin must authorize replies in this Project Environment" in diagnostics
    assert '"unknown"' in diagnostics

    cleared = client.put(f"/api/v1{lark_path}", json={**lark_body, "credential": None})
    assert cleared.status_code == 200, cleared.text
    assert not cleared.json()["credential_configured"]
    assert post(f"{lark_path}/check", {}, 200)["state"] == "not configured"

    async def has_listener_status():
        return await runtime.redis.exists(connectors.listener_key(lark["id"]))

    eventually(lambda: client.portal.call(has_listener_status), lambda count: count == 0)
    assert client.delete(f"/api/v1{duplicate_path}").status_code == 204
    assert client.get(f"/api/v1{duplicate_path}").status_code == 404

    assert next_run["id"] != main["id"] and next_run["thread_id"] == main["thread_id"]
    assert any(
        mount["name"] == "workspace" and mount["environment_id"] == replacement["id"]
        for mount in next_run["environment_mounts"]
    )

    assert next_run["browser_binding"]["browser_id"] == browser["id"]
    assert read(path)["project"]["cli_update_state"] == "succeeded"
    detached = client.patch(update_url, json={"browser_id": None})
    assert detached.status_code == 200, detached.text
    assert read(path)["project"]["browser"] is None
    for thread_id in (main["thread_id"], worker["thread_id"]):
        assert read(f"/threads/{thread_id}/browser") is None
    assert read(f"/runs/{main['id']}")["browser_binding"] is None
    assert read(f"/runs/{next_run['id']}")["browser_binding"]["browser_id"] == browser["id"]

    synced = post(f"{path}/agent-revision/sync", {}, 200)
    assert synced["agent_revision_id"] != claw["agent_revision_id"]
    assert post(f"{path}/agent-revision/sync", {}, 200) == synced
    assert not read(path)["agent_revision_outdated"]
    assert read(f"{agent_path}/revisions/{synced['agent_revision_id']}")["config"]["summarize"]
    assert read(f"/runs/{main['id']}")["agent_revision_id"] == claw["agent_revision_id"]

    inspectable = post(f"{base}/claws", {**config, "name": "Schedule controls"})
    inspect_path = f"{base}/claws/{inspectable['id']}"

    async def drain_fixture_mail():
        async with transaction(runtime.storage) as session:
            await inbox.lock_claw(session, ws, claw["id"])
            await session.execute(
                update(InboxItem)
                .where(InboxItem.claw_id == claw["id"], InboxItem.read_at.is_(None))
                .values(read_at=datetime.now(UTC))
            )

    client.portal.call(drain_fixture_mail)
    eventually(lambda: read(f"/threads/{main['thread_id']}"), lambda row: not row["current_run_id"])
    expected_usage = read(f"{path}/usage")
    assert sum(row["usage"]["requests"] for row in expected_usage) > 0
    for labels, principal in (
        ({}, {"Authorization": f"Bearer {key['secret']}"}),
        (
            {"claw": inspectable["id"], "claw_role": "main"},
            {"Authorization": f"Bearer {key['secret']}"},
        ),
        ({"claw": claw["id"], "claw_role": "main"}, {}),
    ):
        submitted = post(
            "/threads",
            {
                "agent_id": agent["id"],
                "payload": {"content": [{"type": "text", "text": "Native usage probe"}]},
                "options": {"labels": labels},
            },
            headers={"Idempotency-Key": uuid4().hex, **principal},
        )
        native_run = eventually(
            lambda run_id=submitted["run"]["id"]: read(f"/runs/{run_id}"),
            lambda run: run["sealed_at"],
        )
        assert native_run["status"] == "completed", native_run.get("failure")
    # A principal-owned native Run with Claw labels still has no reply authority
    # without a durable Claw binding. Usage also excludes other identities/Claws.
    assert len(unbound_tools) == 3
    assert all("claw_connector_reply" not in tools for tools in unbound_tools)
    assert read(f"{inspect_path}/usage")
    assert read(f"{path}/usage") == expected_usage
    window = {
        "start": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        "end": datetime.now(UTC).isoformat(),
        "principal_id": account["id"],
    }
    assert client.get("/api/v1/usage/overview", params=window).status_code == 200
    for role in ("viewer", "runner"):
        reader = post(accounts, {"name": f"Usage {role}", "role": role})
        reader_key = post(f"{accounts}/{reader['id']}/keys", {"name": "Usage fixture"})
        reader_headers = {"Authorization": f"Bearer {reader_key['secret']}"}
        dashboard = client.get(f"/api/v1{path}/usage", headers=reader_headers)
        assert dashboard.status_code == 200, dashboard.text
        assert dashboard.json() == expected_usage
        denied = client.get("/api/v1/usage/overview", params=window, headers=reader_headers)
        assert denied.status_code == 403, denied.text

    scheduled_path = f"{inspect_path}/schedules"
    once = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    rule = {"type": "once", "run_at": once}
    body = {"name": "Main tick", "prompt": "Original", "rule": rule, "lateness_seconds": 300}
    tick = post(scheduled_path, body)
    tick_url = f"/api/v1{scheduled_path}/{tick['id']}"
    changed = client.put(tick_url, json={**body, "name": "Edited", "prompt": "Open me"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["name"] == "Edited" and changed.json()["next_at"] == once.replace(
        "+00:00", "Z"
    )

    async def due():
        async with transaction(runtime.storage) as session:
            await session.execute(
                update(Schedule)
                .where(Schedule.id == tick["id"])
                .values(next_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        await schedules.sweep(runtime, ws, inspectable["id"])

    client.portal.call(due)
    (occurrence,) = read(f"{scheduled_path}/{tick['id']}/occurrences")["items"]
    assert occurrence["delivery_id"] and occurrence["run_id"] is None
    delivery = read(f"{inspect_path}/deliveries/{occurrence['delivery_id']}")
    assert delivery["content"] == "Open me" and delivery["kind"] == "schedule"
    assert client.delete(tick_url).status_code == 204
    assert read(scheduled_path)["items"] == []
    assert read(f"{scheduled_path}/{tick['id']}/occurrences")["items"] == [occurrence]
    assert client.patch(tick_url, json={"enabled": True}).status_code == 404

    # Apply retention after a delivery has already been accepted; do not age its receipt.
    async def mark_read(at):
        async with transaction(runtime.storage) as session:
            item = await session.get_one(InboxItem, delivery["item_id"])
            item.read_at = at

    client.portal.call(mark_read, datetime.now(UTC))
    (read_item,) = read(f"{inspect_path}/deliveries?inbox_state=read")["items"]
    assert read_item["read_at"] and read_item["preview"] == delivery["preview"]
    client.portal.call(mark_read, datetime.now(UTC) - inbox.HISTORY_AGE - timedelta(seconds=1))
    (expired,) = read(f"{inspect_path}/deliveries?inbox_state=expired")["items"]
    assert expired["id"] == delivery["id"] and expired["preview"] is None
    assert client.delete(f"/api/v1{inspect_path}").status_code == 204
    client.portal.call(inbox.cleanup, runtime.storage, ws, inspectable["id"])

    async def retained():
        async with transaction(runtime.storage) as session:
            return await session.get(InboxItem, delivery["item_id"])

    assert client.portal.call(retained) is None
    assert client.delete(f"/api/v1{path}").status_code == 204
    assert read(f"{base}/claws")["items"] == []
    assert client.get(f"/api/v1{path}").status_code == 404
    post(f"{path}/input", message, 404, headers=headers)
    assert read(f"/runs/{main['id']}")["id"] == main["id"]
    assert read(f"/environments/{replacement['id']}")["id"] == replacement["id"]
