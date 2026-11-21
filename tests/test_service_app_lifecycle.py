"""Run Service and its native worker against disposable PostgreSQL and Redis."""

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
from a13n_service.migrations.scratch import scratch_database
from a13n_service.providers import envd
from a13n_service.runs.environments import external
from a13n_service.settings import Database, Settings
from fastapi.testclient import TestClient

from a13n_claw.service_app import connectors, project_cli


@pytest.fixture
def model_server():
    requests = []
    reply = {}

    class ModelHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            tools = {tool["function"]["name"] for tool in body.get("tools", [])}
            requests.append(tools)
            used = {
                call["function"]["name"]
                for message in body["messages"]
                for call in message.get("tool_calls", [])
            }
            if reply and "claw_connector_reply" in tools - used:
                tool, args = "claw_connector_reply", reply
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
                            "id": f"call_{tool}",
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
            yield f"http://127.0.0.1:{server.server_port}", requests, reply
        finally:
            server.shutdown()
            thread.join()


def eventually(read, ready):
    deadline = time.monotonic() + 30
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

    async def provision(_environment, *, update=False):
        return project_cli.Maintenance({"gh": "2.105.0", "lark-cli": "1.0.100"}, False)

    async def check(kind, connector_id, config, token):
        assert token == "synthetic-github-credential"

    monkeypatch.setattr(connectors, "check", check)
    # Provider-side verification and CLI downloads are not part of this lifecycle test.
    monkeypatch.setattr(external, "verify", verify)
    monkeypatch.setattr(envd, "connect", connect)
    monkeypatch.setattr(project_cli, "provision", provision)
    outbound = []

    async def command(_environment, script, arguments, *, variables, timeout):
        assert script == project_cli.REPLY_CLI and timeout == 40
        assert variables["GH_TOKEN"] == "synthetic-github-credential"
        outbound.append(arguments)
        return SimpleNamespace(
            status=SimpleNamespace(exit_code=0),
            output=SimpleNamespace(stdout=SimpleNamespace(inline=b"https://example.com/reply")),
        )

    monkeypatch.setattr(project_cli, "command", command)
    model_url, requests, reply = model_server
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
            exercise_lifecycle(client, model_url, requests, reply, outbound)


def exercise_lifecycle(client, model_url, requests, reply, outbound):
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
    bound = client.patch(f"/api/v1{path}/project", json={"environment_id": shared["id"]})
    assert bound.status_code == 200, bound.text
    assert bound.json()["memory_space_id"] == claw["memory_space_id"]
    overview = eventually(
        lambda: read(path), lambda row: row["main"] and row["main"]["last_run_id"]
    )
    main_id = overview["main"]["last_run_id"]
    main = eventually(lambda: read(f"/runs/{main_id}"), lambda run: run["sealed_at"])
    assert main["thread_id"] == overview["claw"]["main_thread_id"]
    assert main["session_id"] == overview["claw"]["main_session_id"]
    assert main["status"] == "completed", main.get("failure")
    assert main["agent_revision_id"] == claw["agent_revision_id"]
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
    workers = eventually(
        lambda: read(f"{path}/workers")["items"],
        lambda items: items and items[0]["activity"]["last_status"] in {"completed", "failed"},
    )
    (worker,) = workers
    assert worker["thread_id"] != main["thread_id"]
    assert worker["activity"]["last_status"] == "completed"
    worker_run = read(f"/runs/{worker['activity']['last_run_id']}")
    assert worker_run["agent_revision_id"] == claw["agent_revision_id"]
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
    operator_schedule = post(fresh_path, fresh_body, headers=fresh_headers)
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

    reply.update(
        connector_id=connector["id"],
        destination="team/repo#123",
        content="Fixture reply",
        operation="issue_comment",
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
    assert outbound == [
        ("gh", "issue", "comment", "123", "--repo", "github.com/team/repo", "--body=Fixture reply")
    ]

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
