"""Schedules keep business intent durable and delegate execution to the real Host."""

from datetime import UTC, datetime

import pytest

from a13n_claw.configuration.models import ClawSettings
from a13n_claw.database.store import BusinessStore
from a13n_claw.messaging.capability import messaging_integrations
from a13n_claw.runtime.host import open_host
from a13n_claw.schedules.models import (
    ScheduleCatalog,
    ScheduleDefinition,
    Timing,
    read_catalog,
    write_catalog,
)
from a13n_claw.schedules.store import ScheduleStore
from tests.helpers import setup


def definition(**changes):
    return ScheduleDefinition.model_validate(
        {
            "name": "Daily check",
            "prompt": "Check the workspace",
            "project_id": "project-claw-main",
            "timing": {"kind": "interval", "seconds": 60},
            "notification": "never",
            **changes,
        }
    )


def test_timing_timezone_and_bounded_catchup(tmp_path):
    timing = Timing(kind="cron", cron="30 9 * * *", timezone="Asia/Shanghai")
    now = datetime(2026, 10, 21, tzinfo=UTC).timestamp()
    assert timing.after(now) == datetime(2026, 10, 21, 1, 30, tzinfo=UTC).timestamp()
    store = ScheduleStore(BusinessStore(tmp_path / "business.sqlite3", ClawSettings()))
    catalog = ScheduleCatalog(schedules={"daily": definition()})
    store.discover(catalog, {}, now)
    store.discover(catalog, {}, now + 3600)
    store.discover(catalog, {}, now + 3600)
    rows = store.list()
    assert len(rows) == 1
    assert rows[0]["due"] == now + 3600
    assert rows[0]["status"] == "pending"


def test_missed_skip_overlap_and_paused_jobs(tmp_path):
    store = ScheduleStore(BusinessStore(tmp_path / "business.sqlite3", ClawSettings()))
    catalog = ScheduleCatalog(
        schedules={
            "skip": definition(missed="skip", grace_seconds=10),
            "paused": definition(enabled=False),
            "overlap": definition(overlap="skip"),
        }
    )
    store.discover(catalog, {}, 1000)
    store.discover(catalog, {}, 1060)
    store.discover(catalog, {}, 1200)
    assert not store.list("paused")
    assert store.list("skip")[0]["status"] == "skipped"
    assert store.list("overlap")[0]["status"] == "skipped"
    assert len(store.list("overlap")) == 2


@pytest.mark.parametrize("overlap", ["queue", "skip"])
def test_blocked_queue_does_not_starve_other_schedules(tmp_path, overlap):
    store = ScheduleStore(BusinessStore(tmp_path / "business.sqlite3", ClawSettings()))
    first = store.trigger("blocked", definition(), None, "first", 1000)
    store.status(first["id"], "waiting")
    for index in range(50):
        store.trigger("blocked", definition(overlap=overlap), None, f"queued-{index}", 1001 + index)
    other = store.trigger("other", definition(), None, "other", 2000)
    batch = store.pending(3000)
    if overlap == "queue":
        assert batch[0]["id"] == other["id"]
        assert store.earlier_open(batch[1])
    else:
        # Skip-policy occurrences must still reach reconciliation for settlement.
        assert len(batch) == 50
        assert all(row["schedule_id"] == "blocked" for row in batch)


def test_catalog_cas_preserves_external_edit(tmp_path):
    path = tmp_path / "schedules.yaml"
    catalog = ScheduleCatalog(schedules={"daily": definition()})
    digest = write_catalog(path, catalog, None)
    assert read_catalog(path) == (catalog, digest)
    path.write_text(path.read_text() + "\n# external edit\n")
    with pytest.raises(ValueError, match="changed"):
        write_catalog(path, ScheduleCatalog(), digest)
    assert "external edit" in path.read_text()


def test_restart_unknown_and_manual_idempotency(tmp_path):
    business = BusinessStore(tmp_path / "business.sqlite3", ClawSettings())
    store = ScheduleStore(business)
    row = store.trigger("daily", definition(), None, "manual-1", 1000)
    assert store.trigger("daily", definition(), None, "manual-1", 2000)["id"] == row["id"]
    store.start(row["id"], "thread-1")
    store.restart()
    assert store.get(row["id"])["status"] == "unknown"
    assert store.pending(3000) == []
    store.resolve(row["id"], "retry", "Operator accepts repetition risk")
    assert store.get(row["id"])["status"] == "pending"
    with pytest.raises(ValueError, match="Conflicting"):
        store.trigger("daily", definition(prompt="other"), None, "manual-1", 3000)


