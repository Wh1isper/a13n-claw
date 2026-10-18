"""Trusted per-App service binding for native model-visible Claw tools."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Literal
from uuid import uuid4

from a13n_harness.context import AgentContext
from a13n_harness.environment.models import EnvironmentError
from a13n_harness_ui.app import HarnessUiIntegrations
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import FunctionToolset

from a13n_claw.service import ClawService


def messaging_integrations(service: ClawService | None = None) -> HarnessUiIntegrations:
    """Configuration serializes only the capability name, never this trusted service."""

    def bound() -> ClawService:
        if service is None:
            raise ValueError("Claw messaging service is not open during configuration setup")
        return service

    @dataclass
    class ClawMessaging(AbstractCapability[AgentContext]):
        def get_toolset(self) -> FunctionToolset[AgentContext]:
            return FunctionToolset(
                tools=[
                    claw_inbox_list,
                    claw_inbox_read,
                    claw_inbox_file,
                    claw_inbox_settle,
                    claw_send_message,
                    claw_send_file,
                ],
                id="claw-messaging",
            )

    async def claw_inbox_list(ctx: RunContext[AgentContext]) -> dict[str, Any]:
        """List up to 50 actionable Inbox references in your authorized routed context."""
        try:
            return {"items": await bound().inbox_list(ctx.deps.thread_id, ctx.deps.run_id)}
        except ValueError as exc:
            return {"error": str(exc)}

    async def claw_inbox_read(ctx: RunContext[AgentContext], item_id: str) -> dict[str, Any]:
        """Read an admitted Inbox item's message, version, and attachment availability."""
        try:
            return await bound().inbox_read(ctx.deps.thread_id, ctx.deps.run_id, item_id)
        except ValueError as exc:
            return {"error": str(exc)}

    async def claw_inbox_file(
        ctx: RunContext[AgentContext], item_id: str, index: int
    ) -> dict[str, Any]:
        """Materialize one authorized retained attachment in your Thread tmp directory.

        Use native file/media tools on the returned path. The retained original is unchanged.
        """
        try:
            name, data = await bound().inbox_file(
                ctx.deps.thread_id, ctx.deps.run_id, item_id, index
            )
            name = PurePosixPath(name.replace("\\", "/")).name
            name = name if name not in {"", ".", ".."} else "attachment"
            mount = next(
                (m for m in ctx.deps.environment.snapshot.mounts if m.name == "thread-files"), None
            )
            if mount is None or mount.mount_path is None:
                raise ValueError("Thread files are not available in this Environment")
            # Environment mount paths use forward slashes on every Host platform.
            directory = str(PurePosixPath(mount.mount_path) / "tmp" / f"claw-{uuid4().hex}")
            path = str(PurePosixPath(directory) / name)
            selected = ctx.deps.environment.select_files(path, alias="thread-files")

            async def chunks():
                yield data

            async with ctx.deps.environment.open_files(selected) as files:
                await files.mkdir(directory, parents=True, exist_ok=True)
                await files.write_bytes_stream(path, chunks(), mode="create")
            return {
                "path": path,
                "name": name,
                "size": len(data),
            }
        except (ValueError, OSError, EnvironmentError) as exc:
            return {"error": str(exc)}

    async def claw_inbox_settle(
        ctx: RunContext[AgentContext],
        item_id: str,
        expected_version: int,
        disposition: Literal["handled", "ignored", "deferred", "pending"],
        reason: str,
        dependency: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record a disposition after processing; reading and final answers do not settle work.

        Deferred requires dependency {kind: time, at: Unix seconds},
        {kind: operation, thread_id, receipt_id}, or {kind: human, reason}.
        Unknown effects require operator reconciliation, not a new attempt.
        """
        try:
            return await bound().settle(
                ctx.deps.thread_id,
                ctx.deps.run_id,
                item_id,
                expected_version,
                disposition,
                reason,
                dependency,
            )
        except ValueError as exc:
            return {"error": str(exc)}

    async def claw_send_message(
        ctx: RunContext[AgentContext],
        item_id: str,
        request_key: str,
        text: str,
    ) -> dict[str, Any]:
        """Reply explicitly to an Inbox item's original destination.

        Choose one stable request_key for the intended reply. Repeating identical arguments
        returns its delivery, never sends again. Unknown outcomes need operator review.
        Only canonical Main can send in One Thread mode; final output is not broadcast.
        """
        try:
            return await bound().request_send(
                ctx.deps.thread_id, ctx.deps.run_id, item_id, request_key, text
            )
        except ValueError as exc:
            return {"error": str(exc)}

    async def claw_send_file(
        ctx: RunContext[AgentContext], item_id: str, request_key: str, file_path: str
    ) -> dict[str, Any]:
        """Send one file from your current Environment to this item's original destination.

        Maximum 10 MiB. Use a stable request_key; unknown sends require operator review.
        """
        try:
            # Check messaging scope before reading the selected Environment file.
            await bound().inbox_read(ctx.deps.thread_id, ctx.deps.run_id, item_id)
            selected = ctx.deps.environment.select_files(file_path)
            async with ctx.deps.environment.open_files(selected) as files:
                data = await files.read_bytes(file_path, length=10 * 1024 * 1024 + 1)
            if len(data) > 10 * 1024 * 1024:
                raise ValueError("Files are limited to 10 MiB")
            return await bound().request_send(
                ctx.deps.thread_id,
                ctx.deps.run_id,
                item_id,
                request_key,
                "",
                file={
                    "name": PurePosixPath(file_path.replace("\\", "/")).name,
                    "data": base64.b64encode(data).decode(),
                },
            )
        except (ValueError, OSError, EnvironmentError) as exc:
            return {"error": str(exc)}

    return HarnessUiIntegrations(capabilities={"ClawMessaging": ClawMessaging})
