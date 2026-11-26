"""GitHub webhook normalization and native Service ingress."""

import hashlib
import hmac
import json
import os
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from a13n_service.app import build_app
from a13n_service.infra.db import transaction
from a13n_service.migrations.scratch import scratch_database
from a13n_service.settings import Database, Settings
from a13n_service.tenancy.tables import OrganizationRow, WorkspaceRow
from fastapi.testclient import TestClient

from a13n_claw.service_app import connectors, main_thread
from a13n_claw.service_app.github_poll import GitHubSettings
from a13n_claw.service_app.github_webhook import normalize
from a13n_claw.service_app.models import Claw, Connector


def payload(event):
    obj = {
        "id": 17,
        "number": 3,
        "body": "hello @Bot",
        "title": "Title",
        "user": {"login": "Alice"},
    }
    base = {"repository": {"full_name": "Owner/Repo"}}
    if event == "issue_comment":
        return base | {"action": "created", "comment": obj, "issue": {"number": 3}}
    if event == "pull_request_review_comment":
        return base | {"action": "created", "comment": obj, "pull_request": {"number": 3}}
    if event == "issues":
        return base | {"action": "opened", "issue": obj}
    if event == "pull_request":
        return base | {"action": "opened", "pull_request": obj}
    return base | {"action": "submitted", "review": obj, "pull_request": {"number": 3}}


@pytest.mark.parametrize(
    "event",
    [
        "issue_comment",
        "pull_request_review_comment",
        "issues",
        "pull_request",
        "pull_request_review",
    ],
)
def test_normalize(event):
    settings = GitHubSettings(
        mode="webhook", bot_login=" Bot ", channels=("owner/repo",), senders=("alice",)
    )
    item = normalize(event, payload(event), settings)
    assert item is not None
    assert item[1].sender == "Alice" and item[1].github_issue_number == 3
    assert (
        normalize(event, payload(event), settings.model_copy(update={"channels": ("other/repo",)}))
        is None
    )
    assert (
        normalize(event, payload(event), settings.model_copy(update={"senders": ("bot",)})) is None
    )
    assert (
        normalize(
            event,
            payload(event),
            settings.model_copy(update={"bot_login": "alice", "senders": ("*",)}),
        )
        is None
    )
    no_mention = payload(event)
    key = {
        "issue_comment": "comment",
        "pull_request_review_comment": "comment",
        "issues": "issue",
        "pull_request": "pull_request",
        "pull_request_review": "review",
    }[event]
    no_mention[key]["body"] = "No mention"
    assert normalize(event, no_mention, settings) is None
    no_mention["action"] = "unsupported"
    assert normalize(event, no_mention, settings) is None


