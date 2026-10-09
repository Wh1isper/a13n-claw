"""Agent tools over independently durable child Threads, not live task receipts."""

import asyncio
from typing import Literal

from a13n_harness.context import AgentContext
from a13n_harness.tools.identity import TOOL_IDENTITY_KEY, ToolIdentity
from pydantic import JsonValue
from pydantic_ai import RunContext, Tool
from pydantic_ai.capabilities import Capability

from a13n_claw.domain import TERMINAL, ClawError, RunRecord
from a13n_claw.storage import Store


def delegation_capability(store: Store, run: RunRecord) -> Capability[AgentContext]:
    assert run.owner is not None
    owner = run.owner

    async def delegate_work(
        ctx: RunContext[AgentContext],
        profile_id: str,
        prompt: str,
        cancel_policy: Literal["keep", "cancel"] = "keep",
        notify: bool = False,
    ) -> dict[str, JsonValue]:
        """Admit background work on a distinct child Thread using a captured child Profile.

        This returns acceptance, not completion. Use inspect_delegation to read the
        saved result. notify delivers a saved result once as a parent conversation
        input; it may start a successor Run if the parent completed. A stopped parent
        is never automatically restarted. cancel_policy controls propagation of an
        explicit parent cancellation, not the child's current outcome.
        """
        if ctx.tool_call_id is None:
            raise ClawError("delegation_identity", "Delegation requires a tool call identity")
        child = await asyncio.to_thread(
            store.delegate_work,
            run.id,
            owner,
            ctx.tool_call_id,
            profile_id,
            prompt,
            cancel_policy=cancel_policy,
            notify=notify,
        )
        return child.model_dump(mode="json")

    async def inspect_delegation(
        delegation_id: str,
        wait_seconds: float = 0,
    ) -> dict[str, JsonValue]:
        """Read saved child progress or wait up to 30 seconds for a stopped outcome.

        Waiting, blocked and queued are not successful completion. A timeout returns
        the current saved state and does not cancel the child. History stays separate.
        """
        if not 0 <= wait_seconds <= 30:
            raise ClawError("wait_range", "Wait must be between zero and 30 seconds", 422)
        deadline = asyncio.get_running_loop().time() + wait_seconds
        while True:
            child = await asyncio.to_thread(store.child_progress, run.id, owner, delegation_id)
            remaining = deadline - asyncio.get_running_loop().time()
            if child.status in TERMINAL | {"waiting"} or remaining <= 0:
                return child.model_dump(mode="json")
            await asyncio.sleep(min(0.1, remaining))

    profiles = ", ".join(child.profile.id for child in run.composition.children)
    return Capability(
        id="claw.delegation",
        instructions=(
            f"Available captured child Profiles: {profiles}. "
            "Delegated work has independent durable outcomes."
        ),
        tools=[
            Tool(delegate_work, metadata={TOOL_IDENTITY_KEY: ToolIdentity("claw.work.delegate")}),
            Tool(
                inspect_delegation, metadata={TOOL_IDENTITY_KEY: ToolIdentity("claw.work.inspect")}
            ),
        ],
    )
