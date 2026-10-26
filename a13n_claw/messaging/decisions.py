"""Text presentation and complete ordered responses for native decision batches."""

from __future__ import annotations

import json

from a13n_harness_ui.surfaces import DecisionBatchView, DecisionResponseBatch


def present(item_id: str, batch: DecisionBatchView) -> str:
    lines = [f"Native decisions for {item_id}:"]
    values = []
    supported = True
    for index, request in enumerate(batch.requests, 1):
        lines.append(f"{index}. {request.kind}: {request.tool_name}")
        if request.kind == "question":
            for question in request.questions:
                lines.append(f"   {question.header}: {question.question}")
                lines.append(
                    "   Select multiple or answer freely:"
                    if question.multi_select
                    else "   Select one or answer freely:"
                )
                lines.extend(
                    f"   - {option.label}: {option.description}" for option in question.options
                )
            values.append("REPLACE WITH YOUR ANSWER")
        else:
            lines.append(json.dumps(request.model_dump(mode="json"), ensure_ascii=False))
            if request.kind == "approval":
                values.append(False)
            else:
                supported = False
    if supported:
        lines.extend(
            [
                "Reply with one JSON array entry per numbered request, in order. "
                "Approvals: true to allow, false to deny. Questions: an answer string "
                "covering all questions in that request. The entire batch is resolved together.",
                f"/claw respond {item_id} {batch.continuation_id} "
                + json.dumps(values, ensure_ascii=False),
                f"For native structured answers use /claw decisions {item_id} and /claw answer.",
            ]
        )
    else:
        lines.append("External tool results require the authenticated CLI/API native response.")
    text = "\n".join(lines)
    if len(text) > 20_000:
        return (
            f"Native decisions for {item_id} exceed the IM limit. "
            "Use the authenticated CLI/API to inspect and answer the complete batch."
        )
    return text


def ordered_response(batch: DecisionBatchView, values_json: str) -> DecisionResponseBatch:
    values = json.loads(values_json)
    if not isinstance(values, list) or len(values) != len(batch.requests):
        raise ValueError("Provide one JSON array entry for every pending request, in order")
    responses = []
    for request, value in zip(batch.requests, values, strict=True):
        if request.kind == "approval" and isinstance(value, bool):
            responses.append(
                {"kind": "approval", "request_id": request.request_id, "approved": value}
            )
        elif request.kind == "question" and isinstance(value, str) and value.strip():
            responses.append(
                {"kind": "question", "request_id": request.request_id, "response": value}
            )
        else:
            raise ValueError(
                "Use booleans for approvals and nonempty strings for questions; "
                "external results require the native CLI/API"
            )
    return DecisionResponseBatch.model_validate(
        {
            "expected_continuation_id": batch.continuation_id,
            "responses": tuple(responses),
        }
    )
