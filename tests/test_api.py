"""HTTP acceptance, current authorization and restart over the actual coordinator."""

import time
from contextlib import asynccontextmanager

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.models.function import FunctionModel

from a13n_claw.app import create_app
from a13n_claw.domain import ClawError


@asynccontextmanager
async def models(definition, credential):
    assert credential == "provider-secret-fixture"

    async def model(messages, info):
        yield "Saved through the real application boundary"

    yield FunctionModel(stream_function=model)


def authorize(client: TestClient, tmp_path):
    token = (tmp_path / "data" / "operator.token").read_text().strip()
    client.headers["Authorization"] = f"Bearer {token}"


def configure(client: TestClient):
    response = client.post(
        "/api/resources/import",
        json={
            "resources": [
                {
                    "id": "model",
                    "kind": "model",
                    "expected_version": 0,
                    "content": {
                        "provider": "openai",
                        "model_name": "fixture",
                        "credential_env": "TEST_KEY",
                    },
                },
                {
                    "id": "local",
                    "kind": "environment",
                    "expected_version": 0,
                    "content": {"kind": "local"},
                },
                {
                    "id": "default",
                    "kind": "profile",
                    "expected_version": 0,
                    "content": {"model_id": "model", "environment_id": "local"},
                },
                {
                    "id": "instance",
                    "kind": "defaults",
                    "expected_version": 0,
                    "content": {"profile_id": "default"},
                },
            ]
        },
    )
    assert response.status_code == 200, response.text
    assert (
        client.put(
            "/api/credentials/TEST_KEY", json={"value": "provider-secret-fixture"}
        ).status_code
        == 204
    )


def completed(client: TestClient, run_id: str):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()
        if run["status"] in {"completed", "failed", "cancelled"}:
            assert run["status"] == "completed", run
            return run
        time.sleep(0.02)
    pytest.fail("Run did not reach a durable terminal state")


def test_authenticated_http_execution_reconnect_and_fork(console, tmp_path):
    with TestClient(create_app(models=models)) as client:
        assert client.get("/api/threads").status_code == 401
        authorize(client, tmp_path)
        configure(client)
        info = client.get("/api/instance").json()
        assert info["dispatcher"] == "ready" and info["connectivity"] == "not_probed"
        creation = {"request_id": "create", "title": "Conversation"}
        target = client.post("/api/threads", json=creation).json()
        assert client.post("/api/threads", json=creation).json() == target
        assert client.post("/api/threads", json={**creation, "title": "Changed"}).status_code == 409
        assert client.post("/api/threads", json={"title": "Missing identity"}).status_code == 422
        request = {"request_id": "once", "text": "First message"}
        accepted = client.post(f"/api/threads/{target['id']}/inputs", json=request)
        assert accepted.status_code == 202, accepted.text
        receipt = accepted.json()
        result = completed(client, receipt["run_id"])
        assert "owner" not in result
        assert result["output"] == "Saved through the real application boundary"
        retried = client.post(f"/api/threads/{target['id']}/inputs", json=request).json()
        assert retried["id"] == receipt["id"] and retried["disposition"] == "incorporated"
        history = client.get(f"/api/threads/{target['id']}/history").json()
        assert history["checkpoint_id"] == result["checkpoint_id"] and history["messages"]
        assert len(client.get(f"/api/threads/{target['id']}/targets").json()) == 1
        forked = client.post(
            "/api/threads/fork",
            json={
                "request_id": "fork",
                "checkpoint_id": result["checkpoint_id"],
                "title": "Branch",
                "profile_id": "default",
            },
        )
        assert forked.status_code == 201, forked.text
        assert forked.json()["fork_checkpoint_id"] == result["checkpoint_id"]
        assert "provider-secret-fixture" not in client.get("/api/resources").text
        assert "provider-secret-fixture" not in client.get("/api/credentials").text
    with TestClient(create_app(models=models)) as reconnected:
        authorize(reconnected, tmp_path)
        assert (
            reconnected.get(f"/api/runs/{receipt['run_id']}").json()["output"] == result["output"]
        )
        assert (
            reconnected.post(f"/api/threads/{target['id']}/inputs", json=request).json()["id"]
            == receipt["id"]
        )
        second = reconnected.post(
            f"/api/threads/{target['id']}/inputs", json={"request_id": "second", "text": "Continue"}
        ).json()
        assert completed(reconnected, second["run_id"])["checkpoint_id"] != result["checkpoint_id"]


