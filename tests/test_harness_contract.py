"""Published dependency contracts required by Claw's execution coordinator.

These checks use a deterministic native model, not a replacement execution engine.
They verify the retained evidence on which Claw's own durable state must rely.
"""

import asyncio
from collections.abc import AsyncIterator

import pytest
from a13n_harness import (
    AgentSpec,
    DeferredToolResume,
    HarnessBuilder,
    HarnessRunResultEvent,
    HarnessState,
    RunBindings,
)
from a13n_harness.capabilities.steering import steering_input_ids
from pydantic_ai import Tool
from pydantic_ai.capabilities import Capability
from pydantic_ai.messages import ModelMessage, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, DeltaToolCall, DeltaToolCalls, FunctionModel


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_steered_input_identity_survives_serialized_continuation():
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def model_stream(messages: list[ModelMessage], info: AgentInfo) -> AsyncIterator[str]:
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
        yield "complete"

    executable = HarnessBuilder(instrumentation=None).build(
        AgentSpec(), output_type=str, model=FunctionModel(stream_function=model_stream)
    )
    async with executable.stream(
        "initial",
        previous_state=HarnessState.new(thread_id="thread_contract"),
        bindings=RunBindings.embedded(),
    ) as stream:

        async def consume():
            return [event async for event in stream]

        consumer = asyncio.create_task(consume())
        await asyncio.wait_for(entered.wait(), timeout=10)
        await stream.steer("additional", input_id="input_contract")
        release.set()
        events = await asyncio.wait_for(consumer, timeout=10)
    terminal = events[-1]
    assert isinstance(terminal, HarnessRunResultEvent)
    assert terminal.result.status == "completed", terminal.result.failure
    state = terminal.result.state
    assert state is not None
    restored = HarnessState.model_validate_json(state.model_dump_json())
    assert restored.thread_id == "thread_contract"
    assert steering_input_ids(restored.message_history) == ("input_contract",)
    async with executable.stream("continue", previous_state=restored) as continuation:
        result_events = [event async for event in continuation]
    completed = result_events[-1]
    assert isinstance(completed, HarnessRunResultEvent)
    assert completed.result.status == "completed"
    assert completed.result.thread_id == "thread_contract"


@pytest.mark.anyio
async def test_approval_wait_can_restore_without_executing_before_acceptance():
    effects: list[int] = []

    def effect(value: int) -> int:
        effects.append(value)
        return value

    async def model(
        messages: list[ModelMessage], info: AgentInfo
    ) -> AsyncIterator[str | DeltaToolCalls]:
        returned = any(
            isinstance(part, ToolReturnPart) for message in messages for part in message.parts
        )
        if returned:
            yield "complete"
        else:
            yield {
                0: DeltaToolCall(
                    name="effect", json_args='{"value": 7}', tool_call_id="call_contract"
                )
            }

    executable = HarnessBuilder(instrumentation=None).build(
        AgentSpec(),
        output_type=str,
        model=FunctionModel(stream_function=model),
        capabilities=(Capability(tools=[Tool(effect, requires_approval=True)], id="contract"),),
    )
    result = await executable.run(
        "change", previous_state=HarnessState.new(thread_id="thread_wait")
    )
    assert result.status == "suspended", result.failure
    assert result.state is not None and result.deferred is not None
    assert effects == []
    restored = HarnessState.model_validate_json(result.state.model_dump_json())
    resumed = await executable.run(
        previous_state=restored,
        deferred_resume=DeferredToolResume(
            result.deferred, result.deferred.build_results(approve_all=True)
        ),
    )
    assert resumed.status == "completed", resumed.failure
    assert resumed.thread_id == "thread_wait"
    assert effects == [7]
