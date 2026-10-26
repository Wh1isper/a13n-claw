"""Exact public Host observations shared by messaging and scheduled work."""

from a13n_harness_ui.app import HarnessUiApp
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.surfaces import ThreadDetail

TERMINAL = {"completed", "failed", "cancelled", "suspended"}
# These public errors conclusively reject admission. Shutdown cancellation can occur
# after admission, so app_stopping must retain uncertainty like a lost response.
REJECTED = {"thread_run_active", "thread_touch_failed"}
DECISION_REJECTED = {
    "thread_deferred_not_pending",
    "thread_continuation_conflict",
    "thread_interaction_expired",
    "thread_deferred_response_incomplete",
    "thread_deferred_response_kind_mismatch",
    "thread_deferred_response_invalid",
}


async def exact_operation_status(host: HarnessUiApp, thread_id: str, receipt_id: str) -> str | None:
    """Use exact public live or saved evidence, never the latest unrelated outcome."""
    try:
        operation = await host.get_root_operation(receipt_id)
    except HarnessUiError:
        detail = await host.get_thread(thread_id)
        saved = detail.thread.last_execution
        if saved is not None and saved.execution_id == receipt_id:
            return saved.status
        return None
    if operation.receipt.thread_id != thread_id:
        raise ValueError("Operation does not belong to the selected Thread")
    return operation.status


async def retained_output(host: HarnessUiApp, thread_id: str, detail: ThreadDetail) -> str | None:
    saved, completion = detail.thread.last_execution, detail.thread.completion
    if saved is None or completion is None or completion.run_id != saved.run_id:
        return None
    try:
        page = await host.get_thread_transcript(
            thread_id=thread_id, expected_continuation_id=completion.continuation_id, limit=100
        )
    except HarnessUiError:
        return None
    if not page.turns or page.later_turns_cursor is not None:
        return None
    final_position = page.turns[-1].final_position
    entry = next((e for e in page.entries if e.position == final_position), None)
    if entry is None:
        return None
    parts = [p for p in entry.parts if p.kind == "assistant" and p.text]
    if not parts:
        return None
    text = "\n".join(p.text or "" for p in parts)
    if any(p.text_truncated for p in parts):
        text += f"\n[Retained excerpt; inspect Thread {thread_id} for full output.]"
    return text
