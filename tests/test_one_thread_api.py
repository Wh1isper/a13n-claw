"""Public operations, scoped integration ingress and saved-state Console controls."""

from fastapi.testclient import TestClient
from test_api import authorize, configure, models

from a13n_claw.app import create_app


def test_one_thread_http_controls_persist_across_restart(console, tmp_path):
    with TestClient(create_app(mode="one_thread", models=models)) as client:
        authorize(client, tmp_path)
        configure(client)
        assert client.get("/api/instance").json()["conversation_mode"] == "one_thread"
        main = client.post("/api/coordination/main", json={"profile_id": "default"}).json()
        assert (
            client.post("/api/coordination/main", json={"profile_id": "default"}).json()["id"]
            == main["id"]
        )
        state = client.get("/api/coordination").json()
        body = {"expected_version": state["version"], "paused": True}
        assert client.put("/api/coordination/control", json=body).status_code == 200
        assert client.put("/api/coordination/control", json=body).status_code == 409
        integration = client.post(
            "/api/clients",
            json={
                "principal": {"id": "bridge", "actions": [], "profile_ids": [], "thread_ids": []}
            },
        )
        assert integration.status_code == 201, integration.text
        policy = {
            "platform": "http",
            "account": "account",
            "channel": "team",
            "ingress_actor_id": "bridge",
            "allowed_senders": ["alice"],
            "shared_context_acknowledged": True,
            "group": True,
        }
        result = client.put("/api/channels/team", json={"expected_version": 0, "policy": policy})
        assert result.status_code == 200, result.text
        client.headers["Authorization"] = "Bearer " + integration.json()["token"]
        incoming = {
            "event_id": "event",
            "sender": "alice",
            "text": "Private conversation",
            "addressed": True,
        }
        assert client.get("/api/inbox").status_code == 403
        assert client.get(f"/api/threads/{main['id']}").status_code == 403
        ignored = client.post(
            "/api/channels/team/messages",
            json=incoming | {"event_id": "ignored", "addressed": False},
        )
        assert ignored.status_code == 200
        assert ignored.json()["disposition"] == "ignored"
        first = client.post("/api/channels/team/messages", json=incoming)
        assert first.status_code == 200, first.text
        assert client.post("/api/channels/team/messages", json=incoming).json() == first.json()
        assert (
            client.post(
                "/api/channels/team/messages", json=incoming | {"text": "Changed"}
            ).status_code
            == 409
        )
        authorize(client, tmp_path)
        assert client.get(f"/api/threads/{main['id']}/runs").json() == []
        item = client.get("/api/inbox").json()[0]
        assert item["disposition"] == "pending"
        response = client.put(
            f"/api/inbox/{item['id']}",
            json={
                "expected_version": item["version"],
                "disposition": "deferred",
                "note": "Need human decision",
                "human_key": "confirm-plan",
            },
        )
        assert response.status_code == 200, response.text
    with TestClient(create_app(mode="one_thread", models=models)) as client:
        authorize(client, tmp_path)
        state = client.get("/api/coordination").json()
        assert state["main_id"] == main["id"] and state["paused"]
        assert state["counts"]["deferred"] == 1
        item = client.get("/api/inbox").json()[0]
        assert item["human_key"] == "confirm-plan"
        assert (
            client.put(
                f"/api/inbox/{item['id']}",
                json={"expected_version": 1, "disposition": "handled", "note": "Stale editor"},
            ).status_code
            == 409
        )
        assert client.get("/api/deliveries").json() == []
