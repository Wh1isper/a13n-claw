"""Durable application boundaries independent of model/provider timing."""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from a13n_harness import HarnessState

from a13n_claw.domain import ClawError, InputRequest, Principal
from a13n_claw.storage import Store


@pytest.fixture
def store(tmp_path: Path) -> Store:
    value = Store(tmp_path / "claw.sqlite3")
    value.bootstrap("operator-token-hash")
    value.save_resource(
        "operator",
        "model",
        "model",
        {
            "provider": "openai",
            "model_name": "test",
            "credential_env": "TEST_KEY",
        },
        0,
    )
    value.save_resource("operator", "environment", "local", {"kind": "local"}, 0)
    value.save_resource(
        "operator",
        "profile",
        "default",
        {
            "model_id": "model",
            "environment_id": "local",
        },
        0,
    )
    return value


def thread(store: Store):
    return store.create_thread("operator", "Conversation", "default")


def admit(store: Store, thread_id: str, request_id: str, **kwargs):
    return store.admit(
        "operator",
        thread_id,
        InputRequest(request_id=request_id, text=request_id, **kwargs),
        "/shared",
    )


def running(store: Store, thread_id: str):
    receipt = admit(store, thread_id, "initial")
    store.claim(receipt.run_id, "owner", "/shared")
    store.start(receipt.run_id, "owner")
    return receipt


def test_identified_admission_is_atomic_under_concurrent_retries(store: Store):
    target = thread(store)
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(lambda _: admit(store, target.id, "same"), range(20)))
    assert len({receipt.id for receipt in receipts}) == 1
    assert len(store.runs("operator", target.id)) == 1
    assert len(store.inputs("operator", target.id)) == 1
    with pytest.raises(ClawError, match="different input"):
        store.admit(
            "operator", target.id, InputRequest(request_id="same", text="changed"), "/shared"
        )


def test_admission_and_queued_join_keep_captured_composition(store: Store):
    target = thread(store)
    first = admit(store, target.id, "first")
    store.save_resource(
        "operator",
        "model",
        "model",
        {
            "provider": "openai",
            "model_name": "changed",
            "credential_env": "TEST_KEY",
        },
        1,
    )
    second = admit(store, target.id, "second")
    assert second.run_id == first.run_id
    assert store.run("operator", first.run_id).composition.model.content["model_name"] == "test"
    store.claim(first.run_id, "owner", "/shared")
    third = admit(store, target.id, "third")
    assert third.run_id == first.run_id
    assert [item.text for item in store.start(first.run_id, "owner")] == [
        "first",
        "second",
        "third",
    ]
    steered = admit(store, target.id, "fourth")
    assert steered.disposition == "steering"
    assert store.take_steering(first.run_id, "owner")[0].id == steered.id


def test_combined_selection_conflict_rolls_back_without_receipt(store: Store):
    target = thread(store)
    admit(store, target.id, "first")
    with pytest.raises(ClawError, match="separate Run"):
        admit(store, target.id, "second", profile_id="default", expected_thread_version=1)
    assert store.thread("operator", target.id).version == 1
    assert len(store.inputs("operator", target.id)) == 1
    with pytest.raises(ClawError, match="Missing profile"):
        admit(
            store,
            target.id,
            "invalid",
            separate_run=True,
            profile_id="missing",
            expected_thread_version=1,
        )
    assert store.thread("operator", target.id).version == 1


def test_resource_version_rejects_lost_update(store: Store):
    with pytest.raises(ClawError, match="preserve your draft"):
        store.save_resource("operator", "environment", "local", {"kind": "local"}, 0)
    assert next(r for r in store.resources("operator") if r.kind == "environment").version == 1


def test_checkpoint_and_terminal_outcome_are_atomic(store: Store):
    target = thread(store)
    receipt = running(store, target.id)
    with pytest.raises(ClawError, match="another Thread"):
        store.publish(
            receipt.run_id, "owner", HarnessState.new(thread_id="thread_wrong"), status="completed"
        )
    assert store.thread("operator", target.id).checkpoint_id is None
    assert store.run("operator", receipt.run_id).status == "running"
    with pytest.raises(ClawError, match="outside this execution"):
        store.publish(
            receipt.run_id,
            "owner",
            HarnessState.new(thread_id=target.id),
            incorporated=("unknown",),
            status="completed",
        )
    assert store.thread("operator", target.id).checkpoint_id is None
    checkpoint = store.publish(
        receipt.run_id,
        "owner",
        HarnessState.new(thread_id=target.id),
        incorporated=(receipt.id,),
        status="completed",
        output="saved",
    )
    assert store.thread("operator", target.id).checkpoint_id == checkpoint
    assert store.checkpoint(checkpoint).thread_id == target.id
    assert store.run("operator", receipt.run_id).output == "saved"
    assert admit(store, target.id, "initial").disposition == "incorporated"
    with pytest.raises(ClawError, match="no longer owns"):
        store.publish(
            receipt.run_id, "owner", HarnessState.new(thread_id=target.id), status="completed"
        )


