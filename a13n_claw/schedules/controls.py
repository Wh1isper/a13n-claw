"""Human IM controls for occurrences authored in the same qualified source."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.surfaces import DecisionResponseBatch

from a13n_claw.async_utils import database
from a13n_claw.encoding import encode
from a13n_claw.runtime.observations import DECISION_REJECTED, REJECTED

if TYPE_CHECKING:
    from a13n_claw.runtime.service import ClawService

COMMANDS = {
    "occurrences",
    "occurrence",
    "occurrence-decisions",
    "occurrence-answer",
    "occurrence-cancel",
}


async def execute(service: ClawService, item: dict[str, Any], command: str, arguments: str) -> None:
    """Caller holds the service lock; a text identifier never grants access."""
    try:
        if command == "occurrences":
            value = await service.scheduler.history(item, arguments.strip() or None)
            await database(service.store.complete_control, item["id"], encode(value))
            return
        occurrence_id, _, argument = arguments.strip().partition(" ")
        row = await service.scheduler.owned_occurrence(item, occurrence_id)
        await service.scheduler.observe()
        row = await database(service.scheduler.store.get, occurrence_id)
        if command == "occurrence":
            await database(
                service.store.complete_control,
                item["id"],
                encode(service.scheduler.occurrence_view(row)),
            )
            return
        if not row["thread_id"]:
            raise ValueError(
                "Occurrence has no admitted Thread; use operator CLI for pending cancellation"
            )
        if command == "occurrence-decisions":
            pending = await service.host.thread_decisions(thread_id=row["thread_id"])
            await database(
                service.store.complete_control,
                item["id"],
                pending.model_dump_json() if pending else "No pending decisions",
            )
            return
        response = None
        if command == "occurrence-answer":
            response = DecisionResponseBatch.model_validate_json(argument)
            if row["status"] != "waiting":
                raise ValueError("Occurrence is not waiting")
            await service.host.thread_decisions(
                thread_id=row["thread_id"],
                expected_continuation_id=response.expected_continuation_id,
            )
        elif command == "occurrence-cancel":
            if not argument or argument != row["receipt_id"]:
                raise ValueError("Cancellation requires the exact current occurrence receipt")
            if row["status"] not in {"accepted", "waiting"}:
                raise ValueError("Occurrence is not active or waiting")
        else:
            raise ValueError("Unknown occurrence control")
    except (ValueError, HarnessUiError) as exc:
        await database(
            service.store.complete_control,
            item["id"],
            exc.code if isinstance(exc, HarnessUiError) else str(exc),
        )
        return

    attempt = await database(service.store.begin_attempt, row["thread_id"], "control", [item["id"]])
    try:
        if response is not None:
            receipt = await service.scheduler.respond(row["thread_id"], response)
            value = receipt.model_dump(mode="json")
        else:
            value = await service.scheduler.cancel(occurrence_id)
    except BaseException as exc:
        if isinstance(exc, HarnessUiError) and exc.code in REJECTED | DECISION_REJECTED:
            await database(service.store.complete_control, item["id"], exc.code, attempt)
            return
        await database(
            service.store.finish_attempt,
            attempt,
            "unknown",
            evidence="Occurrence control outcome unavailable; inspect Host and occurrence state",
        )
        raise
    await database(service.store.complete_control, item["id"], encode(value), attempt)