def test_native_webhook(monkeypatch, tmp_path):
    url, redis = os.environ.get("CLAW_TEST_DATABASE_URL"), os.environ.get("A13N_REDIS__URL")
    if not url or not redis:
        pytest.skip("Disposable PostgreSQL and Redis required")

    async def notify(*args, **kwargs):
        pass

    reactions = []

    async def eyes(*args):
        reactions.append(args)

    monkeypatch.setattr(main_thread, "notify", notify)
    monkeypatch.setattr(connectors, "github_eyes", eyes)
    with scratch_database(Database(url=url)) as database:
        settings = Settings.model_validate(
            {
                "database": database.model_copy(update={"auto_migrate": True}),
                "redis": {"url": redis},
                "encryption": {"key_file": tmp_path / "key"},
                "objects": {"root": tmp_path / "objects"},
                "plugins": {"bootstrap_enabled_apps": ["a13n.claw"]},
            }
        )
        with TestClient(build_app(role="all", settings=settings)) as client:
            auth = client.post(
                "/api/v1/auth/bootstrap",
                json={
                    "email": "webhook@example.com",
                    "name": "Admin",
                    "password": "claw-test-password",
                },
            )
            assert auth.status_code == 200, auth.text
            client.headers["X-CSRF-Token"] = auth.json()["csrf_token"]
            org = client.get("/api/v1/organizations").json()["items"][0]["id"]
            ws = client.get(f"/api/v1/organizations/{org}/workspaces").json()["items"][0]["id"]
            client.headers["X-Workspace-ID"] = ws
            claw_id = "clw_" + uuid4().hex

            async def seed():
                async with transaction(client.app.state.runtime.storage) as session:
                    session.add(
                        Claw(
                            id=claw_id,
                            workspace_id=ws,
                            name="Webhook",
                            agent_id="agent",
                            memory_space_id="space",
                            service_account_id="account",
                            unread_limit=20,
                        )
                    )

            client.portal.call(seed)
            path = f"/api/v1/apps/a13n.claw/claws/{claw_id}/connectors"
            body = {
                "name": "GitHub",
                "kind": "github",
                "config": {
                    "mode": "webhook",
                    "bot_login": "bot",
                    "senders": ["alice"],
                    "api_url": "https://github.example/api",
                },
                "webhook_secret": "webhook secret",
                "credential": "fixture-token",
            }
            missing_secret = {k: v for k, v in body.items() if k != "webhook_secret"}
            assert client.post(path, json=missing_secret).status_code == 400
            missing_bot = {**body, "config": {"mode": "webhook", "senders": ["alice"]}}
            assert client.post(path, json=missing_bot).status_code == 400
            response = client.post(path, json=body)
            assert response.status_code == 201, response.text
            row = response.json()
            assert row["webhook_secret_configured"] and "webhook_secret" not in row
            hook = f"/api/v1/app-webhooks/a13n.claw/github/{row['id']}"

            def send(event, data, secret="webhook secret", delivery="one", signature=True):
                raw = json.dumps(data).encode()
                digest = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
                headers = {"X-GitHub-Event": event, "X-GitHub-Delivery": delivery}
                if signature:
                    headers["X-Hub-Signature-256"] = "sha256=" + digest
                return client.post(hook, content=raw, headers=headers)

            operation = client.app.openapi()["paths"][
                "/api/v1/app-webhooks/a13n.claw/github/{connector_id}"
            ]["post"]
            assert not operation.get("security")
            assert (
                client.post(
                    hook + "-missing", content=b"{}", headers={"X-GitHub-Event": "ping"}
                ).status_code
                == 404
            )
            assert client.post(hook, content=b"x" * (1024 * 1024 + 1)).status_code == 413
            invalid_raw = b"{invalid"
            invalid_signature = (
                "sha256=" + hmac.new(b"webhook secret", invalid_raw, hashlib.sha256).hexdigest()
            )
            assert (
                client.post(
                    hook,
                    content=invalid_raw,
                    headers={"X-Hub-Signature-256": invalid_signature, "X-GitHub-Event": "ping"},
                ).status_code
                == 400
            )
            assert (
                client.post(hook, content=b"{}", headers={"X-Hub-Signature-256": "bad"}).status_code
                == 401
            )
            assert send("ping", {}).json() == {"status": "pong"}
            assert send("ping", {}, secret="wrong").status_code == 401
            assert send("ping", {}, signature=False).status_code == 401
            assert send("unhandled", {}).json() == {"status": "ignored"}
            for event in (
                "issue_comment",
                "pull_request_review_comment",
                "issues",
                "pull_request",
                "pull_request_review",
            ):
                result = send(event, payload(event))
                assert result.status_code == 201, result.text
                assert result.json()["status"] == "accepted"
                duplicate = send(event, payload(event), delivery="two")
                assert duplicate.status_code == 200 and duplicate.json()["status"] == "duplicate", (
                    duplicate.text
                )
            assert len(reactions) == 4
            assert all(args[3] == "https://github.example/api" for args in reactions)

            async def change_row(model, identity, **changes):
                async with transaction(client.app.state.runtime.storage) as session:
                    item = await session.get(model, identity)
                    for key, value in changes.items():
                        setattr(item, key, value)

            client.portal.call(lambda: change_row(Connector, row["id"], enabled=False))
            assert send("ping", {}).status_code == 404
            client.portal.call(lambda: change_row(Connector, row["id"], enabled=True))
            old_config = row["config"]
            client.portal.call(
                lambda: change_row(Connector, row["id"], config={**old_config, "mode": "polling"})
            )
            assert send("ping", {}).status_code == 404
            client.portal.call(lambda: change_row(Connector, row["id"], config=old_config))
            client.portal.call(lambda: change_row(Claw, claw_id, archived_at=datetime.now(UTC)))
            assert send("ping", {}).status_code == 404
            client.portal.call(lambda: change_row(Claw, claw_id, archived_at=None))
            client.portal.call(lambda: change_row(WorkspaceRow, ws, archived_at=datetime.now(UTC)))
            assert send("ping", {}).status_code == 404
            client.portal.call(lambda: change_row(WorkspaceRow, ws, archived_at=None))

            async def disable_app():
                async with transaction(client.app.state.runtime.storage) as session:
                    organization = await session.get(OrganizationRow, org)
                    previous = organization.settings
                    organization.settings = {**previous, "app_plugins": {"enabled": []}}
                    return previous

            old_settings = client.portal.call(disable_app)
            assert send("ping", {}).status_code == 404
            client.portal.call(lambda: change_row(OrganizationRow, org, settings=old_settings))
            client.portal.call(lambda: change_row(Claw, claw_id, unread_limit=5))
            more = payload("issue_comment")
            more["comment"]["id"] = 18
            assert send("issue_comment", more).status_code == 409
            client.portal.call(lambda: change_row(Claw, claw_id, unread_limit=20))
            body.pop("webhook_secret")
            updated = client.put(f"{path}/{row['id']}", json=body)
            assert updated.status_code == 200 and updated.json()["webhook_secret_configured"], (
                updated.text
            )
            body["webhook_secret"] = None
            assert client.put(f"{path}/{row['id']}", json=body).status_code == 400


def test_eyes_uses_configured_api_url(monkeypatch):
    import asyncio

    import httpx

    calls = []

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            return httpx.Response(201, request=httpx.Request("POST", url))

    monkeypatch.setattr(connectors.httpx, "AsyncClient", Client)
    message = connectors.ExternalMessage(
        event_id="one",
        channel="owner/repo",
        sender="alice",
        content="@bot",
        github_issue_number=3,
        github_comment_id=17,
    )
    asyncio.run(connectors.github_eyes("con", message, "token", "https://github.example/api"))
    assert calls[0][0] == "https://github.example/api/repos/owner/repo/issues/comments/17/reactions"
    assert calls[0][1]["json"] == {"content": "eyes"}
