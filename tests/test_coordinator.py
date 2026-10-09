"""Application integration tests using the published Harness and a native test model."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from a13n_harness import AgentSpec, HarnessBuilder, RunBindings
from a13n_harness.errors import RunError
from pydantic_ai import Tool
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelMessage, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel
from pydantic_ai.tools import DeferredToolResults

from a13n_claw.coordinator import Coordinator, RunRuntime
from a13n_claw.decisions import RESULTS
from a13n_claw.domain import ClawError, InputRequest
from a13n_claw.storage import Store


async def wait_status(store: Store, run_id: str, statuses: set[str]):
    async with asyncio.timeout(10):
        while True:
            run = await asyncio.to_thread(store.run, "operator", run_id)
            if run.status in statuses:
                return run
            await asyncio.sleep(0.01)


def submit(store: Store, thread_id: str, identity: str):
    return store.admit(
        "operator", thread_id, InputRequest(request_id=identity, text=identity), "/shared"
    )


@pytest.mark.anyio
async def test_execution_checkpoint_and_same_thread_continuation(runtime_store: Store):
    histories = []

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        histories.append(messages)
        yield "saved answer"

    @asynccontextmanager
    async def factory(run, checkpoint):
        yield RunRuntime(
            HarnessBuilder(instrumentation=None).build(
                AgentSpec(),
                output_type=str,
                model=FunctionModel(stream_function=model),
                capabilities=(checkpoint,),
            ),
            RunBindings.embedded(),
        )

    target = runtime_store.create_thread("operator", "Test", "default")
    first = submit(runtime_store, target.id, "first")
    coordinator = Coordinator(runtime_store, "/shared", factory)
    await coordinator.start()
    try:
        result = await wait_status(runtime_store, first.run_id, {"completed", "failed"})
        assert result.status == "completed", result.error
        assert result.output == "saved answer"
        assert submit(runtime_store, target.id, "first").disposition == "incorporated"
        second = submit(runtime_store, target.id, "second")
        coordinator.wake()
        result2 = await wait_status(runtime_store, second.run_id, {"completed", "failed"})
        assert result2.status == "completed", result2.error
        assert result2.checkpoint_id != result.checkpoint_id
        assert len(histories[1]) > len(histories[0])
    finally:
        await coordinator.close()


@pytest.mark.anyio
async def test_running_message_is_steered_with_stable_input_identity(runtime_store: Store):
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
        yield "answer"

    @asynccontextmanager
    async def factory(run, checkpoint):
        yield RunRuntime(
            HarnessBuilder(instrumentation=None).build(
                AgentSpec(),
                output_type=str,
                model=FunctionModel(stream_function=model),
                capabilities=(checkpoint,),
            ),
            RunBindings.embedded(),
        )

    target = runtime_store.create_thread("operator", "Test", "default")
    first = submit(runtime_store, target.id, "first")
    coordinator = Coordinator(runtime_store, "/shared", factory)
    await coordinator.start()
    try:
        await asyncio.wait_for(entered.wait(), 10)
        steered = submit(runtime_store, target.id, "second")
        assert steered.run_id == first.run_id
        async with asyncio.timeout(10):
            while submit(runtime_store, target.id, "second").disposition != "delivered":
                await asyncio.sleep(0.01)
        release.set()
        result = await wait_status(runtime_store, first.run_id, {"completed", "failed"})
        assert result.status == "completed", result.error
        assert submit(runtime_store, target.id, "second").disposition == "incorporated"
        assert len(runtime_store.runs("operator", target.id)) == 1
    finally:
        release.set()
        await coordinator.close()


@pytest.mark.anyio
async def test_complete_decision_batch_survives_restart_and_resumes_once(runtime_store: Store):
    effects = []

    def effect(value: int) -> int:
        effects.append(value)
        return value

    async def model(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        if any(isinstance(part, ToolReturnPart) for message in messages for part in message.parts):
            yield "approved result"
        else:
            yield {
                0: DeltaToolCall(name="effect", json_args='{"value": 1}', tool_call_id="call_one"),
                1: DeltaToolCall(name="effect", json_args='{"value": 2}', tool_call_id="call_two"),
            }

    @asynccontextmanager
    async def factory(run, checkpoint):
        yield RunRuntime(
            HarnessBuilder(instrumentation=None).build(
                AgentSpec(),
                output_type=str,
                model=FunctionModel(stream_function=model),
                capabilities=(
                    checkpoint,
                    Capability(id="effects", tools=[Tool(effect, requires_approval=True)]),
                ),
            ),
            RunBindings.embedded(),
        )

    target = runtime_store.create_thread("operator", "Test", "default")
    first = submit(runtime_store, target.id, "first")
    coordinator = Coordinator(runtime_store, "/shared", factory)
    await coordinator.start()
    waiting = await wait_status(runtime_store, first.run_id, {"waiting", "failed"})
    assert waiting.status == "waiting", waiting.error
    assert effects == []
    held = submit(runtime_store, target.id, "held")
    assert held.disposition == "held"
    decisions = runtime_store.decisions("operator", first.run_id)
    decision_id = decisions[0]["id"]
    await coordinator.close()
    reopened = Store(runtime_store.path)
    assert reopened.reconcile_startup() == []
    with pytest.raises(RunError, match="exactly cover"):
        reopened.answer(
            "operator",
            decision_id,
            RESULTS.dump_json(DeferredToolResults(approvals={"call_one": True})).decode(),
        )
    assert reopened.run("operator", first.run_id).status == "waiting"
    answer = RESULTS.dump_json(
        DeferredToolResults(approvals={"call_one": True, "call_two": False})
    ).decode()
    reopened.answer("operator", decision_id, answer)
    reopened.answer("operator", decision_id, answer)
    with pytest.raises(ClawError, match="already accepted"):
        reopened.answer(
            "operator",
            decision_id,
            RESULTS.dump_json(
                DeferredToolResults(approvals={"call_one": False, "call_two": False})
            ).decode(),
        )
    resumed = Coordinator(reopened, "/shared", factory)
    await resumed.start()
    try:
        result = await wait_status(reopened, first.run_id, {"completed", "failed"})
        assert result.status == "completed", result.error
        assert effects == [1]
        assert reopened.answer("operator", decision_id, answer).status == "completed"
        assert effects == [1]
    finally:
        await resumed.close()


@pytest.mark.anyio
async def test_active_cancel_stops_harness_before_publishing_cancelled(runtime_store: Store):
    entered = asyncio.Event()

    async def model(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        entered.set()
        await asyncio.Event().wait()
        yield "unreachable"

    @asynccontextmanager
    async def factory(run, checkpoint):
        yield RunRuntime(
            HarnessBuilder(instrumentation=None).build(
                AgentSpec(),
                output_type=str,
                model=FunctionModel(stream_function=model),
                capabilities=(checkpoint,),
            ),
            RunBindings.embedded(),
        )

    target = runtime_store.create_thread("operator", "Test", "default")
    first = submit(runtime_store, target.id, "first")
    coordinator = Coordinator(runtime_store, "/shared", factory)
    await coordinator.start()
    try:
        await asyncio.wait_for(entered.wait(), 10)
        assert runtime_store.cancel("operator", first.run_id).status == "running"
        result = await wait_status(runtime_store, first.run_id, {"cancelled", "failed"})
        assert result.status == "cancelled", result.error
        assert result.checkpoint_id is not None
    finally:
        await coordinator.close()


@pytest.mark.anyio
async def test_cancel_during_preparation_does_not_wait_for_provider(runtime_store: Store):
    preparing = asyncio.Event()
    stopped = asyncio.Event()

    @asynccontextmanager
    async def factory(run, checkpoint):
        preparing.set()
        try:
            await asyncio.Event().wait()
            raise AssertionError("Preparation must be cancelled")
            yield  # pragma: no cover
        finally:
            stopped.set()

    target = runtime_store.create_thread("operator", "Test", "default")
    first = submit(runtime_store, target.id, "first")
    coordinator = Coordinator(runtime_store, "/shared", factory)
    await coordinator.start()
    try:
        await asyncio.wait_for(preparing.wait(), 10)
        runtime_store.cancel("operator", first.run_id)
        coordinator.wake()
        result = await wait_status(runtime_store, first.run_id, {"cancelled", "failed"})
        assert result.status == "cancelled", result.error
        assert stopped.is_set()
        assert submit(runtime_store, target.id, "first").disposition == "unapplied"
    finally:
        await coordinator.close()


@pytest.mark.anyio
async def test_revoked_client_cannot_execute_tools_after_model_call(runtime_store: Store):
    from a13n_claw.domain import Principal

    entered = asyncio.Event()
    release = asyncio.Event()
    effects = []

    def effect() -> str:
        effects.append("executed")
        return "done"

    async def model(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        entered.set()
        await release.wait()
        yield {0: DeltaToolCall(name="effect", json_args="{}", tool_call_id="call_one")}

    @asynccontextmanager
    async def factory(run, checkpoint):
        yield RunRuntime(
            HarnessBuilder(instrumentation=None).build(
                AgentSpec(),
                output_type=str,
                model=FunctionModel(stream_function=model),
                capabilities=(checkpoint, Capability(id="effects", tools=[Tool(effect)])),
            ),
            RunBindings.embedded(),
        )

    target = runtime_store.create_thread("operator", "Test", "default")
    actor = Principal(
        id="client", actions=("submit", "read"), thread_ids=(target.id,), profile_ids=("default",)
    )
    runtime_store.set_principal("operator", actor, "client-hash")
    first = runtime_store.admit(
        "client", target.id, InputRequest(request_id="first", text="work"), "/shared"
    )
    coordinator = Coordinator(runtime_store, "/shared", factory)
    await coordinator.start()
    try:
        await asyncio.wait_for(entered.wait(), 10)
        runtime_store.set_principal(
            "operator", actor.model_copy(update={"enabled": False}), "client-hash"
        )
        release.set()
        result = await wait_status(runtime_store, first.run_id, {"completed", "failed"})
        assert result.status == "failed"
        assert effects == []
    finally:
        release.set()
        await coordinator.close()


@pytest.mark.anyio
async def test_recovery_never_replays_an_approved_unknown_effect(runtime_store: Store):
    entered = asyncio.Event()
    effects = []
    recovered_history = []

    async def effect() -> str:
        effects.append("external change")
        entered.set()
        await asyncio.Event().wait()
        return "unreachable"

    async def model(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        if any(isinstance(part, ToolReturnPart) for message in messages for part in message.parts):
            recovered_history.extend(messages)
            yield "Recovered without repeating the change"
        else:
            yield {0: DeltaToolCall(name="effect", json_args="{}", tool_call_id="call_change")}

    @asynccontextmanager
    async def factory(run, checkpoint):
        yield RunRuntime(
            HarnessBuilder(instrumentation=None).build(
                AgentSpec(),
                output_type=str,
                model=FunctionModel(stream_function=model),
                capabilities=(
                    checkpoint,
                    Capability(id="effect", tools=[Tool(effect, requires_approval=True)]),
                ),
            ),
            RunBindings.embedded(),
        )

    target = runtime_store.create_thread("operator", "Recover", "default")
    first = submit(runtime_store, target.id, "first")
    coordinator = Coordinator(runtime_store, "/shared", factory)
    await coordinator.start()
    try:
        assert (
            await wait_status(runtime_store, first.run_id, {"waiting", "failed"})
        ).status == "waiting"
        decision_id = runtime_store.decisions("operator", first.run_id)[0]["id"]
        runtime_store.answer(
            "operator",
            decision_id,
            RESULTS.dump_json(DeferredToolResults(approvals={"call_change": True})).decode(),
        )
        coordinator.wake()
        await asyncio.wait_for(entered.wait(), 10)
        runtime_store.cancel("operator", first.run_id)
        stopped = await wait_status(runtime_store, first.run_id, {"failed", "cancelled"})
        assert stopped.status == "cancelled", stopped.error
        assert stopped.recovery_required
    finally:
        await coordinator.close()
    reopened = Store(runtime_store.path)
    reopened.reconcile_run(
        "operator", first.run_id, "Verified the external change; do not repeat it"
    )
    recovery = reopened.recover(
        "operator", first.run_id, "recover-1", "Report the reviewed result", "/shared"
    )
    assert (
        reopened.recover(
            "operator", first.run_id, "recover-1", "Report the reviewed result", "/shared"
        ).id
        == recovery.id
    )
    with pytest.raises(ClawError, match="identity was reused"):
        reopened.recover("operator", first.run_id, "recover-1", "Different instruction", "/shared")
    resumed = Coordinator(reopened, "/shared", factory)
    await resumed.start()
    try:
        result = await wait_status(reopened, recovery.id, {"completed", "failed", "waiting"})
        assert result.status == "completed", result.error
        assert result.recovery_of == first.run_id
        assert effects == ["external change"]
        assert recovered_history
        assert reopened.run("operator", first.run_id).status == "cancelled"
    finally:
        await resumed.close()


@pytest.mark.anyio
async def test_runtime_cleanup_cannot_overwrite_saved_completion(runtime_store: Store):
    async def model(messages, info):
        yield "saved"

    @asynccontextmanager
    async def factory(run, checkpoint):
        yield RunRuntime(
            HarnessBuilder(instrumentation=None).build(
                AgentSpec(),
                output_type=str,
                model=FunctionModel(stream_function=model),
                capabilities=(checkpoint,),
            ),
            RunBindings.embedded(),
        )
        raise RuntimeError("Provider cleanup failed")

    target = runtime_store.create_thread("operator", "Cleanup", "default")
    receipt = submit(runtime_store, target.id, "first")
    coordinator = Coordinator(runtime_store, "/shared", factory)
    await coordinator.start()
    await wait_status(runtime_store, receipt.run_id, {"completed", "failed"})
    await coordinator.close()
    assert runtime_store.run("operator", receipt.run_id).output == "saved"
    assert runtime_store.run("operator", receipt.run_id).status == "completed"
    assert coordinator.failure is None


@pytest.mark.anyio
async def test_cancel_before_executor_entry_retires_claimed_owner(runtime_store: Store):
    @asynccontextmanager
    async def factory(run, checkpoint):
        raise AssertionError("Cancelled task must not construct a runtime")
        yield

    target = runtime_store.create_thread("operator", "Never entered", "default")
    receipt = submit(runtime_store, target.id, "first")
    run = runtime_store.claim(receipt.run_id, "owner", "/shared")
    coordinator = Coordinator(runtime_store, "/shared", factory)
    coordinator.started[run.id] = "owner"
    task = asyncio.create_task(coordinator._execute(run, "owner"))
    coordinator.active[run.id] = task
    task.add_done_callback(lambda done: coordinator._retired(run.id, done))
    task.cancel()
    await coordinator.close()
    result = runtime_store.run("operator", run.id)
    assert result.status == "cancelled"
    assert result.owner is None
    assert submit(runtime_store, target.id, "first").disposition == "unapplied"


@pytest.mark.anyio
async def test_dispatch_failure_is_observable_and_shutdown_still_joins_active_work(
    runtime_store: Store, monkeypatch
):
    entered = asyncio.Event()
    cleaned = asyncio.Event()

    @asynccontextmanager
    async def factory(run, checkpoint):
        entered.set()
        try:
            await asyncio.Event().wait()
            yield
        finally:
            cleaned.set()

    target = runtime_store.create_thread("operator", "Dispatch failure", "default")
    receipt = submit(runtime_store, target.id, "first")
    coordinator = Coordinator(runtime_store, "/shared", factory)
    await coordinator.start()
    await asyncio.wait_for(entered.wait(), 10)

    def fail():
        raise OSError("Storage unavailable")

    monkeypatch.setattr(runtime_store, "queued", fail)
    coordinator.wake()
    async with asyncio.timeout(10):
        while coordinator.failure is None:
            await asyncio.sleep(0.01)
    await coordinator.close()
    assert cleaned.is_set()
    assert coordinator.failure == "dispatch_failed"
    assert runtime_store.run("operator", receipt.run_id).status == "cancelled"
