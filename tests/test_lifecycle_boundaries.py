"""Regressions at durable admission, native shutdown, and cancellation boundaries."""

import asyncio
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

import pytest
from a13n_harness import HarnessState
from a13n_harness.toolsets.shell import ShellToolset
from pydantic_ai.messages import ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.tools import DeferredToolResults
from test_recovery import waiting
from test_runtime import terminal

from a13n_claw.coordinator import Coordinator
from a13n_claw.decisions import RESULTS
from a13n_claw.domain import ClawError, InputRequest, Principal, ProfileDefinition
from a13n_claw.environments import EnvironmentManager, target_definition
from a13n_claw.runtime import Runtime
from a13n_claw.storage import Store


@pytest.mark.parametrize("discard_next", [False, True])
def test_discard_revoked_late_input_restores_thread_without_regrant(runtime_store, discard_next):
    store = runtime_store
    thread = store.create_thread("operator", "Work", "default")
    first = store.admit(
        "operator", thread.id, InputRequest(request_id="first", text="First"), "/shared"
    )
    store.claim(first.run_id, "owner", "/shared")
    store.start(first.run_id, "owner")
    actor = Principal(
        id="client", actions=("submit",), thread_ids=(thread.id,), profile_ids=("default",)
    )
    store.set_principal("operator", actor, "test-hash")
    late = store.admit("client", thread.id, InputRequest(request_id="late", text="Late"), "/shared")
    store.set_principal("operator", actor.model_copy(update={"enabled": False}), None)
    store.publish(
        first.run_id,
        "owner",
        HarnessState.new(thread_id=thread.id),
        incorporated=(first.id,),
        status="completed",
    )
    following = store.admit(
        "operator", thread.id, InputRequest(request_id="next", text="Next"), "/shared"
    )
    assert following.disposition == "blocked"
    if discard_next:
        store.discard_blocked("operator", following.id, "No longer wanted")
    with pytest.raises(ClawError, match="disabled"):
        store.discard_blocked("client", late.id, "Discard")
    with pytest.raises(ClawError, match="Only blocked"):
        store.discard_blocked("operator", first.id, "Cannot discard incorporated work")
    assert (
        store.discard_blocked("operator", late.id, "Sender revoked; never delivered").disposition
        == "unapplied"
    )
    assert (
        store.discard_blocked("operator", late.id, "Retry after lost reply").disposition
        == "unapplied"
    )
    store.release_blocked("operator", thread.id, "/shared")
    if discard_next:
        assert following.run_id not in store.queued()
        assert store.run("operator", following.run_id).status == "cancelled"
    else:
        assert store.claim(following.run_id, "next-owner", "/shared").status == "preparing"
    with pytest.raises(ClawError, match="disabled"):
        store.principal("client")
    with sqlite3.connect(store.path) as db:
        assert db.execute(
            "SELECT count(*) FROM audit WHERE kind='blocked_input_discarded'"
        ).fetchone()[0] == (2 if discard_next else 1)


def test_resume_starts_old_held_and_new_pending_in_receipt_order(runtime_store):
    store = runtime_store
    thread, first, _, _, answer, decision = waiting(store)
    older = store.admit(
        "operator", thread.id, InputRequest(request_id="A", text="Older"), "/shared"
    )
    store.answer("operator", decision, RESULTS.dump_json(answer).decode())
    newer = store.admit(
        "operator", thread.id, InputRequest(request_id="B", text="Newer"), "/shared"
    )
    store.claim(first.run_id, "resumed", "/shared")
    assert [item.id for item in store.start(first.run_id, "resumed")] == [older.id, newer.id]
    assert store.take_steering(first.run_id, "resumed") == []


def test_creation_identity_is_atomic_and_does_not_rediscover_defaults(runtime_store):
    store = runtime_store
    store.save_resource("operator", "defaults", "instance", {"profile_id": "default"}, 0)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _: store.create_thread("operator", "Work", request_id="create"), range(8)
            )
        )
    assert len({item.id for item in results}) == 1
    store.save_resource("operator", "defaults", "instance", {"profile_id": None}, 1)
    assert store.create_thread("operator", "Work", request_id="create").id == results[0].id
    with pytest.raises(ClawError, match="identity was reused"):
        store.create_thread("operator", "Different", request_id="create")