@pytest.mark.anyio
async def test_native_scheduled_execution_and_authorized_notification(tmp_path, monkeypatch):
    root, service, transport = await setup(tmp_path, monkeypatch)
    job = definition(notification="always", destination={"channel_id": "room"})
    await service.scheduler.put("daily", job, None)
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        row = await service.scheduler.trigger("daily", "run-once")
        await service.reconcile()
        row = service.scheduler.store.get(row["id"])
        assert row["status"] == "accepted", (row, service.scheduler.error)
        await host.wait_root_operation(row["receipt_id"], timeout_seconds=20)
        await service.reconcile()
        row = service.scheduler.store.get(row["id"])
        assert row["status"] == "completed", row
        assert row["result"] == "Not broadcast"
        assert len(transport.sent) == 1
        assert transport.sent[0][0].chat_id == "chat"
        assert transport.sent[0][0].reply_to is None
        assert "Not broadcast" in transport.sent[0][1].text
        await service.reconcile()
        assert len(transport.sent) == 1
        assert not service.store.rows("SELECT * FROM inbox")
        assert await host.active_root_operation(row["thread_id"]) is None


@pytest.mark.anyio
async def test_shutdown_after_schedule_admission_preserves_unknown(tmp_path, monkeypatch):
    import asyncio

    from a13n_harness_ui.errors import HarnessUiError

    root, service, transport = await setup(tmp_path, monkeypatch)
    await service.scheduler.put("daily", definition(), None)
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        submit = host.submit_thread
        accepted = []

        async def lost(**kwargs):
            receipt = await submit(**kwargs)
            accepted.append(receipt)
            raise HarnessUiError("Shutdown before receipt returned", code="app_stopping")

        monkeypatch.setattr(host, "submit_thread", lost)
        row = await service.scheduler.trigger("daily", "shutdown")
        await service.reconcile()
        await host.wait_root_operation(accepted[0].receipt_id, timeout_seconds=20)
        assert service.scheduler.store.get(row["id"])["status"] == "unknown"
        await asyncio.sleep(service.settings.retry_seconds)
        await service.reconcile()
        assert len(accepted) == 1
        assert service.scheduler.store.get(row["id"])["status"] == "unknown"
        assert transport.sent == []


@pytest.mark.anyio
async def test_schedule_delivery_does_not_follow_rebound_channel(tmp_path, monkeypatch):
    root, service, transport = await setup(tmp_path, monkeypatch)
    job = definition(notification="always", destination={"channel_id": "room"})
    await service.scheduler.put("daily", job, None)
    row = await service.scheduler.trigger("daily", "delivery-only")
    service.scheduler.store.start(row["id"], "thread-schedule")
    service.scheduler.store.status(row["id"], "accepted", receipt_id="receipt-test")
    service.scheduler.store.complete(row["id"], "completed", "original result")
    delivery = service.store.rows("SELECT * FROM deliveries")[0]
    channels = dict(service.settings.channels)
    channels["room"] = channels["room"].model_copy(update={"chat_id": "other-chat"})
    service.settings = service.settings.model_copy(update={"channels": channels})
    result = await service.delivery.send(delivery["id"])
    assert "blocked" in result
    assert transport.sent == []


@pytest.mark.parametrize(
    "timing",
    [
        {"kind": "cron", "cron": "0 0 31 2 *"},
        {"kind": "cron", "cron": "0 0 * * *", "timezone": "Invalid/Zone"},
        {"kind": "interval", "seconds": 1},
        {"kind": "once", "at": "2026-10-21T09:00:00"},
    ],
)
def test_invalid_timing_is_validation_error(timing):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Timing.model_validate(timing)


@pytest.mark.anyio
async def test_restart_recovers_exact_result_without_resubmission(tmp_path, monkeypatch):
    root, service, transport = await setup(tmp_path, monkeypatch)
    await service.scheduler.put("daily", definition(), None)
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        row = await service.scheduler.trigger("daily", "restart-test")
        await service.reconcile()
        row = service.scheduler.store.get(row["id"])
        await host.wait_root_operation(row["receipt_id"], timeout_seconds=20)
        # Crash boundary: Host saved the result but Claw has not observed completion.
    service.scheduler.store.restart()
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        await service.reconcile()
        saved = service.scheduler.store.get(row["id"])
        assert saved["status"] == "completed"
        assert saved["receipt_id"] == row["receipt_id"]
        assert saved["result"] == "Not broadcast"
        assert len(service.scheduler.store.list()) == 1


@pytest.mark.anyio
async def test_unrelated_later_run_does_not_complete_old_occurrence(tmp_path, monkeypatch):
    root, service, transport = await setup(tmp_path, monkeypatch)
    await service.scheduler.put("daily", definition(), None)
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        row = await service.scheduler.trigger("daily", "unobserved")
        await service.reconcile()
        row = service.scheduler.store.get(row["id"])
        await host.wait_root_operation(row["receipt_id"], timeout_seconds=20)
        other = await host.submit_thread(thread_id=row["thread_id"], prompt="Unrelated later work")
        await host.wait_root_operation(other.receipt_id, timeout_seconds=20)
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        await service.reconcile()
        saved = service.scheduler.store.get(row["id"])
        assert saved["status"] == "unknown"
        assert saved["result"] is None
        assert not service.store.rows("SELECT * FROM deliveries")


