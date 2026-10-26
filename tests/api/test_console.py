"""Console boundaries against one real, isolated, published Host."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from pydantic_ai.models.test import TestModel

from a13n_claw.app import create_app
from a13n_claw.configuration.bootstrap import initialize


@pytest.fixture
async def console_api(tmp_path, monkeypatch):
    monkeypatch.setenv("A13N_OFFICIAL_MODELS_AUTO_UPDATE", "0")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    async def resolve(self, model_id, *, thread_id):
        return TestModel(call_tools=[], custom_output_text="Recorded reply")

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    root, workspace = tmp_path / "claw", tmp_path / "work"
    workspace.mkdir()
    await initialize(root, workspace, model_route="openai:gpt-4o")
    app = create_app(root=root, console=False)
    token = (root / "operator.token").read_text().strip()
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://claw",
            headers={"Authorization": f"Bearer {token}"},
        ) as client,
    ):
        yield client, app.state.service, root


@pytest.mark.anyio
async def test_usage_keeps_native_totals_and_query_validation(console_api):
    client, service, _ = console_api
    today = datetime.now(UTC).date()
    query = {"start": str(today), "end": str(today + timedelta(days=1)), "group_by": "model"}
    empty = await client.get("/api/usage", params=query)
    assert empty.status_code == 200, empty.text
    assert empty.json()["summary"]["total_tokens"] == 0
    main = (await client.get("/api/status")).json()["instance"]["main_thread_id"]
    receipt = (
        await client.post(f"/api/threads/{main}/inputs", json={"text": "Record usage"})
    ).json()
    await service.host.wait_root_operation(receipt["receipt_id"], timeout_seconds=20)
    first = (await client.get("/api/usage", params={**query, "limit": 1})).json()
    off_page = (await client.get("/api/usage", params={**query, "offset": 100})).json()
    assert first["summary"] == off_page["summary"]
    assert off_page["groups"] == []
    totals = first["summary"]
    assert totals["model_requests"] > 0
    assert totals["total_tokens"] == totals["input_tokens"] + totals["output_tokens"]
    detail = await client.get(f"/api/threads/{main}/usage")
    assert detail.status_code == 200, detail.text
    assert detail.json()["thread_id"] == main
    invalid = await client.get("/api/usage", params={**query, "end": query["start"]})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["fields"]
    assert (await client.get("/api/usage", params={**query, "limit": 101})).status_code == 422
    assert (
        await client.get(
            "/api/usage", params={**query, "project_id": "project-main", "projectless": True}
        )
    ).status_code == 422
    assert first["end"] == query["end"]


@pytest.mark.anyio
async def test_accounts_are_native_and_errors_remain_actionable(console_api):
    client, service, _ = console_api
    identity = id(service.host)
    created = await client.post(
        "/api/subscriptions/accounts", json={"provider": "codex", "name": "Work"}
    )
    assert created.status_code == 201, created.text
    account_id = created.json()["id"]
    catalog = await client.get("/api/subscriptions")
    assert catalog.status_code == 200, catalog.text
    account = next(
        item for item in catalog.json()["accounts"] if item["account"]["id"] == account_id
    )
    assert not account["authentication"]["usable"]
    assert account["usage"]["usage"] is None
    updated = await client.patch(
        f"/api/subscriptions/accounts/{account_id}", json={"name": "Renamed", "enabled": False}
    )
    assert updated.json()["name"] == "Renamed"
    assert updated.json()["enabled"] is False
    missing = await client.patch(
        "/api/subscriptions/accounts/acct-missing", json={"name": "Missing"}
    )
    assert missing.status_code == 409
    assert missing.json()["error"]["code"] != "request_unknown"
    wrong_provider = await client.post(
        "/api/login", json={"provider": "grok", "account_id": account_id}
    )
    assert wrong_provider.status_code == 409
    assert wrong_provider.json()["error"]["code"] == "account_provider_mismatch"
    assert (await client.get("/api/login")).json() is None
    assert (await client.get("/api/login/missing")).status_code == 409
    assert (await client.delete(f"/api/subscriptions/accounts/{account_id}")).status_code == 204
    assert id(service.host) == identity


@pytest.mark.anyio
async def test_model_edits_preserve_fields_and_source_preconditions(console_api):
    client, _, root = console_api
    inventory = await client.get("/api/models")
    assert inventory.status_code == 200, inventory.text
    source = inventory.json()["models"][0]
    model = source["resource"]
    model["name"] = "Edited model"
    model["settings"] = {"temperature": 0.42, "max_tokens": 512}
    body = {
        "resource": model,
        "relative_path": source["relative_path"],
        "expected_digest": source["source_digest"],
    }
    changed = await client.put("/api/models", json=body)
    assert changed.status_code == 200, changed.text
    assert "configuration" not in changed.json()
    assert (await client.put("/api/models", json=body)).status_code == 409
    current = (await client.get("/api/models")).json()["models"][0]
    assert current["resource"]["settings"] == model["settings"]
    assert current["resource"]["authentication"] == model["authentication"]
    body["expected_digest"] = current["source_digest"]
    path = root / "host" / source["relative_path"]
    external = path.read_bytes() + b"\n# external edit\n"
    path.write_bytes(external)
    assert (await client.put("/api/models", json=body)).status_code == 409
    assert path.read_bytes() == external
    assert (
        await client.put("/api/models", json={**body, "relative_path": "agents/claw.yaml"})
    ).status_code == 409
    choices = await client.get("/api/models/choices")
    assert choices.status_code == 200, choices.text
    chatgpt = next(
        item for item in choices.json()["connections"] if item["id"] == "chatgpt-subscription"
    )
    assert "manual_callback" in chatgpt["account"]["login_methods"]
    connection = next(
        item for item in choices.json()["connections"] if item["authentication"] == "api_key"
    )
    recipe = await client.post(
        "/api/models/prepare",
        json={
            "connection": connection["id"],
            "model_id": connection["default_model"],
            "authentication": {"kind": "api_key", "env": "CLAW_MODEL_API_KEY"},
        },
    )
    assert recipe.status_code == 200, recipe.text
    assert recipe.json()["route"] == f"{connection['provider']}:{connection['default_model']}"


@pytest.mark.anyio
async def test_credentials_and_validation_never_echo_secret_values(console_api):
    client, _, _ = console_api
    secret = "fixture-secret-must-not-be-returned"
    result = await client.put(
        "/api/credentials", json={"credential_ref": "key-test", "key": secret}
    )
    assert result.status_code == 200, result.text
    assert secret not in result.text
    assert (await client.get("/api/credentials")).json() == [{"credential_ref": "key-test"}]
    invalid = await client.put(
        "/api/credentials", json={"credential_ref": "not valid", "key": secret}
    )
    assert invalid.status_code == 422
    assert secret not in invalid.text
    assert invalid.json()["error"]["fields"][0]["path"] == ["body", "credential_ref"]


def test_console_contract_has_response_schemas_without_runtime_docs(tmp_path):
    app = create_app(root=tmp_path, console=False)
    schema = app.openapi()
    for path in (
        "/api/usage",
        "/api/models",
        "/api/subscriptions",
        "/api/login",
        "/api/threads/{thread_id}/usage",
    ):
        assert schema["paths"][path]["get"]["responses"]["200"]["content"]["application/json"][
            "schema"
        ]
    assert app.openapi_url is None


@pytest.mark.anyio
async def test_schedule_status_and_preview_do_not_invent_next_due(console_api):
    from a13n_claw.api.schedules import schedule_status
    from a13n_claw.database.store import BusinessStore
    from a13n_claw.runtime.service import ClawService
    from a13n_claw.schedules.models import ScheduleCatalog
    from tests.schedules.test_schedules import definition

    client, running, root = console_api
    # Clock projection is tested without racing the live dispatcher's discovery.
    service = ClawService(
        running.settings, BusinessStore(root / "preview.sqlite3", running.settings), {}
    )
    scheduler = service.scheduler
    scheduler.catalog = ScheduleCatalog(schedules={"daily": definition()})
    initial = await schedule_status(service)
    assert initial.status["daily"].state == "pending_discovery"
    assert initial.status["daily"].next_due is None
    scheduler.store.discover(scheduler.catalog, {}, 1000)
    observed = await schedule_status(service)
    assert observed.status["daily"].next_due.timestamp() == 1060
    scheduler.store.discover(scheduler.catalog, {}, 1060)
    observed = await schedule_status(service)
    assert observed.status["daily"].latest.status == "pending"
    assert observed.status["daily"].next_due.timestamp() == 1120
    scheduler.catalog = ScheduleCatalog(schedules={"daily": definition(enabled=False)})
    paused = await schedule_status(service)
    assert paused.status["daily"].state == "paused"
    assert paused.status["daily"].next_due is None
    result = await client.post(
        "/api/schedules/preview",
        json={
            "timing": {"kind": "cron", "cron": "0 9 * * *", "timezone": "America/New_York"},
            "after": "2026-10-31T12:00:00Z",
            "count": 3,
        },
    )
    assert result.status_code == 200, result.text
    assert result.json()["instants"] == [
        "2026-10-31T09:00:00-04:00",
        "2026-11-01T09:00:00-05:00",
        "2026-11-02T09:00:00-05:00",
    ]
    assert (
        await client.post(
            "/api/schedules/preview",
            json={
                "timing": {"kind": "interval", "seconds": 60},
                "after": "2026-10-31T12:00:00",
            },
        )
    ).status_code == 409


@pytest.mark.anyio
async def test_thread_business_links_and_occurrence_deliveries_are_exact(console_api):
    from tests.schedules.test_schedules import definition

    client, service, _ = console_api
    main = (await client.get("/api/status")).json()["instance"]["main_thread_id"]
    async with service.lock:
        with service.store.transaction() as db:
            for key, thread in (("linked", None), ("direct", main), ("unrelated", "other-thread")):
                db.execute(
                    "INSERT INTO inbox (id,connection,event_id,fingerprint,mode,thread_id,"
                    "body,disposition,version,reason,next_check,created) "
                    "VALUES(?,?,?,?,?,?,?,'handled',1,'settled',0,?)",
                    (key, "fixture", key, key, "one_thread", thread, "private-prompt", 100),
                )
            db.execute(
                "INSERT INTO attempts VALUES('attempt-linked',?,'submit','completed',"
                "'receipt-linked','saved evidence',101)",
                (main,),
            )
            db.execute("INSERT INTO attempt_items VALUES('attempt-linked','linked')")
        job = definition(notification="always", destination={"channel_id": "fixture"})
        row = service.scheduler.store.trigger(
            "daily",
            job,
            {"channel_id": "fixture", "chat_id": "chat", "reply_to": None},
            "linked-request",
            100,
        )
        service.scheduler.store.start(row["id"], main)
        service.scheduler.store.status(row["id"], "accepted", receipt_id="fixture-receipt")
        service.scheduler.store.complete(row["id"], "completed", "Recorded result")
        view = await client.get(f"/api/threads/{main}/business")
        assert view.status_code == 200, view.text
        result = view.json()
        assert {item["id"] for item in result["inbox"]} == {"direct", "linked"}
        assert "private-prompt" not in view.text
        assert result["attempts"][0]["id"] == "attempt-linked"
        assert result["occurrences"][0]["id"] == row["id"]
        deliveries = await client.get(f"/api/occurrences/{row['id']}/deliveries")
        assert deliveries.status_code == 200, deliveries.text
        assert deliveries.json() == result["deliveries"]
        assert deliveries.json()[0]["status"] == "pending"
        assert "body" not in deliveries.json()[0]
        bounded = await client.get(f"/api/threads/{main}/business", params={"limit": 1})
        assert len(bounded.json()["inbox"]) == 1
        assert (
            await client.get(f"/api/threads/{main}/business", params={"limit": 101})
        ).status_code == 422
    assert (await client.get("/api/threads/missing/business")).status_code == 409
    assert (await client.get("/api/occurrences/missing/deliveries")).status_code == 409