@pytest.mark.parametrize("version", [1, 2])
def test_schema_upgrade_retains_existing_history(tmp_path, version):
    from a13n_claw.storage import _SCHEMA

    path = tmp_path / "legacy.sqlite3"
    # Build the actual old schema, not a new database with a false version marker.
    with sqlite3.connect(path) as db:
        db.executescript(_SCHEMA)
        if version == 1:
            db.execute("DROP TABLE thread_creations")
        db.execute(f"PRAGMA user_version={version}")
        db.execute(
            "INSERT INTO threads(id,title,profile_id,created_at) "
            "VALUES ('retained','Retained','default','2026-01-01')"
        )
    reopened = Store(path)
    reopened.bootstrap("operator-token-hash")
    thread = reopened.thread("operator", "retained")
    assert thread.title == "Retained" and thread.active and thread.owner_main_id is None
    assert reopened.coordination.mode() == "per_channel"
    assert Store(path).thread("operator", thread.id) == thread


def test_legacy_profile_limits_normalize_to_one_native_owner():
    profile = ProfileDefinition.model_validate(
        {
            "model_id": "model",
            "environment_id": "local",
            "max_requests": 8,
            "agent": {"usage_limits": {"tool_calls_limit": 0}},
        }
    )
    assert profile.agent.usage_limits.request_limit == 8
    assert profile.agent.usage_limits.tool_calls_limit == 0
    assert "max_requests" not in profile.model_dump()


@pytest.mark.anyio
@pytest.mark.parametrize("boundary", ["manage", "acquire"])
async def test_cancelled_target_intent_settles_before_next_operation(
    runtime_store, tmp_path, monkeypatch, boundary
):
    store = runtime_store
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    thread = store.create_thread("operator", "Target", "default")
    receipt = store.admit(
        "operator", thread.id, InputRequest(request_id="work", text="Work"), str(workspace)
    )
    run = store.claim(receipt.run_id, "owner", str(workspace))
    target = store.target_for(run.id, "owner", target_definition(run))
    manager = EnvironmentManager(store)
    entered, release, settled = threading.Event(), threading.Event(), threading.Event()
    original = store.target_operation

    def delayed(target_id, operation):
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
            try:
                return original(target_id, operation)
            finally:
                settled.set()
        return original(target_id, operation)

    monkeypatch.setattr(store, "target_operation", delayed)

    async def use():
        if boundary == "manage":
            await manager.manage("operator", target.id, "inspect")
        else:
            async with manager.acquire(run):
                pytest.fail("Cancelled acquisition exposed access")

    task = asyncio.create_task(use())
    following = None
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert manager.locks[target.id].locked() and not task.done()
        following = asyncio.create_task(manager.manage("operator", target.id, "prepare"))
        await asyncio.sleep(0)
        assert not following.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        if following is not None:
            ready = await asyncio.wait_for(following, 5)
            assert settled.is_set()
            assert store.target("operator", target.id) == ready
            assert ready.status == "ready"


@pytest.mark.anyio
async def test_native_limits_prevent_first_tool_in_production_runtime(runtime_store, tmp_path):
    store = runtime_store
    store.save_credential("operator", "TEST_KEY", "test-only")
    store.save_resource(
        "operator",
        "profile",
        "default",
        {
            "model_id": "model",
            "environment_id": "local",
            "agent": {"usage_limits": {"tool_calls_limit": 0}},
        },
        1,
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "source.txt").write_text("Should not be read")
    thread = store.create_thread("operator", "Limits", "default")
    receipt = store.admit(
        "operator", thread.id, InputRequest(request_id="work", text="Read"), str(workspace)
    )

    async def model(messages, info):
        yield {
            0: DeltaToolCall(
                name="view", json_args='{"file_path":"/workspace/source.txt"}', tool_call_id="read"
            )
        }

    @asynccontextmanager
    async def models(definition, credential):
        yield FunctionModel(stream_function=model)

    coordinator = Coordinator(store, str(workspace), Runtime(store, tmp_path, models=models))
    await coordinator.start()
    try:
        result = await terminal(store, receipt.run_id)
        assert result.status == "failed" and result.error == "usage_limit_exceeded"
        state = store.checkpoint(result.checkpoint_id)
        # Harness may normalize interrupted calls with synthetic tool returns.
        assert "Should not be read" not in state.model_dump_json()
    finally:
        await coordinator.close()


