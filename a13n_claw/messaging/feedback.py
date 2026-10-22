"""Bounded, durable IM presentation of business and native Host observations."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from a13n_harness_ui.errors import HarnessUiError

from a13n_claw.async_utils import database
from a13n_claw.messaging.transport import Destination, OutboundMessage
from a13n_claw.runtime.observations import retained_output

if TYPE_CHECKING:
    from a13n_claw.runtime.service import ClawService

_LABELS = {
    "retained": "Retained; waiting for Host admission.",
    "admitted": "Admitted by Host.",
    "running": "Host operation is running.",
    "suspended": "Awaiting a native decision or continuation. Ask a Channel operator to inspect.",
    "blocked": "Processing is blocked. An operator can inspect the retained Inbox item.",
    "unknown": "Operation outcome is uncertain; operator reconciliation is required.",
    "failed": "Host operation failed. An operator can inspect the exact operation.",
    "cancelled": "Host operation was cancelled.",
    "completed": "Host operation completed; business settlement and delivery are separate.",
}
_REACTIONS = {"retained": "OnIt", "failed": "ERROR", "completed": "DONE"}


class Feedback:
    def __init__(self, service: ClawService):
        self.service = service

    def source(self, item):
        message, body = self.service.ingress.source(item)
        if message.platform != "lark" or "control" in body:
            raise ValueError("No automatic feedback for this source")
        return message, self.service.settings.channels[item["channel_id"]]

    async def enqueue(
        self, item, key: str, body: OutboundMessage, *, kind="status", continuation=None
    ):
        message, _ = self.source(item)
        request_key = f"claw:feedback:{item['id']}:{key}"
        if await database(
            self.service.store.rows, "SELECT id FROM deliveries WHERE request_key=?", (request_key,)
        ):
            return
        await database(
            self.service.store.request_delivery,
            request_key=request_key,
            channel_id=item["channel_id"],
            destination=Destination(
                chat_id=message.chat_id, reply_to=message.message_id
            ).model_dump(),
            thread_id=item["thread_id"] if kind == "decision" else None,
            item_id=item["id"],
            body=body.model_dump(),
            feedback_kind=kind,
            continuation_id=continuation,
        )

    async def status(self, item: dict[str, Any], phase: str) -> None:
        try:
            _, binding = self.source(item)
        except ValueError:
            return
        if binding.progress_updates:
            await self.enqueue(
                item,
                phase,
                OutboundMessage(
                    text=f"Claw {item['id']}: {_LABELS[phase]}\n/claw status {item['id']}"
                ),
            )
        if binding.reactions and phase in _REACTIONS:
            await self.enqueue(
                item,
                f"reaction:{phase}",
                OutboundMessage(reaction=_REACTIONS[phase]),
                kind="reaction",
            )

    async def decisions(self, item: dict[str, Any]) -> None:
        try:
            message, binding = self.source(item)
        except ValueError:
            return
        # Shared Main has no automatically safe disclosure destination. Routed prompts
        # require an operator origin and a deliberately enabled destination policy.
        if (
            not binding.decision_prompts
            or item["mode"] == "one_thread"
            or message.sender_id not in binding.operators
            or not item["thread_id"]
        ):
            return
        decisions = await self.service.host.thread_decisions(thread_id=item["thread_id"])
        if decisions is None:
            return
        responses = []
        for request in decisions.requests:
            if request.kind == "approval":
                responses.append(
                    {"kind": "approval", "request_id": request.request_id, "approved": False}
                )
            elif request.kind == "question":
                responses.append(
                    {
                        "kind": "question",
                        "request_id": request.request_id,
                        "response": "REPLACE WITH YOUR ANSWER",
                    }
                )
            else:
                # External tool results need the native operator surface, not fabricated values.
                return
        text = (
            f"Native decisions for {item['id']}:\n{decisions.model_dump_json()}\n\n"
            "Review every request, edit the complete response below, then send it. "
            "Approvals default to denial; change approved to true only to authorize that request.\n"
            f"/claw answer {item['id']} "
            + json.dumps(
                {
                    "expected_continuation_id": decisions.continuation_id,
                    "responses": responses,
                }
            )
        )
        if len(text) > 20_000:
            text = (
                f"Native decisions for {item['id']} exceed the IM limit. "
                "Use the authenticated CLI/API to inspect and answer the complete batch."
            )
        await self.enqueue(
            item,
            f"decision:{decisions.continuation_id}",
            OutboundMessage(text=text),
            kind="decision",
            continuation=decisions.continuation_id,
        )

    async def authorize_delivery(self, row, item) -> None:
        feedback = await database(
            self.service.store.rows,
            "SELECT * FROM feedback_deliveries WHERE delivery_id=?",
            (row["id"],),
        )
        if not feedback:
            if row["request_key"].startswith("claw:final:"):
                _, binding = self.source(item)
                if not binding.final_reply or self.service.settings.mode == "one_thread":
                    raise ValueError("Automatic final reply policy revoked")
            return
        message, binding = self.source(item)
        value = feedback[0]
        if value["kind"] == "status" and not binding.progress_updates:
            raise ValueError("Progress policy revoked")
        if value["kind"] == "reaction" and not binding.reactions:
            raise ValueError("Reaction policy revoked")
        if value["kind"] == "decision":
            if (
                not binding.decision_prompts
                or self.service.settings.mode == "one_thread"
                or message.sender_id not in binding.operators
            ):
                raise ValueError("Decision disclosure policy revoked")
            decisions = await self.service.host.thread_decisions(thread_id=row["thread_id"])
            if decisions is None or decisions.continuation_id != value["continuation_id"]:
                raise ValueError("Decision is no longer pending")

    async def final(self, attempt, items) -> None:
        if self.service.settings.mode == "one_thread" or not items:
            return
        # Multiple steers of the same operation select the latest original input once.
        item = items[-1]
        try:
            message, binding = self.source(item)
        except ValueError:
            return
        if not binding.final_reply:
            return
        related = await database(
            self.service.store.rows,
            "SELECT i.* FROM inbox i JOIN attempt_items ai ON ai.item_id=i.id "
            "JOIN attempts a ON a.id=ai.attempt_id WHERE a.receipt_id=? "
            "AND a.kind IN ('submit','steer','continue') ORDER BY i.created,i.id",
            (attempt["receipt_id"],),
        )
        if not related or related[-1]["id"] != item["id"]:
            return
        key = f"claw:final:{attempt['receipt_id']}"
        existing = await database(
            self.service.store.rows,
            "SELECT d.id FROM deliveries d WHERE d.request_key=? OR "
            "(d.item_id IN (SELECT ai.item_id FROM attempt_items ai JOIN attempts a "
            "ON a.id=ai.attempt_id WHERE a.receipt_id=?) AND d.created>=? AND NOT EXISTS "
            "(SELECT 1 FROM feedback_deliveries f WHERE f.delivery_id=d.id))",
            (key, attempt["receipt_id"], attempt["created"]),
        )
        if existing:
            await database(self.service.store.settle_routed_reply, attempt["receipt_id"])
            return
        try:
            operation = await self.service.host.get_root_operation(attempt["receipt_id"])
        except HarnessUiError:
            detail = await self.service.host.get_thread(attempt["thread_id"])
            saved = detail.thread.last_execution
            if (
                saved is None
                or saved.execution_id != attempt["receipt_id"]
                or saved.status != "completed"
            ):
                return
            output = await retained_output(self.service.host, attempt["thread_id"], detail)
        else:
            if (
                operation.receipt.thread_id != attempt["thread_id"]
                or operation.status != "completed"
            ):
                return
            output = operation.outcome.execution.output if operation.outcome else None
        if not isinstance(output, str) or not output.strip():
            return
        if len(output) > 20_000:
            output = (
                output[:19_800] + "\n[Output truncated; use the operator CLI/API for full output.]"
            )
        await database(
            self.service.store.request_delivery,
            request_key=key,
            channel_id=item["channel_id"],
            destination=Destination(
                chat_id=message.chat_id, reply_to=message.message_id
            ).model_dump(),
            thread_id=attempt["thread_id"],
            item_id=item["id"],
            body=OutboundMessage(text=output).model_dump(),
        )
        await database(self.service.store.settle_routed_reply, attempt["receipt_id"])

    async def observe(self, attempt, phase: str) -> None:
        if attempt["kind"] == "control":
            return
        items = await database(
            self.service.store.rows,
            "SELECT i.* FROM inbox i JOIN attempt_items ai ON ai.item_id=i.id "
            "WHERE ai.attempt_id=? ORDER BY i.created,i.id",
            (attempt["id"],),
        )
        for item in items:
            await self.status(item, phase)
            if phase == "suspended":
                await self.decisions(item)
        if phase == "completed":
            await self.final(attempt, items)
