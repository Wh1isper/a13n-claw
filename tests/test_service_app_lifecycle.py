"""Run Service and its native worker against disposable PostgreSQL and Redis."""

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from dataclasses import replace
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
from a13n_service.runs.tables import RunRow, ThreadRow
from a13n_service.settings import Database, Settings
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from a13n_claw.service_app import (
    app as claw_app,
)
from a13n_claw.service_app import (
    connectors,
    inbox,
    main_thread,
    project_cli,
    schedules,
    supervisor,
)
from a13n_claw.service_app.models import InboxItem, Occurrence, Schedule


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
            if "claw_report" in tools - used:
                tool, args = "claw_report", {"message": "Scheduled result for Main"}
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
    # Drive periodic sweeps explicitly so background ticks do not race rollback or notify checks.
    real_definition = claw_app.definition
    monkeypatch.setattr(claw_app, "definition", lambda: replace(real_definition(), sweeps=()))
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
                tool_results,
                unbound_tools,
                monkeypatch,
            )
        assert maintenance[0] is False and maintenance[-1] is True
        assert maintenance.count(True) == 1


def exercise_lifecycle(
    client, model_url, requests, reply, tool_results, unbound_tools, monkeypatch
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
    for role in ("runner", "admin"):
        wrong_account = post(accounts, {"name": f"Wrong {role}", "role": role})
        refused = post(f"{base}/claws", {**config, "service_account_id": wrong_account["id"]}, 409)
        assert refused["error"]["details"]["reason"] == "claw_builder_required"
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

    async def exercise_environment_hook():
        from a13n_claw.service_app.models import Claw

        context = SimpleNamespace(
            owner_app_id="a13n.claw",
            app_context={"claw_id": claw["id"]},
            environment_id=shared["id"],
            workspace_id=ws,
            execution=FixtureEnvironment(shared["id"]),
        )
        calls = []

        async def install(_execution, *, update, install_timeout):
            calls.append((update, install_timeout))
            return project_cli.Maintenance({"gh": "fixture", "lark-cli": "fixture"}, False)

        async with transaction(runtime.storage) as session:
            (await session.get_one(Claw, claw["id"])).cli_versions = {}
        with monkeypatch.context() as patch:
            patch.setattr(project_cli, "provision", install)
            await claw_app.on_environment_created(context)
            async with short_session(runtime.storage) as session:
                assert (await session.get_one(Claw, claw["id"])).cli_versions == {
                    "gh": "fixture",
                    "lark-cli": "fixture",
                }
            assert calls == [(False, 18)]
            context.environment_id = "different"
            await claw_app.on_environment_created(context)
            context.owner_app_id = "other.app"
            context.environment_id = shared["id"]
            await claw_app.on_environment_created(context)
            assert len(calls) == 1
            context.owner_app_id = "a13n.claw"
            async with transaction(runtime.storage) as session:
                (await session.get_one(Claw, claw["id"])).cli_versions = {}

            async def failed(_execution, *, update, install_timeout):
                return project_cli.Maintenance({}, True, "fixture failure")

            patch.setattr(project_cli, "provision", failed)
            await claw_app.on_environment_created(context)
            async with short_session(runtime.storage) as session:
                assert not (await session.get_one(Claw, claw["id"])).cli_versions

            async def rebound(_execution, *, update, install_timeout):
                async with transaction(runtime.storage) as session:
                    (await session.get_one(Claw, claw["id"])).environment_id = None
                return project_cli.Maintenance({"gh": "wrong"}, False)

            patch.setattr(project_cli, "provision", rebound)
            await claw_app.on_environment_created(context)
            async with transaction(runtime.storage) as session:
                row = await session.get_one(Claw, claw["id"])
                assert not row.cli_versions
                row.environment_id = shared["id"]
                row.archived_at = datetime.now(UTC)
            await claw_app.on_environment_created(context)
            async with transaction(runtime.storage) as session:
                row = await session.get_one(Claw, claw["id"])
                assert not row.cli_versions
                row.archived_at = None
        async with transaction(runtime.storage) as session:
            (await session.get_one(Claw, claw["id"])).cli_versions = {
                "gh": "2.105.0",
                "lark-cli": "1.0.100",
            }

    client.portal.call(exercise_environment_hook)

    async def provenance():
        from a13n_service.resources.memories.tables import MemorySpaceRow
        from a13n_service.runs.tables import SessionRow

        async with short_session(runtime.storage) as session:
            rows = (
                await session.get_one(MemorySpaceRow, claw["memory_space_id"]),
                await session.get_one(SessionRow, main["session_id"]),
                await session.get_one(ThreadRow, main["thread_id"]),
                await session.get_one(RunRow, main["id"]),
            )
            assert rows[-1].trigger_app_id == "a13n.claw"
            return [(row.app_id, row.app_context) for row in rows]

    assert client.portal.call(provenance) == [("a13n.claw", {"claw_id": claw["id"]})] * 4
    (schedule,) = read(f"{path}/schedules?target=fresh")["items"]
    assert schedule["name"] == "Fresh follow-up" and schedule["enabled"]
    schedule_path = f"{path}/schedules/{schedule['id']}"
    trigger_key = {"Idempotency-Key": uuid4().hex}
    occ = post(f"{schedule_path}/trigger", {}, 200, headers=trigger_key)

    async def refused_admission(*_args, **_kwargs):
        raise ServiceError("unavailable", "synthetic account refusal")

    with monkeypatch.context() as patch:
        patch.setattr(schedules.app_threads, "prepare_admission", refused_admission)
        replay = post(f"{schedule_path}/trigger", {}, 200, headers=trigger_key)
        assert replay["id"] == occ["id"] and replay["run_id"] == occ["run_id"]
        post(f"{schedule_path}/trigger", {}, 503, headers={"Idempotency-Key": uuid4().hex})
    scheduled = eventually(
        lambda: read(f"{schedule_path}/occurrences"),
        lambda page: page["items"] and page["items"][0]["status"] == "completed",
    )["items"][0]
    assert scheduled["id"] == occ["id"]
    assert scheduled["thread_id"] != main["thread_id"]
    assert scheduled["session_id"]
    assert scheduled["run_id"] != main["id"]
    reports = eventually(
        lambda: read(f"{path}/deliveries"),
        lambda page: any(item.get("origin") == "worker" for item in page["items"]),
    )
    assert any(item.get("origin") == "worker" for item in reports["items"])

    async def captured_execution():
        async with short_session(runtime.storage) as session:
            main_run = await session.get_one(RunRow, main["id"])
            fresh_run = await session.get_one(RunRow, scheduled["run_id"])
            return [
                (main_run.principal_id, main_run.authority),
                (fresh_run.principal_id, fresh_run.authority),
            ]

    for principal_id, authority in client.portal.call(captured_execution):
        assert principal_id == account["id"]
        assert authority["principal_id"] == account["id"]
        assert set(authority["verbs"]) == {"read", "run", "write"}
    observed = read(path)["project"]
    assert observed["environment"] == "ready"
    assert observed["automation_account"] == "active"
    assert observed["cli_versions"] == {"gh": "2.105.0", "lark-cli": "1.0.100"}
    assert observed["cli_update_state"] is None
    assert read(f"{path}/usage")[0]["usage"]["requests"] > 0
    assert all(
        "claw_assign" not in tools and "claw_connector_reply" not in tools for tools in requests
    )

    replacement = post(
        "/environments",
        {**target, "name": "Replacement device"},
        headers={"Authorization": f"Bearer {key['secret']}"},
    )
    update_url = f"/api/v1{path}/project"
    fresh_body = {
        "name": "Operator schedule",
        "prompt": "Later work",
        "target": "fresh",
        "rule": {"type": "once", "run_at": (datetime.now(UTC) + timedelta(minutes=15)).isoformat()},
    }
    fresh_headers = {"Idempotency-Key": uuid4().hex}
    fresh_path = f"{path}/schedules"
    operator_schedule = post(fresh_path, fresh_body, headers=fresh_headers)
    assert post(fresh_path, fresh_body, headers=fresh_headers)["id"] == operator_schedule["id"]
    post(fresh_path, {**fresh_body, "prompt": "Different work"}, 409, headers=fresh_headers)
    updated = eventually(
        lambda: client.patch(update_url, json={"environment_id": replacement["id"]}),
        lambda response: response.status_code != 409,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["main_thread_id"] == main["thread_id"]
    assert read(path)["project"]["environment_name"] == replacement["name"]
    enabled = client.put(
        f"/api/v1{connector_path}",
        json={key: value for key, value in connector_body.items() if key != "credential"},
    )
    assert enabled.status_code == 200 and enabled.json()["credential_configured"], enabled.text
    egress_body = {
        **{key: value for key, value in connector_body.items() if key != "credential"},
        "egress_primary": True,
    }
    for stale_environment in (None, shared["id"]):
        rejected = client.put(
            f"/api/v1{connector_path}",
            json={**egress_body, "egress_environment_id": stale_environment},
        )
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["error"]["details"]["reason"] == "egress_environment_changed"
    authorized = client.put(
        f"/api/v1{connector_path}",
        json={**egress_body, "egress_environment_id": replacement["id"]},
    )
    assert authorized.status_code == 200 and authorized.json()["egress_primary"], authorized.text
    clear = client.put(
        f"/api/v1{connector_path}",
        json={**egress_body, "egress_environment_id": replacement["id"], "credential": None},
    )
    assert clear.status_code == 200 and not clear.json()["credential_configured"], clear.text
    restored = client.put(
        f"/api/v1{connector_path}",
        json={
            **egress_body,
            "egress_environment_id": replacement["id"],
            "credential": "synthetic-github-credential",
        },
    )
    assert restored.status_code == 200 and restored.json()["credential_configured"], restored.text

    async def active_generation():
        from a13n_claw.service_app.models import ConnectorEgress

        async with short_session(runtime.storage) as session:
            return await session.scalar(
                select(ConnectorEgress.id).where(
                    ConnectorEgress.connector_id == authorized.json()["id"],
                    ConnectorEgress.state.in_(("pending", "preparing", "ready")),
                )
            )

    assert client.portal.call(active_generation) is not None
    disabled = client.put(f"/api/v1{connector_path}", json={**egress_body, "egress_primary": False})
    assert disabled.status_code == 200, disabled.text
    assert post(f"{path}/project/cli-update", {}, 202) == {"state": "queued"}
    assert read(path)["project"]["cli_update_state"] == "queued"

    for thread_id, run_id in ((main["thread_id"], main["id"]),):
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
    browser_schedule_path = f"{fresh_path}/{scheduled_browser['id']}"
    browser_occurrence = post(
        f"{browser_schedule_path}/trigger",
        {},
        200,
        headers={"Idempotency-Key": uuid4().hex},
    )
    browser_run = eventually(
        lambda: read(f"/runs/{browser_occurrence['run_id']}"),
        lambda run: run["sealed_at"],
    )
    assert browser_run["browser_binding"]["browser_id"] == browser["id"]
    assert browser_run["environment_mounts"][0]["environment_id"] == replacement["id"]
    for thread_id in (main["thread_id"],):
        assert read(f"/threads/{thread_id}/browser")["browser_id"] == browser["id"]
    detached = client.patch(update_url, json={"browser_id": None})
    assert detached.status_code == 200, detached.text
    reattached = client.patch(update_url, json={"browser_id": browser["id"]})
    assert reattached.status_code == 200, reattached.text

    overlapping = post(fresh_path, {**fresh_body, "name": "Overlap guard"})

    async def make_outstanding():
        async with transaction(runtime.storage) as session:
            session.add(
                Occurrence(
                    id="occ_pending_test",
                    schedule_id=overlapping["id"],
                    scheduled_at=datetime.now(UTC) - timedelta(seconds=1),
                    target="fresh",
                    status="processing",
                )
            )

    client.portal.call(make_outstanding)
    overlap_result = post(
        f"{fresh_path}/{overlapping['id']}/trigger",
        {},
        200,
        headers={"Idempotency-Key": uuid4().hex},
    )
    assert overlap_result["status"] == "overlap" and overlap_result["thread_id"] is None
    refused_switch = client.put(
        f"/api/v1{fresh_path}/{overlapping['id']}",
        json={
            "name": "Overlap guard",
            "prompt": "Later work",
            "target": "main",
            "lateness_seconds": 300,
        },
    )
    assert refused_switch.status_code == 409, refused_switch.text

    async def verify_seal():
        async with transaction(runtime.storage) as session:
            session.add(
                Occurrence(
                    id="occ_seal_test",
                    schedule_id=overlapping["id"],
                    scheduled_at=datetime.now(UTC),
                    target="fresh",
                    status="processing",
                    run_id="run_seal_test",
                    thread_id="thread_seal_test",
                    session_id="sess_seal_test",
                )
            )
        event = SimpleNamespace(
            app_id="a13n.claw",
            app_context={"claw_id": claw["id"]},
            labels={"claw": claw["id"], "claw_role": "scheduled"},
            workspace_id=ws,
            principal_id=account["id"],
            run_id="run_seal_test",
            thread_id="thread_seal_test",
            session_id="sess_seal_test",
            status="completed",
        )
        spoofed = SimpleNamespace(**{**vars(event), "app_id": "other.app"})
        await claw_app.on_run_seal(runtime, spoofed)
        async with short_session(runtime.storage) as session:
            assert (await session.get_one(Occurrence, "occ_seal_test")).status == "processing"
        event.labels["claw"] = "spoofed"
        await claw_app.on_run_seal(runtime, event)
        async with short_session(runtime.storage) as session:
            assert (await session.get_one(Occurrence, "occ_seal_test")).status == "completed"

    client.portal.call(verify_seal)

    # A refused native admission rolls its writes back, then advances the exact due slot.
    failed_schedule = post(fresh_path, {**fresh_body, "name": "Admission failure"})
    failed_threads = []
    original_native_create = schedules.app_threads.create_thread_in_transaction

    async def fail_after_native_write(*args, **kwargs):
        thread, _receipt = await original_native_create(*args, **kwargs)
        failed_threads.append(thread.id)
        raise ServiceError("unavailable", "synthetic admission failure")

    async def make_due():
        async with transaction(runtime.storage) as session:
            row = await session.get_one(Schedule, failed_schedule["id"])
            row.next_at = datetime.now(UTC) - timedelta(seconds=2)

    client.portal.call(make_due)
    with monkeypatch.context() as patch:
        patch.setattr(
            schedules.app_threads, "create_thread_in_transaction", fail_after_native_write
        )
        client.portal.call(lambda: schedules.sweep(runtime, ws, claw["id"]))
    failure = read(f"{fresh_path}/{failed_schedule['id']}/occurrences")["items"][0]
    assert failure["status"] == "unavailable" and failure["error"] == "unavailable"
    assert failed_threads and client.get(f"/api/v1/threads/{failed_threads[0]}").status_code == 404
    stopped = read(f"{fresh_path}/{failed_schedule['id']}")
    assert not stopped["enabled"] and stopped["next_at"] is None

    unavailable_schedule = post(fresh_path, {**fresh_body, "name": "Unavailable account"})

    async def make_account_due():
        async with transaction(runtime.storage) as session:
            row = await session.get_one(Schedule, unavailable_schedule["id"])
            row.next_at = datetime.now(UTC) - timedelta(seconds=2)

    client.portal.call(make_account_due)
    with monkeypatch.context() as patch:
        patch.setattr(schedules.app_threads, "prepare_admission", refused_admission)
        client.portal.call(lambda: schedules.sweep(runtime, ws, claw["id"]))
    unavailable = read(f"{fresh_path}/{unavailable_schedule['id']}/occurrences")["items"][0]
    assert unavailable["status"] == "unavailable" and unavailable["error"] == "unavailable"
    unavailable_row = read(f"{fresh_path}/{unavailable_schedule['id']}")
    assert not unavailable_row["enabled"] and unavailable_row["next_at"] is None

    stale_schedule = post(fresh_path, {**fresh_body, "name": "Changed during failure"})

    async def make_stale_due():
        async with transaction(runtime.storage) as session:
            row = await session.get_one(Schedule, stale_schedule["id"])
            row.next_at = datetime.now(UTC) - timedelta(seconds=2)

    client.portal.call(make_stale_due)
    original_transaction = schedules.transaction
    transaction_calls = 0

    @asynccontextmanager
    async def edit_before_fallback(storage):
        nonlocal transaction_calls
        transaction_calls += 1
        if transaction_calls == 2:
            async with original_transaction(storage) as session:
                row = await session.get_one(Schedule, stale_schedule["id"])
                row.prompt = "Edited after the failed admission"
        async with original_transaction(storage) as session:
            yield session

    with monkeypatch.context() as patch:
        patch.setattr(
            schedules.app_threads, "create_thread_in_transaction", fail_after_native_write
        )
        patch.setattr(schedules, "transaction", edit_before_fallback)
        client.portal.call(lambda: schedules.sweep(runtime, ws, claw["id"]))
    stale = read(f"{fresh_path}/{stale_schedule['id']}")
    assert stale["prompt"] == "Edited after the failed admission" and stale["enabled"]
    assert read(f"{fresh_path}/{stale_schedule['id']}/occurrences")["items"] == []
    # Disable this still-due test schedule before the periodic sweep can fire it.
    assert (
        client.patch(
            f"/api/v1{fresh_path}/{stale_schedule['id']}", json={"enabled": False}
        ).status_code
        == 200
    )

    async def subscribers():
        return (await runtime.redis.pubsub_numsub(connectors.CONNECTOR_CHANGES))[0][1]

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
    }

    lark = post(f"{path}/connectors", lark_body)
    lark_path = f"{path}/connectors/{lark['id']}"
    eventually(
        lambda: read(lark_path), lambda row: row["listener_status"] == "connected", timeout=4
    )
    replica = client.portal.call(lambda: supervisor.Listeners(runtime))
    eventually(lambda: client.portal.call(subscribers), lambda count: count >= 2, timeout=4)
    duplicate = post(f"{path}/connectors", {**lark_body, "name": "Duplicate Lark"})
    duplicate_path = f"{path}/connectors/{duplicate['id']}"
    eventually(
        lambda: read(duplicate_path), lambda row: row["listener_status"] == "conflict", timeout=4
    )
    assert read(lark_path)["listener_status"] == "connected"
    assert duplicate["credential_configured"]
    client.portal.call(replica.close)

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
    for thread_id in (main["thread_id"],):
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

    manual = post(scheduled_path, {**body, "name": "Manual"})

    async def trigger_manual():
        async with transaction(runtime.storage) as session:
            locked = await inbox.lock_claw(session, ws, inspectable["id"])
            first = await schedules.trigger(
                session,
                locked,
                manual["id"],
                occurrence_id="ct_first",
                runtime=runtime,
                admission=None,
            )
            duplicate = await schedules.trigger(
                session,
                locked,
                manual["id"],
                occurrence_id="ct_first",
                runtime=runtime,
                admission=None,
            )
            overlap = await schedules.trigger(
                session,
                locked,
                manual["id"],
                occurrence_id="ct_second",
                runtime=runtime,
                admission=None,
            )
            row = await schedules.find_schedule(session, locked, manual["id"])
            return first.id, duplicate.id, first.status, overlap.status, row.next_at.isoformat()

    first_id, duplicate_id, first_status, overlap_status, next_at = client.portal.call(
        trigger_manual
    )
    assert first_id == duplicate_id == "ct_first"
    assert (first_status, overlap_status) == ("unread", "overlap")
    assert next_at == datetime.fromisoformat(once).isoformat()

    http_schedule = post(scheduled_path, {**body, "name": "HTTP trigger"})
    notified = []

    async def notified_after_commit(runtime, workspace_id, claw_id):
        async with short_session(runtime.storage) as session:
            from a13n_claw.service_app.models import Occurrence

            row = await session.scalar(
                select(Occurrence).where(Occurrence.schedule_id == http_schedule["id"])
            )
            notified.append((workspace_id, claw_id, row.status if row else None))

    trigger_headers = {"Idempotency-Key": uuid4().hex}
    with monkeypatch.context() as patch:
        patch.setattr(main_thread, "notify", notified_after_commit)
        first_http = post(
            f"{scheduled_path}/{http_schedule['id']}/trigger", {}, 200, headers=trigger_headers
        )
        replay_http = post(
            f"{scheduled_path}/{http_schedule['id']}/trigger", {}, 200, headers=trigger_headers
        )
    assert first_http["id"] == replay_http["id"] and first_http["status"] == "unread"
    assert notified == [(ws, inspectable["id"], "unread")] * 2

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

    # Reproduce two missed terminal updates from real Fresh Runs across archive:
    # one is repaired by its seal hint, the other by the organization sweep.
    async def pending_at_archive():
        async with transaction(runtime.storage) as session:
            for oid in (occ["id"], browser_occurrence["id"]):
                (await session.get_one(Occurrence, oid)).status = "processing"
            due = await session.get_one(Schedule, operator_schedule["id"])
            due.next_at = datetime.now(UTC) - timedelta(seconds=1)
            return due.next_at

    archived_due = client.portal.call(pending_at_archive)
    assert client.delete(f"/api/v1{path}").status_code == 204

    async def reconcile_after_archive():
        event = SimpleNamespace(
            app_id="a13n.claw",
            app_context={"claw_id": claw["id"]},
            labels={"claw": claw["id"], "claw_role": "scheduled"},
            workspace_id=ws,
            principal_id=account["id"],
            run_id=occ["run_id"],
            thread_id=occ["thread_id"],
            session_id=occ["session_id"],
            status="completed",
        )
        await claw_app.on_run_seal(runtime, event)
        async with short_session(runtime.storage) as session:
            sealed = await session.get_one(Occurrence, occ["id"])
            awaiting_sweep = await session.get_one(Occurrence, browser_occurrence["id"])
            assert sealed.status == "completed" and awaiting_sweep.status == "processing"
        await claw_app.sweep(runtime, org)
        async with short_session(runtime.storage) as session:
            sealed = await session.get_one(Occurrence, occ["id"])
            recovered = await session.get_one(Occurrence, browser_occurrence["id"])
            native = await session.get_one(RunRow, browser_occurrence["run_id"])
            due = await session.get_one(Schedule, operator_schedule["id"])
            recorded = (
                await session.scalars(select(Occurrence.id).where(Occurrence.schedule_id == due.id))
            ).all()
            return sealed.status, recovered.status, native.status, due.next_at, recorded

    sealed, recovered, native_status, after_archive_due, recorded = client.portal.call(
        reconcile_after_archive
    )
    assert sealed == "completed" and recovered == native_status
    assert after_archive_due == archived_due and not recorded
    assert read(f"{base}/claws")["items"] == []
    assert client.get(f"/api/v1{path}").status_code == 404
    post(f"{path}/input", message, 404, headers=headers)
    assert read(f"/runs/{main['id']}")["id"] == main["id"]
    assert read(f"/environments/{replacement['id']}")["id"] == replacement["id"]


def test_execution_command_shell_facet():
    from a13n_harness.environment.models import EnvironmentError

    async def run():
        environment = FixtureEnvironment("env-fixture")
        with pytest.raises(EnvironmentError) as error:
            await project_cli.command(environment, "echo test")
        assert error.value.code == "environment_unsupported"
        observed = []

        async def execute(request):
            observed.append(request)
            return "fixture-result"

        environment.operations = EnvironmentOperations(shell=SimpleNamespace(exec=execute))
        assert await project_cli.command(environment, "echo test") == "fixture-result"
        assert len(observed) == 1

    asyncio.run(run())
