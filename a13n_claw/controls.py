"""Explicit IM commands over retained source associations and public Host controls."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.surfaces import DecisionResponseBatch

if TYPE_CHECKING:
    from a13n_claw.service import ClawService

HELP = """Claw commands (Channel operators only):
/claw tasks
/claw status <inbox-id>
/claw continue <inbox-id> <text>       (idle Thread)
/claw steer <inbox-id> <receipt-id> <text>
/claw cancel <inbox-id> <receipt-id>
/claw decisions <inbox-id>
/claw answer <inbox-id> <complete DecisionResponseBatch JSON>
Use the operator CLI/API for workers, history, configuration, and reconciliation.
Commands select existing work; ordinary replies keep the configured routing mode.
"""


def is_control(text: str) -> bool:
    return text == "/claw" or text.startswith("/claw ")


async def execute(service: ClawService, item: dict[str, Any]) -> None:
    """Called under the product lock. Record mutation intent before invoking the Host."""
    from a13n_claw.service import database

    message, body = service.source(item)
    binding = service.authorize_message(message, item["channel_id"])
    if message.sender_id not in binding.operators:
        await database(
            service.store.complete_control, item["id"], "Channel operator grant required"
        )
        return
    command, _, arguments = body["control"].partition(" ")
    if command in {"", "help"}:
        await database(service.store.complete_control, item["id"], HELP)
        return
    if command == "tasks":
        rows = await database(
            service.store.rows,
            "SELECT id,thread_id,disposition,reason,blocked FROM inbox "
            "WHERE channel_id=? AND body IS NOT NULL "
            "AND json_extract(body,'$.control') IS NULL ORDER BY created DESC LIMIT 20",
            (item["channel_id"],),
        )
        await database(service.store.complete_control, item["id"], json.dumps(rows))
        return

    target_id, _, argument = arguments.strip().partition(" ")
    try:
        target = await database(service.store.item, target_id)
        if target["channel_id"] != item["channel_id"]:
            raise ValueError("Target is outside this Channel")
        service.source(target)
        if target["mode"] != service.settings.mode or not target["thread_id"]:
            raise ValueError(
                "Target is not routed in the current mode; inspect its business status"
            )
        thread_id = target["thread_id"]
        detail = await service.host.get_thread(thread_id)
        if detail.thread.archived or detail.thread.configuration.project_id != target["project_id"]:
            raise ValueError("Target Thread is archived or its Project changed")
        if service.settings.mode == "one_thread":
            instance = await database(service.store.instance)
            if thread_id != instance["main_thread_id"] or detail.thread.role != "coordinator":
                raise ValueError("Target is not canonical Main")
        active = await service.host.active_root_operation(thread_id)
        if command == "status":
            value = {
                "inbox_id": target_id,
                "disposition": target["disposition"],
                "reason": target["reason"],
                "blocked": target["blocked"],
                "thread_id": thread_id,
                "operation": active.model_dump(mode="json", exclude={"outcome", "failure"})
                if active
                else None,
                "saved_execution": detail.thread.last_execution.model_dump(
                    mode="json", exclude={"error_message"}
                )
                if detail.thread.last_execution
                else None,
                "awaiting_decisions": bool(detail.deferred_requests),
            }
            await database(service.store.complete_control, item["id"], json.dumps(value))
            return
        if command == "decisions":
            decisions = await service.host.thread_decisions(thread_id=thread_id)
            await database(
                service.store.complete_control,
                item["id"],
                decisions.model_dump_json() if decisions is not None else "No pending decisions",
            )
            return
        receipt_id, _, text = argument.partition(" ")
        response = None
        if command in {"steer", "cancel"}:
            if active is None or active.receipt.receipt_id != receipt_id:
                raise ValueError("Exact operation is no longer active; refetch status")
            if command == "steer" and not text.strip():
                raise ValueError("Steering requires text")
        elif command == "continue":
            if active is not None or detail.deferred_requests:
                raise ValueError("Thread is active or awaiting decisions; use the exact control")
            if not argument.strip():
                raise ValueError("Continuation requires text")
        elif command == "answer":
            response = DecisionResponseBatch.model_validate_json(argument)
            # Validate exact continuation and selected pending set at the Host boundary.
            await service.host.thread_decisions(
                thread_id=thread_id, expected_continuation_id=response.expected_continuation_id
            )
        else:
            raise ValueError(HELP)
    except (ValueError, HarnessUiError) as exc:
        error = exc.code if isinstance(exc, HarnessUiError) else str(exc)
        await database(service.store.complete_control, item["id"], error)
        return

    followup = None
    try:
        if command in {"continue", "answer"}:
            attempt, followup = await database(
                service.store.begin_continuation, thread_id, item["id"], target_id
            )
        else:
            attempt = await database(
                service.store.begin_attempt, thread_id, "control", [item["id"]]
            )
    except ValueError as exc:
        await database(service.store.complete_control, item["id"], str(exc))
        return
    try:
        if command == "continue":
            result = await service.host.submit_thread(
                thread_id=thread_id,
                prompt=f"Human IM continuation from {message.platform}/{message.connection}, "
                f"sender {message.sender_id}, for Inbox {target_id}:\n{argument}",
            )
        elif command == "steer":
            result = await service.host.steer_root_operation(
                receipt_id=receipt_id, message=f"Human IM steering for Inbox {target_id}:\n{text}"
            )
        elif command == "cancel":
            result = await service.host.cancel_root_operation(receipt_id)
        else:
            assert response is not None
            result = await service.host.respond_decisions(thread_id=thread_id, response=response)
    except BaseException:
        if followup is not None:
            await database(
                service.store.finish_attempt,
                followup,
                "unknown",
                evidence="Continuation response unavailable; inspect exact Host state",
            )
        await database(
            service.store.finish_attempt,
            attempt,
            "unknown",
            evidence="Control returned no conclusive response; inspect the exact Host state",
        )
        raise
    if followup is not None:
        await database(
            service.store.finish_attempt, followup, "accepted", receipt_id=result.receipt_id
        )
    await database(service.store.complete_control, item["id"], result.model_dump_json(), attempt)
