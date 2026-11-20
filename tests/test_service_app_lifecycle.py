"""Run Service and its native worker against disposable PostgreSQL and Redis."""

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
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

from a13n_claw.service_app import project_cli


@pytest.fixture
def model_server():
    requests = []

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
            if "claw_assign" in tools - used:
                tool, args = "claw_assign", {"task": "Collect a result", "name": "Test Worker"}
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
            yield f"http://127.0.0.1:{server.server_port}", requests
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

    async def provision(_environment):
        return True

    async def check_accounts(_environment):
        return {"github": "verified", "lark": "not verified"}

    # Provider-side verification and CLI downloads are not part of this lifecycle test.
    monkeypatch.setattr(external, "verify", verify)
    monkeypatch.setattr(envd, "connect", connect)
    monkeypatch.setattr(project_cli, "provision", provision)
    monkeypatch.setattr(project_cli, "check_accounts", check_accounts)
    model_url, requests = model_server
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
            exercise_lifecycle(client, model_url, requests)


def exercise_lifecycle(client, model_url, requests):
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
    claw = post(f"{base}/claws", {**config, "environment_id": shared["id"]})
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
    detail_path = f"{path}/deliveries/{received['delivery_id']}"
    delivery = eventually(lambda: read(detail_path), lambda item: item["run_id"] is not None)
    assert read(f"/threads/{delivery['thread_id']}")["session_id"] == delivery["session_id"]
    main = eventually(lambda: read(f"/runs/{delivery['run_id']}"), lambda run: run["sealed_at"])
    assert main["status"] == "completed", main.get("failure")
    assert main["agent_revision_id"] == claw["agent_revision_id"]
    observed = read(path)["project"]
    assert observed["environment"] == "ready"
    assert observed["automation_account"] == "active"
    assert observed["cli"] == {"github": "verified", "lark": "not verified"}
    assert observed["cli_checked_at"]
    workers = eventually(
        lambda: read(f"{path}/workers")["items"],
        lambda items: items and items[0]["activity"]["last_status"] in {"completed", "failed"},
    )
    (worker,) = workers
    assert worker["thread_id"] != delivery["thread_id"]
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
