"""Serialization and exact correlation of native Harness decision batches."""

from a13n_harness import DeferredToolResume, HarnessState
from a13n_harness.tools.deferred import preflight_deferred_resume, validate_deferred_requests
from pydantic import TypeAdapter
from pydantic_ai.messages import ModelRequest, ModelResponse, RetryPromptPart, ToolReturnPart
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults

from a13n_claw.domain import ClawError

REQUESTS = TypeAdapter(DeferredToolRequests)
RESULTS = TypeAdapter(DeferredToolResults)


def validate_wait(state: HarnessState, requests_json: str) -> DeferredToolRequests:
    requests = REQUESTS.validate_json(requests_json)
    call_ids, approval_ids = validate_deferred_requests(requests)
    if not call_ids and not approval_ids:
        raise ClawError("empty_decision", "A waiting boundary requires at least one decision")
    response: ModelResponse | None = None
    answered: set[str] = set()
    for message in state.message_history:
        if isinstance(message, ModelResponse):
            response = message
            answered.clear()
        elif isinstance(message, ModelRequest):
            answered.update(
                part.tool_call_id
                for part in message.parts
                if isinstance(part, ToolReturnPart | RetryPromptPart)
            )
    remaining = (
        {
            call.tool_call_id: call
            for call in response.tool_calls
            if call.tool_call_id not in answered
        }
        if response is not None
        else {}
    )
    if set(remaining) != call_ids | approval_ids:
        raise ClawError(
            "decision_mismatch", "Decision batch does not cover the continuation's pending calls"
        )
    for call in (*requests.calls, *requests.approvals):
        saved = remaining[call.tool_call_id]
        if saved.tool_name != call.tool_name or saved.args_as_dict() != call.args_as_dict():
            raise ClawError(
                "decision_mismatch", "Decision arguments differ from the saved continuation"
            )
    return requests


def validate_answer(
    state: HarnessState, requests_json: str, response_json: str
) -> DeferredToolResume:
    requests = validate_wait(state, requests_json)
    results = RESULTS.validate_json(response_json)
    resume = preflight_deferred_resume(DeferredToolResume(requests, results), previous_state=state)
    if resume is None:
        raise ClawError("decision_stale", "This decision is no longer pending")
    return resume
