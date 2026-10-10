"""Native Harness capabilities bound to the actual executing durable Thread."""

import asyncio
from typing import Any

from a13n_harness.context import AgentContext
from a13n_harness.tools.identity import TOOL_IDENTITY_KEY, ToolIdentity
from pydantic_ai import RunContext, Tool
from pydantic_ai.capabilities import Capability

from a13n_claw.attention import DeliveryRequest, ProcessingChange, WorkerCreate
from a13n_claw.domain import ClawError, InputRequest, RunRecord
from a13n_claw.messaging import Messaging
from a13n_claw.storage import Store


def identity(ctx: RunContext[AgentContext]) -> str:
    if ctx.tool_call_id is None:
        raise ClawError("request_identity", "This operation requires a stable tool call identity")
    return ctx.tool_call_id


def collaboration_capability(
    store: Store, run: RunRecord, *, main: bool
) -> Capability[AgentContext]:
    assert run.owner is not None
    owner = run.owner
    coordination = store.coordination

    async def create_worker(
        ctx: RunContext[AgentContext], title: str, profile_id: str, text: str
    ) -> dict[str, Any]:
        """Create one persistent owned worker and admit its initial work atomically.

        Acceptance is not completion. Workers survive Main Runs; inspect saved
        outcomes before accepting a result. Do not repeat a task already admitted by a user.
        """
        return await asyncio.to_thread(
            coordination.create_worker,
            run.id,
            owner,
            WorkerCreate(request_id=identity(ctx), title=title, profile_id=profile_id, text=text),
        )

    async def inspect_threads(thread_id: str | None = None) -> dict[str, Any]:
        """List your permitted Threads, or inspect one Thread and its latest saved Runs.

        Main sees its owned workers; workers see only themselves and their owner.
        Inactive, waiting, failed and cancelled do not mean successful completion.
        """
        return await asyncio.to_thread(coordination.inspect_collaboration, run.id, owner, thread_id)

    async def send_thread_message(
        ctx: RunContext[AgentContext], thread_id: str, text: str
    ) -> dict[str, Any]:
        """Send durable work or a reply within your bound ownership scope.

        A worker report is retained as Main attention, then admitted exactly once as
        ordinary input when automatic-processing barriers permit. Its input_id links
        the saved receipt; inspect Threads to see incorporation. Acceptance is not processing.
        """
        return await asyncio.to_thread(
            coordination.send_collaboration,
            run.id,
            owner,
            thread_id,
            InputRequest(request_id=identity(ctx), text=text),
        )

    async def list_attention(
        disposition: str | None = "pending", after: int = 0, item_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Inspect durable worker/human attention without acknowledging it.

        Use sequence pagination. Human input was already admitted; reconcile its
        saved references rather than assigning it again. Reading is not settlement.
        """
        return await asyncio.to_thread(
            coordination.bound_inbox,
            run.id,
            owner,
            attention=True,
            disposition=disposition,
            after=after,
            item_id=item_id,
        )

    async def update_attention(item_id: str, change: ProcessingChange) -> dict[str, Any]:
        """Explicitly settle, ignore, reopen or defer attention using the observed version.

        Deferred work needs exactly one worker Run, UTC time or human-resolution key.
        A note alone is not a dependency. Terminal dependencies reactivate immediately.
        """
        return await asyncio.to_thread(coordination.bound_change, run.id, owner, item_id, change)

    tools = [
        Tool(
            inspect_threads,
            metadata={TOOL_IDENTITY_KEY: ToolIdentity("claw.collaboration.inspect")},
        ),
        Tool(
            send_thread_message,
            metadata={TOOL_IDENTITY_KEY: ToolIdentity("claw.collaboration.send")},
        ),
    ]
    if main:
        tools.extend(
            [
                Tool(
                    create_worker,
                    metadata={TOOL_IDENTITY_KEY: ToolIdentity("claw.collaboration.create")},
                ),
                Tool(
                    list_attention,
                    metadata={TOOL_IDENTITY_KEY: ToolIdentity("claw.attention.read")},
                ),
                Tool(
                    update_attention,
                    metadata={TOOL_IDENTITY_KEY: ToolIdentity("claw.attention.update")},
                ),
            ]
        )
    return Capability(
        id="claw.collaboration",
        instructions=(
            "You are the canonical Main coordinator. Use persistent workers for bounded work; "
            "review durable attention and saved outcomes. Stop a Run does not stop workers."
            if main
            else "You are a persistent worker. Report results or ask Main for guidance. "
            "You cannot create workers, inspect siblings or publish externally."
        ),
        tools=tools,
    )


def messaging_capability(store: Store, run: RunRecord) -> Capability[AgentContext]:
    assert run.owner is not None
    owner = run.owner
    messaging = Messaging(store)

    async def list_inbox(
        disposition: str | None = "pending", after: int = 0, item_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Selectively read admitted external messages, with sequence pagination.

        Reading, notices and successful Runs do not mark work handled. For retained
        attachment IDs use read_retained_file; unavailable attachments are not contents.
        """
        return await asyncio.to_thread(
            store.coordination.bound_inbox,
            run.id,
            owner,
            disposition=disposition,
            after=after,
            item_id=item_id,
        )

    async def update_inbox(item_id: str, change: ProcessingChange) -> dict[str, Any]:
        """Record explicit handling, ignoring, reopening or a durable deferral condition.

        Handling an item does not prove an associated send succeeded. Include saved
        work/delivery references in the note and inspect their independent outcomes.
        """
        return await asyncio.to_thread(
            store.coordination.bound_change, run.id, owner, item_id, change
        )

    async def list_destinations() -> list[dict[str, Any]]:
        """List currently permitted explicit destinations; no automatic broadcast exists."""
        return await asyncio.to_thread(messaging.destinations, run.id, owner)

    async def send_message(
        ctx: RunContext[AgentContext], channel_id: str, text: str
    ) -> dict[str, Any]:
        """Save one delivery intent to an explicit destination. Acceptance is not delivery.

        Query saved deliveries. Unknown outcomes require reconciliation, not another
        send. Final answers and worker output are never implicitly broadcast.
        """
        return await asyncio.to_thread(
            messaging.send,
            run.id,
            owner,
            DeliveryRequest(request_id=identity(ctx), channel_id=channel_id, text=text),
        )

    async def list_deliveries() -> list[dict[str, Any]]:
        """Read independently saved delivery status; an unknown send must not be repeated."""
        return await asyncio.to_thread(messaging.bound_deliveries, run.id, owner)

    return Capability(
        id="claw.messaging",
        instructions="External inputs live in the Inbox. Explicitly dispose every obligation. "
        "Main context is shared, but each external send needs one permitted destination.",
        tools=[
            Tool(function, metadata={TOOL_IDENTITY_KEY: ToolIdentity(permission)})
            for function, permission in [
                (list_inbox, "claw.inbox.read"),
                (update_inbox, "claw.inbox.update"),
                (list_destinations, "claw.messaging.destinations"),
                (send_message, "claw.messaging.send"),
                (list_deliveries, "claw.messaging.deliveries"),
            ]
        ],
    )