def test_current_client_grants_and_no_implicit_administration(console, tmp_path):
    with TestClient(create_app(models=models)) as client:
        authorize(client, tmp_path)
        configure(client)
        first = client.post("/api/threads", json={"request_id": "shared", "title": "Shared"}).json()
        second = client.post(
            "/api/threads", json={"request_id": "private", "title": "Private"}
        ).json()
        actor = {
            "id": "participant",
            "thread_ids": [first["id"]],
            "profile_ids": ["default"],
            "actions": ["read", "submit"],
        }
        response = client.post("/api/clients", json={"principal": actor})
        assert response.status_code == 201, response.text
        participant = {"Authorization": f"Bearer {response.json()['token']}"}
        assert client.post("/api/clients", json={"principal": actor}).status_code == 409
        assert client.get("/api/threads", headers=participant).json()[0]["id"] == first["id"]
        assert client.get(f"/api/threads/{second['id']}", headers=participant).status_code == 403
        assert client.get("/api/resources", headers=participant).status_code == 403
        assert client.get("/api/credentials", headers=participant).status_code == 403
        assert (
            client.post(
                "/api/threads",
                headers=participant,
                json={"request_id": "denied", "title": "Not allowed"},
            ).status_code
            == 403
        )
        assert client.get("/api/profiles", headers=participant).json()[0]["id"] == "default"
        assert (
            client.put(
                "/api/clients/participant", json={"principal": {**actor, "enabled": False}}
            ).status_code
            == 204
        )
        assert client.get("/api/threads", headers=participant).status_code == 403
        assert "token_hash" not in client.get("/api/clients").text


def test_validation_conflicts_and_origin_protection_do_not_echo_secrets(console, tmp_path):
    with TestClient(create_app(models=models)) as client:
        authorize(client, tmp_path)
        configure(client)
        secret = "private-value-must-not-appear"
        invalid = client.post(
            "/api/resources/import",
            json={
                "resources": [
                    {
                        "id": "bad",
                        "kind": "model",
                        "expected_version": 0,
                        "content": {
                            "provider": "openai",
                            "model_name": "test",
                            "credential_env": "KEY",
                            "base_url": f"https://user:{secret}@example.invalid",
                        },
                    }
                ]
            },
        )
        assert invalid.status_code == 422 and secret not in invalid.text
        wrong_type = client.put("/api/credentials/TEST_KEY", json={"value": {"secret": secret}})
        assert wrong_type.status_code == 422 and secret not in wrong_type.text
        assert (
            client.get("/api/instance", headers={"Origin": "https://untrusted.invalid"}).status_code
            == 403
        )
        assert (
            client.get("/api/instance", headers={"Origin": "http://testserver"}).status_code == 200
        )
        assert client.get("/api/instance").headers["cache-control"] == "no-store"
        target = client.post("/api/threads", json={"request_id": "draft", "title": "Draft"}).json()
        edit = {
            "title": "Updated",
            "profile_id": "default",
            "expected_version": target["version"],
            "archived": True,
        }
        assert client.put(f"/api/threads/{target['id']}", json=edit).status_code == 200
        assert client.put(f"/api/threads/{target['id']}", json=edit).status_code == 409
        assert (
            client.post(
                f"/api/threads/{target['id']}/inputs",
                json={"request_id": "archived", "text": "Do not execute"},
            ).status_code
            == 409
        )


def test_second_server_cannot_own_the_same_data_root(console):
    with (
        TestClient(create_app()),
        pytest.raises(ClawError, match="Another server"),
        TestClient(create_app()),
    ):
        pytest.fail("Second server acquired the same data root")


def test_client_grant_cas_cannot_restore_revoked_access(console, tmp_path):
    with TestClient(create_app(models=models)) as client:
        authorize(client, tmp_path)
        created = client.post(
            "/api/clients",
            json={
                "principal": {
                    "id": "reviewer",
                    "actions": ["read"],
                    "thread_ids": ["thread-private"],
                }
            },
        ).json()
        original = created["principal"]
        token = {"Authorization": f"Bearer {created['token']}"}
        revoked = {**original, "enabled": False, "thread_ids": []}
        assert client.put("/api/clients/reviewer", json={"principal": revoked}).status_code == 204
        assert client.get("/api/instance", headers=token).status_code == 403
        stale = client.put(
            "/api/clients/reviewer", json={"principal": {**original, "actions": ["read", "submit"]}}
        )
        assert stale.status_code == 409 and stale.json()["error"]["code"] == "client_conflict"
        current = next(
            item for item in client.get("/api/clients").json() if item["id"] == "reviewer"
        )
        assert current["version"] == original["version"] + 1
        assert not current["enabled"] and current["thread_ids"] == []
        assert client.get("/api/instance", headers=token).status_code == 403
        # A reviewed new snapshot can deliberately re-enable the client.
        assert (
            client.put(
                "/api/clients/reviewer", json={"principal": {**current, "enabled": True}}
            ).status_code
            == 204
        )
        assert client.get("/api/instance", headers=token).status_code == 200


@pytest.mark.parametrize("identity", ["team/bot", "..", "", "has space", "a?b", "a" * 129])
def test_client_identity_must_remain_addressable(console, tmp_path, identity):
    with TestClient(create_app(models=models)) as client:
        authorize(client, tmp_path)
        assert client.post("/api/clients", json={"principal": {"id": identity}}).status_code == 422
        assert [item["id"] for item in client.get("/api/clients").json()] == ["operator"]
