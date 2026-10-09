"""Durable application dispatch around the published Harness execution boundary."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, suppress
from dataclasses import dataclass, replace
from typing import Any

from a13n_harness import (
    DeferredToolResume,
    ExecutableAgent,
    HarnessRunResultEvent,
    HarnessRunStream,
    HarnessState,
    RunBindings,
)
from a13n_harness.capabilities.steering import steering_input_ids
from a13n_harness.context import AgentContext
from a13n_harness.environment import EnvironmentMount
from a13n_harness.environment.coordinator import create_environment_runtime
from a13n_harness.errors import RunError
from a13n_harness.events import HarnessExtensionEvent
from a13n_harness.model_context import ModelContextCoordinatorCapability
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.capabilities.abstract import ValidatedToolArgs
from pydantic_ai.messages import ModelRequest, ToolCallPart, UserPromptPart
from pydantic_ai.models import ModelRequestContext
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import UsageLimits

from a13n_claw.decisions import REQUESTS
from a13n_claw.domain import (
    ClawError,
    InputReceipt,
    ProfileDefinition,
    RunRecord,
    canonical,
    new_id,
)
from a13n_claw.storage import Store

logger = logging.getLogger("a13n_claw.coordinator")


async def commit[T](work: Awaitable[T]) -> T:
    """Join in-flight publication before propagating native Task cancellation."""
    task = asyncio.ensure_future(work)
    cancelled: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            cancelled = exc
    result = task.result()
    if cancelled is not None:
        raise cancelled
    return result


class CheckpointCapability(AbstractCapability[AgentContext]):
    id = "claw.checkpoint"

    def __init__(
        self,
        save: Callable[[HarnessState], Awaitable[None]],
        authorize: Callable[[], Awaitable[None]],
    ):
        self.save = save
        self.authorize = authorize
        self.root_run_id: str | None = None

    def get_ordering(self) -> CapabilityOrdering:
        return CapabilityOrdering(
            position="innermost", wrapped_by=(ModelContextCoordinatorCapability,)
        )

    async def wrap_run(
        self,
        ctx: RunContext[AgentContext],
        *,
        handler: Callable[[], Awaitable[Any]],
    ) -> Any:
        if self.root_run_id is not None:
            return await handler()
        self.root_run_id = ctx.run_id
        try:
            return await handler()
        finally:
            self.root_run_id = None

    async def before_model_request(
        self,
        ctx: RunContext[AgentContext],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        if ctx.run_id == self.root_run_id:
            await self.authorize()
            state = await ctx.deps.export_state(ctx.messages)
            await commit(self.save(state))
        return request_context

    async def before_tool_execute(
        self,
        ctx: RunContext[AgentContext],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
    ) -> ValidatedToolArgs:
        await self.authorize()
        return args


@dataclass(frozen=True)
class RunRuntime:
    executable: ExecutableAgent[str]
    bindings: RunBindings
    environments: dict[str, EnvironmentMount] | None = None


RuntimeFactory = Callable[
    [RunRecord, CheckpointCapability], AbstractAsyncContextManager[RunRuntime]
]


def prompt_for(inputs: list[InputReceipt]) -> str | None:
    if not inputs:
        return None
    return canonical(
        [
            {
                "input_id": item.id,
                "sender": item.actor_id,
                "source": item.source,
                "text": item.text,
                "attachment_ids": item.attachment_ids,
            }
            for item in inputs
        ]
    )


class Coordinator:
    def __init__(
        self, store: Store, workspace: str, factory: RuntimeFactory, *, concurrency: int = 4
    ):
        self.store = store
        self.workspace = workspace
        self.factory = factory
        self.concurrency = concurrency
        self.active: dict[str, asyncio.Task[None]] = {}
        self.started: dict[str, str] = {}
        self.retiring: set[asyncio.Task[None]] = set()
        self.failure: str | None = None
        self.streams: dict[str, HarnessRunStream[str]] = {}
        self.changed = asyncio.Event()
        self.stopping = False
        self.dispatcher: asyncio.Task[None] | None = None
        self.observations: dict[str, list[dict[str, str]]] = {}

    async def start(self) -> None:
        # The application must already own the data-root lease.
        await asyncio.to_thread(self.store.reconcile_startup)
        self.dispatcher = asyncio.create_task(self._dispatch(), name="claw-dispatch")

    def wake(self) -> None:
        self.changed.set()

    async def close(self) -> None:
        self.stopping = True
        self.changed.set()
        if self.dispatcher is not None:
            await asyncio.gather(self.dispatcher, return_exceptions=True)
        for stream in tuple(self.streams.values()):
            stream.cancel()
        # Preparing tasks can be inside external I/O without a Harness stream.
        for run_id, task in tuple(self.active.items()):
            if run_id not in self.streams:
                task.cancel()
        await asyncio.gather(*tuple(self.active.values()), return_exceptions=True)
        # Done callbacks enqueue retirement even for tasks cancelled before entry.
        await asyncio.sleep(0)
        await asyncio.gather(*tuple(self.retiring), return_exceptions=True)

    async def _dispatch(self) -> None:
        try:
            await self._dispatch_loop()
        except Exception:
            self.failure = "dispatch_failed"
            logger.error("Dispatch stopped; inspect saved work before restarting")

    async def _dispatch_loop(self) -> None:
        while not self.stopping:
            self.changed.clear()
            await asyncio.to_thread(self.store.deliver_child_results)
            for run_id, owner in tuple(self.started.items()):
                task = self.active.get(run_id)
                if task is None or task.done() or task.cancelling() or run_id in self.streams:
                    continue
                try:
                    current = await asyncio.to_thread(self.store.execution_view, run_id, owner)
                except ClawError as exc:
                    if exc.code == "stale_owner":
                        continue
                    raise
                if current.cancel_requested:
                    task.cancel()
            for run_id in await asyncio.to_thread(self.store.queued):
                if len(self.active) >= self.concurrency or self.stopping:
                    break
                if run_id in self.active:
                    continue
                owner = new_id("owner")
                try:
                    run = await asyncio.to_thread(self.store.claim, run_id, owner, self.workspace)
                except ClawError as exc:
                    if exc.code not in {"thread_blocked", "run_not_queued"}:
                        await asyncio.to_thread(self.store.block_dispatch, run_id, exc.code)
                    continue
                self.started[run_id] = owner
                task = asyncio.create_task(self._execute(run, owner), name=run_id)
                self.active[run_id] = task
                task.add_done_callback(lambda done, rid=run_id: self._retired(rid, done))
            with suppress(TimeoutError):
                await asyncio.wait_for(self.changed.wait(), timeout=0.1)

    def _retired(self, run_id: str, task: asyncio.Task[None]) -> None:
        owner = self.started[run_id]
        retired = asyncio.create_task(self._retire(run_id, owner, task))
        self.retiring.add(retired)
        retired.add_done_callback(self.retiring.discard)

    async def _retire(self, run_id: str, owner: str, task: asyncio.Task[None]) -> None:
        try:
            if not task.cancelled() and task.exception() is not None:
                logger.error("Execution could not commit an outcome: %s", run_id)
            await asyncio.to_thread(self.store.retire_owner, run_id, owner)
        except Exception:
            self.failure = "publication_failed"
            logger.error("Execution retirement could not be saved: %s", run_id)
        finally:
            self.active.pop(run_id, None)
            self.started.pop(run_id, None)
            self.streams.pop(run_id, None)
            self.changed.set()

    async def _execute(self, run: RunRecord, owner: str) -> None:
        published = False
        try:
            previous = (
                await asyncio.to_thread(self.store.checkpoint, run.checkpoint_id)
                if run.checkpoint_id
                else HarnessState.new(thread_id=run.thread_id)
            )
            resume = await asyncio.to_thread(self.store.resume_decision, run.id, owner)
            initial: list[InputReceipt] = []
            prompt: str | None = None
            known_inputs: set[str] = set()

            async def evidence(state: HarnessState) -> tuple[str, ...]:
                ids = set(steering_input_ids(state.message_history)) & known_inputs
                if prompt is not None and any(
                    isinstance(part, UserPromptPart) and part.content == prompt
                    for message in state.message_history
                    if isinstance(message, ModelRequest)
                    for part in message.parts
                ):
                    ids.update(item.id for item in initial)
                return tuple(sorted(ids))

            async def authorize() -> None:
                await asyncio.to_thread(self.store.authorize_execution, run.id, owner)

            live_stream: HarnessRunStream[str] | None = None

            def pending(state: HarnessState) -> DeferredToolResume | None:
                value = live_stream.pending_deferred_input if live_stream is not None else resume
                return value.remaining(state.message_history) if value is not None else None

            async def save(state: HarnessState) -> None:
                await authorize()
                await asyncio.to_thread(
                    self.store.publish,
                    run.id,
                    owner,
                    state,
                    incorporated=await evidence(state),
                    pending_deferred=pending(state),
                )

            async with self.factory(run, CheckpointCapability(save, authorize)) as runtime:
                initial = await asyncio.to_thread(self.store.start, run.id, owner)
                prompt = prompt_for(initial)
                known_inputs.update(item.id for item in initial)
                profile = ProfileDefinition.model_validate(run.composition.profile.content)
                async with runtime.executable.stream(
                    prompt,
                    previous_state=previous,
                    bindings=replace(
                        runtime.bindings,
                        environment=create_environment_runtime(
                            mounts=runtime.environments, default_mount="workspace"
                        ),
                    )
                    if runtime.environments
                    else runtime.bindings,
                    deferred_resume=resume,
                    tool_recovery="never",
                    usage_limits=UsageLimits(request_limit=profile.max_requests),
                ) as stream:
                    live_stream = stream
                    self.streams[run.id] = stream
                    consume = asyncio.create_task(self._consume(run.id, stream))
                    try:
                        while not consume.done():
                            current = await asyncio.to_thread(
                                self.store.execution_view, run.id, owner
                            )
                            if current.cancel_requested or self.stopping:
                                stream.cancel()
                            deliveries = await asyncio.to_thread(
                                self.store.take_steering, run.id, owner
                            )
                            for item in deliveries:
                                known_inputs.add(item.id)
                                try:
                                    steering = prompt_for([item])
                                    assert steering is not None
                                    await stream.steer(steering, input_id=item.id)
                                except RunError as exc:
                                    if exc.code != "run_not_active":
                                        raise
                                    await asyncio.to_thread(
                                        self.store.steering_delivered,
                                        run.id,
                                        owner,
                                        item.id,
                                        accepted=False,
                                    )
                                else:
                                    await asyncio.to_thread(
                                        self.store.steering_delivered,
                                        run.id,
                                        owner,
                                        item.id,
                                        accepted=True,
                                    )
                            await asyncio.wait({consume}, timeout=0.02)
                        result = await consume
                    finally:
                        if not consume.done():
                            stream.cancel()
                            await consume
                if result is None:
                    raise ClawError(
                        "missing_outcome", "Execution ended without a saved result candidate"
                    )
                state = result.state
                if state is None:
                    await asyncio.to_thread(
                        self.store.finish_without_checkpoint,
                        run.id,
                        owner,
                        status="failed",
                        error="Execution did not produce a complete continuation",
                    )
                else:
                    await commit(
                        asyncio.to_thread(
                            self.store.publish,
                            run.id,
                            owner,
                            state,
                            incorporated=await evidence(state),
                            pending_deferred=pending(state),
                            status="waiting" if result.status == "suspended" else result.status,
                            output=result.output,
                            error=result.failure.code if result.failure is not None else None,
                            requests=REQUESTS.dump_json(result.deferred).decode()
                            if result.deferred
                            else None,
                        )
                    )
                published = True
        except asyncio.CancelledError:
            if not published:
                await commit(asyncio.to_thread(self.store.retire_owner, run.id, owner))
            raise
        except Exception as exc:
            if published:
                # Cleanup cannot rewrite a committed result or waiting decision.
                logger.error("Runtime cleanup failed after publication: %s", run.id)
                return
            code = exc.code if isinstance(exc, ClawError) else "execution_failed"
            await commit(
                asyncio.to_thread(
                    self.store.finish_without_checkpoint, run.id, owner, status="failed", error=code
                )
            )

    async def _consume(self, run_id: str, stream: HarnessRunStream[str]):
        async for event in stream:
            if isinstance(event, HarnessRunResultEvent):
                return event.result
            # Observations are explicitly disposable; saved state owns reconnect.
            observations = self.observations.setdefault(run_id, [])
            kind = (
                event.event.kind
                if isinstance(event.event, HarnessExtensionEvent)
                else event.event.event_kind
            )
            observations.append({"sequence": str(event.sequence), "kind": kind})
            del observations[:-200]
        return None
