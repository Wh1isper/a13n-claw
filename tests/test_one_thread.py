"""One Thread durable levels, ownership, races and current publication authority."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from a13n_harness import HarnessState

from a13n_claw.attention import (
    ChannelPolicy,
    DeliveryRequest,
    DeliveryResolution,
    InboundMessage,
    ProcessingChange,
    ProcessingControl,
    WorkerCreate,
)
from a13n_claw.domain import ClawError, InputRequest, Principal
from a13n_claw.messaging import Messaging
from a13n_claw.storage import Store


def configure(store):
    store.coordination.configure_mode("one_thread")
    main = store.coordination.initialize_main("operator", "default")
    messaging = Messaging(store)
    policy = ChannelPolicy(
        platform="http",
        account="test",
        channel="room",
        ingress_actor_id="operator",
        allowed_senders=("alice",),
        shared_context_acknowledged=True,
        outbound=True,
        delivery_url="http://127.0.0.1:1/messages",
    )
    messaging.save_channel("operator", "room", policy, 0)
    return main, messaging


def receive(messaging, event="event"):
    return messaging.receive(
        "operator",
        "room",
        InboundMessage(event_id=event, sender="alice", text=f"External body {event}"),
        "/shared",
    )


def running_main(store):
    main, messaging = configure(store)
    receipt = store.admit(
        "operator", main.id, InputRequest(request_id="manual", text="Work"), "/shared"
    )
    store.claim(receipt.run_id, "owner", "/shared")
    store.start(receipt.run_id, "owner")
    return main, messaging, receipt


def complete(store, run_id, owner="owner"):
    run = store.run("operator", run_id)
    if run.status == "queued":
        store.claim(run_id, owner, "/shared")
        store.start(run_id, owner)
    ids = tuple(
        item.id
        for item in store.inputs("operator", run.thread_id)
        if item.run_id == run_id and item.disposition in {"delivering", "delivered"}
    )
    store.publish(
        run_id,
        owner,
        HarnessState.new(thread_id=run.thread_id),
        incorporated=ids,
        status="completed",
        output="Apparently finished",
    )


def expire_backoff(store):
    # Advance only the durable test clock boundary; do not acknowledge any item.
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE coordination SET retry_at='2000-01-01T00:00:00+00:00'")


def test_restart_drains_pending_without_notification_or_redelivery(runtime_store):
    main, messaging = configure(runtime_store)
    item = receive(messaging)
    reopened = Store(runtime_store.path)
    reopened.reconcile_startup()
    reopened.coordination.drain("/shared")
    runs = reopened.runs("operator", main.id)
    assert len(runs) == 1 and runs[0].status == "queued"
    assert reopened.coordination.inbox("operator")[0]["id"] == item["inbox_id"]
    assert "External body" not in reopened.inputs("operator", main.id)[0].text
    assert reopened.coordination.inbox("operator")[0]["disposition"] == "pending"


def test_level_drain_after_completion_captures_new_composition_without_new_event(runtime_store):
    store = runtime_store
    main, messaging = configure(store)
    receive(messaging)
    store.coordination.drain("/shared")
    first = store.runs("operator", main.id)[0]
    complete(store, first.id)
    old = store.run("operator", first.id)
    store.save_resource(
        "operator",
        "profile",
        "default",
        {
            "model_id": "model",
            "environment_id": "local",
            "agent": {"instructions": "New composition"},
        },
        1,
    )
    # No completion callback ran. Restart rediscovers the level and durable backoff.
    store = Store(store.path)
    store.coordination.drain("/shared")
    assert store.coordination.view("operator")["blocked_reason"] == "no_progress_backoff"
    assert len(store.runs("operator", main.id)) == 1
    expire_backoff(store)
    store.coordination.drain("/shared")
    second = store.runs("operator", main.id)[-1]
    assert second.id != first.id and second.composition.profile.version == 2
    claimed = store.claim(second.id, "next", "/shared")
    assert claimed.checkpoint_id == old.checkpoint_id
    assert store.run("operator", first.id) == old


def test_concurrent_ingress_and_drain_coalesce_without_losing_items(runtime_store):
    main, messaging = configure(runtime_store)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: receive(messaging, str(i % 10)), range(30)))
        list(pool.map(lambda _: runtime_store.coordination.drain("/shared"), range(20)))
    assert len(runtime_store.coordination.inbox("operator")) == 10
    assert len(runtime_store.runs("operator", main.id)) == 1
    assert len(runtime_store.inputs("operator", main.id)) == 1
    with pytest.raises(ClawError, match="identity was reused"):
        messaging.receive(
            "operator",
            "room",
            InboundMessage(event_id="0", sender="alice", text="Conflict"),
            "/shared",
        )


def test_arrival_racing_with_completion_is_rediscovered(runtime_store):
    store = runtime_store
    main, messaging = configure(store)
    first_item = receive(messaging)
    store.coordination.drain("/shared")
    first = store.runs("operator", main.id)[0]
    store.claim(first.id, "owner", "/shared")
    store.start(first.id, "owner")
    store.coordination.change_item(
        "operator",
        first_item["inbox_id"],
        ProcessingChange(expected_version=1, disposition="handled", note="Handled"),
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        finished = pool.submit(complete, store, first.id)
        arrived = pool.submit(receive, messaging, "late")
        finished.result()
        arrived.result()
    store.coordination.drain("/shared")
    assert len(store.runs("operator", main.id)) == 2
    assert len(store.coordination.inbox("operator", disposition="pending")) == 1


def test_main_is_canonical_and_old_modes_remain_inactive(runtime_store):
    store = runtime_store
    old = store.create_thread("operator", "Old per-channel", "default")
    main, _ = configure(store)
    assert store.coordination.initialize_main("operator", "default").id == main.id
    assert not store.thread("operator", old.id).active
    with pytest.raises(ClawError, match="inactive mode"):
        store.admit("operator", old.id, InputRequest(request_id="x", text="x"), "/shared")
    with pytest.raises(ClawError, match="instead of archiving"):
        store.update_thread(
            "operator", main.id, main.version, title="Main", profile_id="default", archived=True
        )
    store.coordination.configure_mode("per_channel")
    store.coordination.configure_mode("one_thread")
    new = store.coordination.initialize_main("operator", "default")
    assert new.id != main.id and not store.thread("operator", main.id).active
    with pytest.raises(ClawError, match="disabled"):
        receive(Messaging(store))


def test_worker_creation_and_direct_human_input_are_atomic_idempotent(runtime_store):
    store = runtime_store
    main, _, run = running_main(store)
    request = WorkerCreate(
        request_id="create", title="Research", profile_id="default", text="Research"
    )
    child = store.coordination.create_worker(run.run_id, "owner", request)
    retry = store.coordination.create_worker(run.run_id, "owner", request)
    assert child["thread_id"] == retry["thread_id"] and child["input_id"] == retry["input_id"]
    worker = store.thread("operator", child["thread_id"])
    assert worker.owner_main_id == main.id and worker.parent_run_id is None
    with pytest.raises(ClawError, match="identity was reused"):
        store.coordination.create_worker(
            run.run_id, "owner", request.model_copy(update={"text": "different"})
        )
    human = InputRequest(request_id="human", text="Changed task", source="pretend-internal")
    first = store.admit("operator", worker.id, human, "/shared")
    store.admit("operator", worker.id, human, "/shared")
    items = store.coordination.inbox("operator")
    assert len(items) == 1 and items[0]["kind"] == "human_input"
    assert items[0]["payload"]["input_id"] == first.id
    assert len(store.inputs("operator", worker.id)) == 2
    assert Store(store.path).thread("operator", worker.id).owner_main_id == main.id


@pytest.mark.parametrize("outcome", ["completed", "failed", "cancelled", "interrupted"])
def test_deferred_worker_outcomes_reactivate_even_when_deferral_races(runtime_store, outcome):
    store = runtime_store
    _, messaging, run = running_main(store)
    item = receive(messaging)
    child = store.coordination.create_worker(
        run.run_id,
        "owner",
        WorkerCreate(request_id="worker", title="Work", profile_id="default", text="Work"),
    )
    store.coordination.change_item(
        "operator",
        item["inbox_id"],
        ProcessingChange(
            expected_version=1,
            disposition="deferred",
            note="Waiting on worker",
            dependency_run_id=child["run_id"],
        ),
    )
    if outcome == "completed":
        complete(store, child["run_id"], "worker")
    elif outcome == "cancelled":
        store.cancel("operator", child["run_id"])
    else:
        store.claim(child["run_id"], "worker", "/shared")
        if outcome == "interrupted":
            store.reconcile_startup()
        else:
            store.finish_without_checkpoint(
                child["run_id"], "worker", status="failed", error="prepare failed"
            )
    store.coordination.drain("/shared")
    saved = store.coordination.inbox("operator", item_id=item["inbox_id"])[0]
    assert saved["disposition"] == "pending" and saved["version"] == 3
    with pytest.raises(ClawError, match="changed"):
        store.coordination.change_item(
            "operator",
            item["inbox_id"],
            ProcessingChange(expected_version=2, disposition="handled", note="Stale"),
        )
    again = store.coordination.change_item(
        "operator",
        item["inbox_id"],
        ProcessingChange(
            expected_version=3,
            disposition="deferred",
            note="Already ended",
            dependency_run_id=child["run_id"],
        ),
    )
    assert again["disposition"] == "pending"
    assert any(i["kind"] == "worker_outcome" for i in store.coordination.inbox("operator"))


def test_time_and_human_deferrals_survive_restart_without_hot_loop(runtime_store):
    store = runtime_store
    main, messaging = configure(store)
    first, second = receive(messaging, "time"), receive(messaging, "human")
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    store.coordination.change_item(
        "operator",
        first["inbox_id"],
        ProcessingChange(expected_version=1, disposition="deferred", note="Later", due_at=future),
    )
    store.coordination.change_item(
        "operator",
        second["inbox_id"],
        ProcessingChange(
            expected_version=1,
            disposition="deferred",
            note="Need approval",
            human_key="approve-draft",
        ),
    )
    store = Store(store.path)
    store.coordination.drain("/shared")
    assert not store.runs("operator", main.id)
    with sqlite3.connect(store.path) as db:
        db.execute(
            "UPDATE inbox SET due_at='2000-01-01T00:00:00+00:00' WHERE id=?", (first["inbox_id"],)
        )
    store.coordination.drain("/shared")
    assert len(store.runs("operator", main.id)) == 1
    assert (
        store.coordination.inbox("operator", item_id=second["inbox_id"])[0]["disposition"]
        == "deferred"
    )
    store.coordination.change_item(
        "operator",
        second["inbox_id"],
        ProcessingChange(expected_version=2, disposition="pending", note="Human approved"),
    )
    assert len(store.coordination.inbox("operator", disposition="pending")) == 2


def test_pause_blocks_existing_automatic_queue_but_not_direct_human_input(runtime_store):
    store = runtime_store
    main, messaging = configure(store)
    receive(messaging)
    store.coordination.drain("/shared")
    queued = store.runs("operator", main.id)[0]
    version = store.coordination.view("operator")["version"]
    store.coordination.control("operator", ProcessingControl(expected_version=version, paused=True))
    store.coordination.drain("/shared")
    with pytest.raises(ClawError, match="paused"):
        store.claim(queued.id, "owner", "/shared")
    store.admit(
        "operator",
        main.id,
        InputRequest(request_id="human", text="Inspect while paused"),
        "/shared",
    )
    store.claim(queued.id, "owner", "/shared")
    assert store.coordination.view("operator")["paused"]


def test_worker_cannot_publish_enumerate_siblings_or_obtain_mcp_credentials(runtime_store):
    store = runtime_store
    main, messaging, run = running_main(store)
    children = [
        store.coordination.create_worker(
            run.run_id,
            "owner",
            WorkerCreate(request_id=str(i), title=str(i), profile_id="default", text="Work"),
        )
        for i in range(2)
    ]
    child = children[0]
    store.claim(child["run_id"], "worker", "/shared")
    store.start(child["run_id"], "worker")
    with pytest.raises(ClawError, match="Only the actual Main"):
        messaging.send(
            child["run_id"],
            "worker",
            DeliveryRequest(request_id="send", channel_id="room", text="No"),
        )
    with pytest.raises(ClawError, match="ownership scope"):
        store.coordination.inspect_collaboration(
            child["run_id"], "worker", children[1]["thread_id"]
        )
    with pytest.raises(ClawError, match="only to their owner"):
        store.coordination.send_collaboration(
            child["run_id"],
            "worker",
            children[1]["thread_id"],
            InputRequest(request_id="x", text="x"),
        )
    with pytest.raises(ClawError, match="Only the actual Main"):
        store.coordination.create_worker(
            child["run_id"],
            "worker",
            WorkerCreate(request_id="x", title="x", profile_id="default", text="x"),
        )
    store.save_resource("operator", "mcp", "external", {"url": "http://127.0.0.1:1"}, 0)
    store.save_resource(
        "operator",
        "profile",
        "unsafe",
        {"model_id": "model", "environment_id": "local", "mcp_servers": ["external"]},
        0,
    )
    with pytest.raises(ClawError, match="Only Main may bind MCP"):
        store.coordination.create_worker(
            run.run_id,
            "owner",
            WorkerCreate(request_id="unsafe", title="unsafe", profile_id="unsafe", text="x"),
        )
    state = store.coordination.view("operator")
    store.coordination.control(
        "operator", ProcessingControl(expected_version=state["version"], paused=True)
    )
    store.coordination.send_collaboration(
        child["run_id"], "worker", main.id, InputRequest(request_id="report", text="Need guidance")
    )
    store.coordination.drain("/shared")
    assert len(store.inputs("operator", main.id)) == 1
    assert any(i["kind"] == "worker_message" for i in store.coordination.inbox("operator"))


def test_unknown_delivery_independent_of_handling_never_automatically_replayed(runtime_store):
    store = runtime_store
    main, messaging, run = running_main(store)
    item = receive(messaging)
    request = DeliveryRequest(request_id="send", channel_id="room", text="Reply")
    delivery = messaging.send(run.run_id, "owner", request)
    assert messaging.send(run.run_id, "owner", request)["id"] == delivery["id"]
    store.coordination.change_item(
        "operator",
        item["inbox_id"],
        ProcessingChange(
            expected_version=1, disposition="handled", note=f"Reply intent {delivery['id']}"
        ),
    )
    complete(store, run.run_id)
    assert messaging.claim_delivery()[0]["id"] == delivery["id"]
    reopened = Store(store.path)
    reopened.coordination.reconcile_startup()
    messaging = Messaging(reopened)
    saved = messaging.deliveries("operator")[0]
    assert saved["status"] == "unknown" and messaging.claim_delivery() is None
    receive(messaging, "another")
    reopened.coordination.drain("/shared")
    assert (
        reopened.coordination.view("operator")["blocked_reason"]
        == "delivery_reconciliation_required"
    )
    assert len(reopened.runs("operator", main.id)) == 1
    messaging.resolve_delivery(
        "operator",
        delivery["id"],
        DeliveryResolution(
            expected_version=saved["version"],
            outcome="sent",
            note="Receiver confirmed the same identity",
        ),
    )
    reopened.coordination.drain("/shared")
    assert len(reopened.runs("operator", main.id)) == 2
    assert (
        reopened.coordination.inbox("operator", item_id=item["inbox_id"])[0]["disposition"]
        == "handled"
    )


def test_current_channel_and_actor_policy_blocks_read_and_send(runtime_store):
    store = runtime_store
    _, messaging, run = running_main(store)
    receive(messaging)
    delivery = messaging.send(
        run.run_id, "owner", DeliveryRequest(request_id="send", channel_id="room", text="Reply")
    )
    policy = ChannelPolicy.model_validate(messaging.channels("operator")[0]["policy"])
    messaging.save_channel("operator", "room", policy.model_copy(update={"enabled": False}), 1)
    assert messaging.claim_delivery() is None
    assert messaging.deliveries("operator")[0]["status"] == "blocked"
    assert messaging.deliveries("operator")[0]["id"] == delivery["id"]
    assert store.coordination.inbox("operator")[0]["payload"] is None
    store.set_principal("operator", Principal(id="client", actions=("read",)), "hash")
    with pytest.raises(ClawError):
        store.coordination.inbox("client")


def test_per_channel_ingress_keeps_direct_conversations(runtime_store):
    store = runtime_store
    messaging = Messaging(store)
    policy = ChannelPolicy(
        platform="http",
        account="a",
        channel="b",
        ingress_actor_id="operator",
        allowed_senders=("alice",),
        profile_id="default",
    )
    messaging.save_channel("operator", "room", policy, 0)
    first = receive(messaging)
    assert receive(messaging) == first
    second = receive(messaging, "other")
    assert first["thread_id"] == second["thread_id"] and first["run_id"] == second["run_id"]
    assert len(store.inputs("operator", first["thread_id"])) == 2
    assert not store.coordination.view("operator")["main_id"]


@pytest.mark.parametrize("barrier", ["paused", "delivery_reconciliation_required"])
def test_transferred_automatic_input_keeps_claim_barrier(runtime_store, barrier):
    store = runtime_store
    main, messaging = configure(store)
    receive(messaging)
    store.coordination.drain("/shared")
    first = store.runs("operator", main.id)[0]
    store.claim(first.id, "owner", "/shared")
    store.start(first.id, "owner")
    receive(messaging, "late")
    store.coordination.drain("/shared")
    if barrier == "paused":
        state = store.coordination.view("operator")
        store.coordination.control(
            "operator", ProcessingControl(expected_version=state["version"], paused=True)
        )
    else:
        delivery = messaging.send(
            first.id, "owner", DeliveryRequest(request_id="reply", channel_id="room", text="Reply")
        )
        messaging.claim_delivery()
        messaging.finish_delivery(delivery["id"], sent=False)
    complete(store, first.id)
    store = Store(store.path)
    store.coordination.drain("/shared")
    successor = store.runs("operator", main.id)[-1]
    assert successor.id != first.id
    with pytest.raises(ClawError, match=barrier):
        store.claim(successor.id, "next", "/shared")


def test_filtered_receipt_survives_policy_change_without_retaining_body(runtime_store):
    store = runtime_store
    main, messaging = configure(store)
    policy = ChannelPolicy.model_validate(messaging.channels("operator")[0]["policy"])
    policy = policy.model_copy(update={"group": True, "require_addressed": True})
    messaging.save_channel("operator", "room", policy, 1)
    message = InboundMessage(event_id="filtered", sender="alice", text="Rejected secret body")
    first = messaging.receive("operator", "room", message, "/shared")
    assert first["disposition"] == "ignored"
    messaging.save_channel(
        "operator", "room", policy.model_copy(update={"require_addressed": False}), 2
    )
    assert Messaging(Store(store.path)).receive("operator", "room", message, "/shared") == first
    with sqlite3.connect(store.path) as db:
        assert "Rejected secret body" not in str(db.execute("SELECT * FROM ingress").fetchall())
    assert not store.coordination.inbox("operator")
    assert not store.runs("operator", main.id)
    with pytest.raises(ClawError, match="identity was reused"):
        messaging.receive(
            "operator", "room", message.model_copy(update={"text": "changed"}), "/shared"
        )
    messaging.receive("operator", "room", message.model_copy(update={"event_id": "new"}), "/shared")
    assert len(store.coordination.inbox("operator")) == 1


@pytest.mark.parametrize("revocation", ["channel", "sender", "mention"])
def test_external_asset_read_rechecks_provenance_but_human_assets_remain_readable(
    runtime_store, revocation
):
    store = runtime_store
    main, messaging, run = running_main(store)
    external = store.retain_asset(
        "operator", main.id, "external-file", "external.txt", "text/plain", b"External content"
    )
    human = store.retain_asset(
        "operator", main.id, "human-file", "human.txt", "text/plain", b"Human content"
    )
    messaging.receive(
        "operator",
        "room",
        InboundMessage(
            event_id="file", sender="alice", text="Read this", attachment_ids=(external.id,)
        ),
        "/shared",
    )
    assert store.run_asset(run.run_id, "owner", external.id)[1] == b"External content"
    policy = ChannelPolicy.model_validate(messaging.channels("operator")[0]["policy"])
    changes = {
        "channel": {"enabled": False},
        "sender": {"allowed_senders": ("bob",)},
        "mention": {"group": True, "require_addressed": True},
    }
    messaging.save_channel("operator", "room", policy.model_copy(update=changes[revocation]), 1)
    store = Store(store.path)
    assert store.coordination.inbox("operator")[0]["payload"] is None
    with pytest.raises(ClawError, match="no longer permits context access"):
        store.run_asset(run.run_id, "owner", external.id)
    assert store.run_asset(run.run_id, "owner", human.id)[1] == b"Human content"


def test_worker_message_admitted_once_after_pause_and_keeps_transfer_barrier(runtime_store):
    store = runtime_store
    main, _, run = running_main(store)
    child = store.coordination.create_worker(
        run.run_id,
        "owner",
        WorkerCreate(request_id="worker", title="Worker", profile_id="default", text="Work"),
    )
    store.claim(child["run_id"], "worker", "/shared")
    store.start(child["run_id"], "worker")
    state = store.coordination.view("operator")
    store.coordination.control(
        "operator", ProcessingControl(expected_version=state["version"], paused=True)
    )
    request = InputRequest(request_id="report", text="Worker question must steer Main")
    item = store.coordination.send_collaboration(child["run_id"], "worker", main.id, request)
    store.coordination.drain("/shared")
    assert len(store.inputs("operator", main.id)) == 1
    state = store.coordination.view("operator")
    store.coordination.control(
        "operator", ProcessingControl(expected_version=state["version"], paused=False)
    )
    store = Store(store.path)
    store.coordination.drain("/shared")
    messages = [i for i in store.inputs("operator", main.id) if i.text == request.text]
    assert len(messages) == 1 and messages[0].disposition == "steering"
    saved = store.coordination.inbox("operator", item_id=item["id"])[0]
    assert saved["input_id"] == messages[0].id
    assert (
        store.coordination.send_collaboration(child["run_id"], "worker", main.id, request)[
            "input_id"
        ]
        == messages[0].id
    )
    store.coordination.drain("/shared")
    assert len([i for i in store.inputs("operator", main.id) if i.text == request.text]) == 1
    state = store.coordination.view("operator")
    store.coordination.control(
        "operator", ProcessingControl(expected_version=state["version"], paused=True)
    )
    complete(store, run.run_id)
    successor = store.runs("operator", main.id)[-1]
    with pytest.raises(ClawError, match="paused"):
        store.claim(successor.id, "next", "/shared")