def test_separate_queue_selects_latest_checkpoint_at_start(store: Store):
    target = thread(store)
    first = running(store, target.id)
    second = admit(store, target.id, "second", separate_run=True)
    with pytest.raises(ClawError, match="Earlier work"):
        store.claim(second.run_id, "second-owner", "/shared")
    checkpoint = store.publish(
        first.run_id,
        "owner",
        HarnessState.new(thread_id=target.id),
        incorporated=(first.id,),
        status="completed",
    )
    assert store.claim(second.run_id, "second-owner", "/shared").checkpoint_id == checkpoint


def test_completion_transfers_only_confirmed_undelivered_input_once(store: Store):
    target = thread(store)
    first = running(store, target.id)
    late = admit(store, target.id, "late")
    store.publish(
        first.run_id,
        "owner",
        HarnessState.new(thread_id=target.id),
        incorporated=(first.id,),
        status="completed",
    )
    replay = admit(store, target.id, "late")
    assert replay.id == late.id
    assert replay.run_id != first.run_id
    assert replay.disposition == "pending"
    assert len(store.runs("operator", target.id)) == 2
    assert admit(store, target.id, "late") == replay


def test_transfer_prefers_existing_queued_run(store: Store):
    target = thread(store)
    first = running(store, target.id)
    queued = admit(store, target.id, "scheduled", separate_run=True)
    admit(store, target.id, "late")
    store.publish(
        first.run_id,
        "owner",
        HarnessState.new(thread_id=target.id),
        incorporated=(first.id,),
        status="completed",
    )
    assert admit(store, target.id, "late").run_id == queued.run_id
    assert len(store.runs("operator", target.id)) == 2


def test_delivery_without_saved_evidence_blocks_replay(store: Store):
    target = thread(store)
    first = running(store, target.id)
    late = admit(store, target.id, "late")
    store.take_steering(first.run_id, "owner")
    store.steering_delivered(first.run_id, "owner", late.id, accepted=True)
    store.publish(
        first.run_id,
        "owner",
        HarnessState.new(thread_id=target.id),
        incorporated=(first.id,),
        status="completed",
    )
    assert admit(store, target.id, "late").disposition == "uncertain"
    next_input = admit(store, target.id, "next")
    assert next_input.disposition == "blocked"
    with pytest.raises(ClawError, match="unresolved inputs"):
        store.claim(next_input.run_id, "another", "/shared")


def test_restart_retains_receipts_and_interrupts_without_replay(store: Store):
    target = thread(store)
    first = running(store, target.id)
    late = admit(store, target.id, "late")
    reopened = Store(store.path)
    assert reopened.reconcile_startup() == [first.run_id]
    assert reopened.run("operator", first.run_id).status == "interrupted"
    assert admit(reopened, target.id, "initial").disposition == "uncertain"
    assert admit(reopened, target.id, "late").id == late.id
    assert admit(reopened, target.id, "late").disposition == "unapplied"
    with pytest.raises(ClawError, match="no longer owns"):
        store.finish_without_checkpoint(first.run_id, "owner", status="failed", error="late writer")
    assert reopened.reconcile_startup() == []


def test_cancellation_records_intent_and_does_not_fake_stopped_execution(store: Store):
    target = thread(store)
    first = running(store, target.id)
    cancelled = store.cancel("operator", first.run_id)
    assert cancelled.status == "running" and cancelled.cancel_requested
    store.publish(
        first.run_id,
        "owner",
        HarnessState.new(thread_id=target.id),
        incorporated=(first.id,),
        status="completed",
    )
    assert store.cancel("operator", first.run_id).status == "completed"
    queued = admit(store, target.id, "queued")
    assert store.cancel("operator", queued.run_id).status == "cancelled"
    assert admit(store, target.id, "queued").disposition == "unapplied"


def test_current_grants_apply_to_admission_and_dispatch(store: Store):
    target = thread(store)
    delegate = Principal(
        id="client", thread_ids=(target.id,), profile_ids=("default",), actions=("read", "submit")
    )
    store.set_principal("operator", delegate, "client-hash")
    request = InputRequest(request_id="delegated", text="work")
    receipt = store.admit("client", target.id, request, "/shared")
    store.set_principal("operator", delegate.model_copy(update={"enabled": False}), "client-hash")
    with pytest.raises(ClawError, match="disabled"):
        store.claim(receipt.run_id, "owner", "/shared")
    with pytest.raises(ClawError, match="disabled"):
        store.admit("client", target.id, request, "/shared")
    assert store.run("operator", receipt.run_id).status == "queued"


def test_scope_is_not_inferred_from_identifier(store: Store):
    target = thread(store)
    delegate = Principal(id="client", profile_ids=("default",), actions=("read", "submit"))
    store.set_principal("operator", delegate, "client-hash")
    with pytest.raises(ClawError, match="not permitted"):
        store.thread("client", target.id)
    with pytest.raises(ClawError, match="not permitted"):
        store.admit("client", target.id, InputRequest(request_id="wrong", text="work"), "/shared")
    assert store.threads("client") == []