@pytest.mark.anyio
async def test_author_revocation_and_source_rebinding_block_job(tmp_path, monkeypatch):
    from a13n_claw.schedules.models import ScheduleSource

    root, service, transport = await setup(tmp_path, monkeypatch)
    channel = service.settings.channels["room"].model_copy(update={"operators": ("user",)})
    service.settings = service.settings.model_copy(update={"channels": {"room": channel}})
    connection = service.settings.connections["lark"]
    source = ScheduleSource(
        connection="lark", app_id=connection.app_id, domain=connection.domain, chat_id="chat"
    )
    job = definition(author="user", source_channel="room", source=source)
    await service.scheduler.put("daily", job, None)
    assert service.scheduler.authorize(job) is None
    service.settings = service.settings.model_copy(
        update={"channels": {"room": channel.model_copy(update={"operators": ()})}}
    )
    with pytest.raises(ValueError, match="authority"):
        service.scheduler.authorize(job)
    service.settings = service.settings.model_copy(
        update={"channels": {"room": channel.model_copy(update={"chat_id": "other-chat"})}}
    )
    with pytest.raises(ValueError, match="identity"):
        service.scheduler.authorize(job)


def test_pending_has_no_terminal_notification(tmp_path):
    business = BusinessStore(tmp_path / "business.sqlite3", ClawSettings())
    store = ScheduleStore(business)
    row = store.trigger("daily", definition(), None, "not-admitted", 1000)
    store.complete(row["id"], "completed", "Cannot complete an unadmitted request")
    assert store.get(row["id"])["status"] == "pending"


@pytest.mark.anyio
async def test_scheduled_native_decision_resumes_same_occurrence(tmp_path, monkeypatch):
    from a13n_harness_ui.surfaces import DecisionResponseBatch
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart

    from tests.helpers import streaming_model

    async def model(messages, info):
        returned = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if not returned:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "ask_user_question",
                        {
                            "questions": [
                                {
                                    "header": "Choice",
                                    "question": "Which option?",
                                    "multiSelect": False,
                                    "options": [
                                        {"label": "One", "description": "First"},
                                        {"label": "Two", "description": "Second"},
                                    ],
                                }
                            ]
                        },
                    )
                ]
            )
        return ModelResponse(parts=[TextPart("Scheduled question answered")])

    root, service, transport = await setup(tmp_path, monkeypatch, model=streaming_model(model))
    await service.scheduler.put("daily", definition(), None)
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        row = await service.scheduler.trigger("daily", "question")
        await service.reconcile()
        row = service.scheduler.store.get(row["id"])
        operation = await host.wait_root_operation(row["receipt_id"], timeout_seconds=20)
        assert operation.status == "suspended"
        # Answer immediately, before the periodic reconciliation observes suspension.
        pending = await host.thread_decisions(thread_id=row["thread_id"])
        response = DecisionResponseBatch.model_validate(
            {
                "expected_continuation_id": pending.continuation_id,
                "responses": (
                    {
                        "kind": "question",
                        "request_id": pending.requests[0].request_id,
                        "response": "One",
                    },
                ),
            }
        )
        async with service.lock:
            receipt = await service.scheduler.respond(row["thread_id"], response)
        assert receipt.receipt_id != row["receipt_id"]
        resumed = await host.wait_root_operation(receipt.receipt_id, timeout_seconds=20)
        assert resumed.status == "completed"
        await service.reconcile()
        saved = service.scheduler.store.get(row["id"])
        assert saved["status"] == "completed"
        assert saved["result"] == "Scheduled question answered"
        assert saved["receipt_id"] == receipt.receipt_id
        assert len(service.scheduler.store.list()) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("operator", [True, False])
async def test_native_schedule_tool_binds_actual_source_and_operator(
    tmp_path, monkeypatch, operator
):
    from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart

    from tests.helpers import enable_operator, message, streaming_model

    results = []

    async def model(messages, info):
        returned = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if not returned:
            return ModelResponse(parts=[ToolCallPart("claw_inbox_list", {})])
        last = returned[-1]
        if last.tool_name == "claw_inbox_list":
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        "claw_schedules",
                        {
                            "item_id": last.content["items"][0]["id"],
                            "action": "put",
                            "schedule_id": "from-im",
                            "expected_digest": None,
                            "task": {
                                "name": "Reminder",
                                "prompt": "Report tomorrow",
                                "timing": {"kind": "interval", "seconds": 86400},
                            },
                        },
                    )
                ]
            )
        results.append(last.content)
        return ModelResponse(parts=[TextPart("Done")])

    root, service, transport = await setup(tmp_path, monkeypatch, model=streaming_model(model))
    if operator:
        enable_operator(service)
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        await service.ingress.receive(message())
        await service.reconcile()
        receipt = service.store.rows("SELECT * FROM attempts")[0]["receipt_id"]
        operation = await host.wait_root_operation(receipt, timeout_seconds=20)
        assert operation.status == "completed"
        assert results
        if operator:
            assert "error" not in results[0], results
            job = service.scheduler.catalog.schedules["from-im"]
            assert job.author == "user"
            assert job.source.app_id == "test-app"
            assert job.source.chat_id == "chat"
            assert job.destination.channel_id == "room"
            assert job.project_id == "project-claw-main"
            assert job.agent_id is None and job.model_id is None
        else:
            assert "operator" in results[0]["error"]
            assert not service.scheduler.catalog.schedules