@pytest.mark.anyio
@pytest.mark.parametrize("suspended", [False, True])
async def test_cleanup_failure_retains_complete_native_outcome(
    runtime_store, tmp_path, monkeypatch, suspended
):
    store = runtime_store
    store.save_credential("operator", "TEST_KEY", "test-only")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "result.txt").write_text("Completed bytes")
    store.save_resource(
        "operator",
        "profile",
        "default",
        {
            "model_id": "model",
            "environment_id": "local",
            "permissions": {"rules": {"claw.files.retain": "ask"}},
        },
        1,
    )
    thread = store.create_thread("operator", "Cleanup", "default")
    receipt = store.admit(
        "operator", thread.id, InputRequest(request_id="work", text="Work"), str(workspace)
    )
    original = ShellToolset.close

    async def broken_close(self):
        await original(self)
        raise RuntimeError("Injected cleanup failure")

    monkeypatch.setattr(ShellToolset, "close", broken_close)

    async def model(messages, info):
        if suspended and not any(
            isinstance(part, ToolReturnPart) for message in messages for part in message.parts
        ):
            yield {
                0: DeltaToolCall(
                    name="retain_artifact",
                    json_args='{"path":"/workspace/result.txt","name":"result.txt"}',
                    tool_call_id="retain",
                )
            }
        else:
            yield "Unique complete answer"

    @asynccontextmanager
    async def models(definition, credential):
        yield FunctionModel(stream_function=model)

    factory = Runtime(store, tmp_path, models=models)
    coordinator = Coordinator(store, str(workspace), factory)
    await coordinator.start()
    try:
        result = await terminal(store, receipt.run_id)
        assert result.status == "failed" and result.error == "execution_failed"
        state = store.checkpoint(result.checkpoint_id)
        assert store.inputs("operator", thread.id)[0].disposition == "incorporated"
        if not suspended:
            assert result.output == "Unique complete answer"
            assert "Unique complete answer" in state.model_dump_json()
        else:
            assert (
                store.decisions("operator", result.id)[0]["requests"]["approvals"][0][
                    "tool_call_id"
                ]
                == "retain"
            )
    finally:
        await coordinator.close()
    if suspended:
        monkeypatch.setattr(ShellToolset, "close", original)
        store.reconcile_run("operator", result.id, "Cleanup stopped; no tool executed")
        recovery = store.recover("operator", result.id, "recover", "Continue", str(workspace))
        assert recovery.status == "waiting"
        decision = store.decisions("operator", recovery.id)[0]
        store.answer(
            "operator",
            decision["id"],
            RESULTS.dump_json(DeferredToolResults(approvals={"retain": True})).decode(),
        )
        resumed = Coordinator(store, str(workspace), factory)
        await resumed.start()
        try:
            assert (await terminal(store, recovery.id)).status == "completed"
            assert len(store.assets("operator", thread.id)) == 1
        finally:
            await resumed.close()


@pytest.mark.anyio
async def test_decision_resume_preserves_model_visible_message_order(runtime_store, tmp_path):
    store = runtime_store
    store.save_credential("operator", "TEST_KEY", "test-only")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "result.txt").write_text("Result")
    store.save_resource(
        "operator",
        "profile",
        "default",
        {
            "model_id": "model",
            "environment_id": "local",
            "permissions": {"rules": {"claw.files.retain": "ask"}},
        },
        1,
    )
    thread = store.create_thread("operator", "Ordered", "default")
    receipt = store.admit(
        "operator", thread.id, InputRequest(request_id="first", text="First"), str(workspace)
    )
    observed = []

    async def model(messages, info):
        if any(isinstance(part, ToolReturnPart) for message in messages for part in message.parts):
            observed.extend(
                str(part.content)
                for message in messages
                for part in message.parts
                if isinstance(part, UserPromptPart)
            )
            yield "Finished"
        else:
            yield {
                0: DeltaToolCall(
                    name="retain_artifact",
                    json_args='{"path":"/workspace/result.txt","name":"result.txt"}',
                    tool_call_id="retain",
                )
            }

    @asynccontextmanager
    async def models(definition, credential):
        yield FunctionModel(stream_function=model)

    factory = Runtime(store, tmp_path, models=models)
    coordinator = Coordinator(store, str(workspace), factory)
    await coordinator.start()
    try:
        assert (await terminal(store, receipt.run_id)).status == "waiting"
    finally:
        await coordinator.close()
    older = store.admit(
        "operator",
        thread.id,
        InputRequest(request_id="older", text="older-A-marker"),
        str(workspace),
    )
    decision = store.decisions("operator", receipt.run_id)[0]
    store.answer(
        "operator",
        decision["id"],
        RESULTS.dump_json(DeferredToolResults(approvals={"retain": True})).decode(),
    )
    newer = store.admit(
        "operator",
        thread.id,
        InputRequest(request_id="newer", text="newer-B-marker"),
        str(workspace),
    )
    resumed = Coordinator(store, str(workspace), factory)
    await resumed.start()
    try:
        assert (await terminal(store, receipt.run_id)).status == "completed"
        combined = "\n".join(observed)
        assert combined.index("older-A-marker") < combined.index("newer-B-marker")
        applied = {
            item.id
            for item in store.inputs("operator", thread.id)
            if item.disposition == "incorporated"
        }
        assert {older.id, newer.id} <= applied
    finally:
        await resumed.close()