def test_backup_restores_consistent_heads_and_immutable_values(store: Store, tmp_path: Path):
    target = thread(store)
    receipt = running(store, target.id)
    checkpoint = store.publish(
        receipt.run_id,
        "owner",
        HarnessState.new(thread_id=target.id),
        incorporated=(receipt.id,),
        status="completed",
    )
    destination = tmp_path / "backup.sqlite3"
    store.backup(destination)
    restored = Store(destination)
    assert restored.thread("operator", target.id).checkpoint_id == checkpoint
    assert restored.checkpoint(checkpoint).thread_id == target.id
    assert restored.run("operator", receipt.run_id).status == "completed"
    with pytest.raises(ClawError, match="already exists"):
        store.backup(destination)


def test_interrupted_execution_blocks_new_work_even_if_all_inputs_were_saved(store: Store):
    target = thread(store)
    first = running(store, target.id)
    store.publish(
        first.run_id, "owner", HarnessState.new(thread_id=target.id), incorporated=(first.id,)
    )
    store.reconcile_startup()
    assert store.run("operator", first.run_id).recovery_required
    assert admit(store, target.id, "initial").disposition == "incorporated"
    following = admit(store, target.id, "following")
    assert following.disposition == "blocked"
    with pytest.raises(ClawError, match="unresolved inputs"):
        store.claim(following.run_id, "new-owner", "/shared")
    with pytest.raises(ClawError, match="Review uncertain effects"):
        store.release_blocked("operator", target.id, "/shared")
    store.reconcile_run(
        "operator", first.run_id, "Inspected external effects and stopped processes"
    )
    with pytest.raises(ClawError, match="Review uncertain effects"):
        store.release_blocked("operator", target.id, "/shared")
    recovery = store.recover(
        "operator", first.run_id, "recover", "Continue after review", "/shared"
    )
    assert recovery.id != first.run_id
    assert recovery.recovery_of == first.run_id
    assert store.run("operator", first.run_id).status == "interrupted"
    with pytest.raises(ClawError, match="Earlier work"):
        store.claim(following.run_id, "new-owner", "/shared")
    store.claim(recovery.id, "recovery-owner", "/shared")
    initial = store.start(recovery.id, "recovery-owner")
    store.publish(
        recovery.id,
        "recovery-owner",
        HarnessState.new(thread_id=target.id),
        incorporated=tuple(item.id for item in initial),
        status="completed",
    )
    store.release_blocked("operator", target.id, "/shared")
    assert store.claim(following.run_id, "new-owner", "/shared").status == "preparing"


def test_revoked_late_sender_does_not_roll_back_saved_completion(store: Store):
    target = thread(store)
    first = running(store, target.id)
    actor = Principal(
        id="client", actions=("submit",), thread_ids=(target.id,), profile_ids=("default",)
    )
    store.set_principal("operator", actor, "client-hash")
    late = store.admit("client", target.id, InputRequest(request_id="late", text="late"), "/shared")
    store.set_principal("operator", actor.model_copy(update={"enabled": False}), "client-hash")
    store.publish(
        first.run_id,
        "owner",
        HarnessState.new(thread_id=target.id),
        incorporated=(first.id,),
        status="completed",
        output="retained",
    )
    assert store.run("operator", first.run_id).output == "retained"
    assert (
        next(item for item in store.inputs("operator", target.id) if item.id == late.id).disposition
        == "blocked"
    )


def test_database_failure_rolls_back_checkpoint_head_outcome_and_input_proof(store: Store):
    target = thread(store)
    receipt = running(store, target.id)
    before = store.inputs("operator", target.id)
    with sqlite3.connect(store.path) as db:
        db.execute(
            "CREATE TRIGGER fail_terminal BEFORE UPDATE OF status ON runs "
            "WHEN NEW.status = 'completed' BEGIN SELECT RAISE(ABORT, 'injected failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="injected failure"):
        store.publish(
            receipt.run_id,
            "owner",
            HarnessState.new(thread_id=target.id),
            incorporated=(receipt.id,),
            status="completed",
            output="must roll back",
        )
    reopened = Store(store.path)
    assert reopened.thread("operator", target.id).checkpoint_id is None
    result = reopened.run("operator", receipt.run_id)
    assert result.status == "running" and result.output is None and result.checkpoint_id is None
    assert reopened.inputs("operator", target.id) == before
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM checkpoints").fetchone()[0] == 0
        db.execute("DROP TRIGGER fail_terminal")
    checkpoint = reopened.publish(
        receipt.run_id,
        "owner",
        HarnessState.new(thread_id=target.id),
        incorporated=(receipt.id,),
        status="completed",
        output="committed",
    )
    assert reopened.thread("operator", target.id).checkpoint_id == checkpoint
    assert reopened.run("operator", receipt.run_id).output == "committed"


def test_implicit_created_thread_grant_invalidates_old_client_editor(store: Store):
    original = Principal(id="creator", actions=("read", "create"), profile_ids=("default",))
    store.set_principal("operator", original, "creator-hash")
    created = store.create_thread("creator", "Created", "default")
    with pytest.raises(ClawError, match="Client grants changed"):
        store.set_principal("operator", original, None)
    current = store.principal("creator")
    assert current.version == 2 and current.thread_ids == (created.id,)
