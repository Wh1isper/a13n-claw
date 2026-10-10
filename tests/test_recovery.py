"""Recovery retains accepted facts without granting replay of unknown actions."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from a13n_harness import DeferredToolResume, HarnessState
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.tools import DeferredToolRequests, DeferredToolResults

from a13n_claw.decisions import REQUESTS, RESULTS
from a13n_claw.domain import ClawError, InputRequest
from a13n_claw.storage import Store


def waiting(store: Store, *, uncertain_input: bool = False):
    thread = store.create_thread("operator", "Recovery", "default")
    receipt = store.admit(
        "operator", thread.id, InputRequest(request_id="first", text="work"), "/shared"
    )
    store.claim(receipt.run_id, "owner", "/shared")
    store.start(receipt.run_id, "owner")
    calls = [ToolCallPart("question", {}, "question"), ToolCallPart("effect", {}, "effect")]
    requests = DeferredToolRequests(calls=[calls[0]], approvals=[calls[1]])
    state = HarnessState.new(
        thread_id=thread.id,
        message_history=(ModelResponse(parts=calls, state="suspended"),),
    )
    if uncertain_input:
        late = store.admit(
            "operator", thread.id, InputRequest(request_id="late", text="late"), "/shared"
        )
        store.take_steering(receipt.run_id, "owner")
        store.steering_delivered(receipt.run_id, "owner", late.id, accepted=True)
    store.publish(
        receipt.run_id,
        "owner",
        state,
        incorporated=(receipt.id,),
        status="waiting",
        requests=REQUESTS.dump_json(requests).decode(),
    )
    answer = DeferredToolResults(calls={"question": "Keep this answer"}, approvals={"effect": True})
    decision = store.decisions("operator", receipt.run_id)[0]["id"]
    return thread, receipt, state, requests, answer, decision


def test_waiting_uncertain_delivery_requires_explicit_review(runtime_store: Store):
    thread, receipt, _, _, answer, decision = waiting(runtime_store, uncertain_input=True)
    late = next(
        item for item in runtime_store.inputs("operator", thread.id) if item.request_id == "late"
    )
    assert late.disposition == "uncertain"
    serialized = RESULTS.dump_json(answer).decode()
    with pytest.raises(ClawError, match="Review uncertain input"):
        runtime_store.answer("operator", decision, serialized)
    reopened = Store(runtime_store.path)
    assert reopened.reconcile_startup() == []
    reviewed = reopened.acknowledge_uncertainty("operator", late.id, "Do not replay this input")
    assert reviewed.disposition == "unapplied"
    reopened.answer("operator", decision, serialized)
    assert reopened.run("operator", receipt.run_id).status == "queued"


def test_partial_accepted_facts_follow_the_exact_checkpoint_into_recovery(runtime_store: Store):
    thread, receipt, state, requests, answer, decision = waiting(runtime_store)
    runtime_store.answer("operator", decision, RESULTS.dump_json(answer).decode())
    runtime_store.claim(receipt.run_id, "resumed", "/shared")
    runtime_store.start(receipt.run_id, "resumed")
    # One call completed; the external answer is accepted but not incorporated.
    partial = HarnessState.new(
        thread_id=state.thread_id,
        message_history=(
            *state.message_history,
            ModelRequest(parts=[ToolReturnPart("effect", "Completed once", "effect")]),
        ),
    )
    remaining = DeferredToolResume(requests, answer).remaining(partial.message_history)
    assert remaining is not None
    runtime_store.publish(
        receipt.run_id, "resumed", partial, status="failed", pending_deferred=remaining
    )
    reopened = Store(runtime_store.path)
    reopened.reconcile_run("operator", receipt.run_id, "Checked the one completed effect")
    recovery = reopened.recover("operator", receipt.run_id, "recovery", "Continue", "/shared")
    reopened.claim(recovery.id, "new-owner", "/shared")
    restored = reopened.resume_decision(recovery.id, "new-owner")
    assert restored is not None and restored.recovery
    assert restored.results.calls == {"question": "Keep this answer"}
    assert restored.results.approvals == {}
    assert [call.tool_call_id for call in restored.requests.calls] == ["question"]
    assert (
        reopened.thread("operator", thread.id).checkpoint_id
        == reopened.run("operator", receipt.run_id).checkpoint_id
    )


def test_recovery_admission_retries_do_not_create_competing_work(runtime_store: Store):
    _, receipt, _, _, answer, decision = waiting(runtime_store)
    runtime_store.answer("operator", decision, RESULTS.dump_json(answer).decode())
    runtime_store.claim(receipt.run_id, "resumed", "/shared")
    runtime_store.reconcile_startup()
    with pytest.raises(ClawError, match="Review external effects"):
        runtime_store.recover("operator", receipt.run_id, "same", "Continue", "/shared")
    runtime_store.reconcile_run("operator", receipt.run_id, "Nothing executed after acceptance")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(
                lambda _: runtime_store.recover(
                    "operator", receipt.run_id, "same", "Continue", "/shared"
                ),
                range(8),
            )
        )
    assert len({result.id for result in results}) == 1
    with pytest.raises(ClawError, match="already has recovery work"):
        runtime_store.recover("operator", receipt.run_id, "different", "Continue", "/shared")


@pytest.mark.parametrize("release_old", [False, True])
def test_ordinary_messages_select_recovery_before_older_queue(
    runtime_store: Store, release_old: bool
):
    thread, receipt, _, _, answer, decision = waiting(runtime_store)
    runtime_store.answer("operator", decision, RESULTS.dump_json(answer).decode())
    runtime_store.claim(receipt.run_id, "lost-owner", "/shared")
    runtime_store.reconcile_startup()
    # A separate older queue must not intercept input while recovery owns advancement.
    old = runtime_store.admit(
        "operator",
        thread.id,
        InputRequest(request_id="old", text="old", separate_run=True),
        "/shared",
    )
    assert old.disposition == "blocked"
    runtime_store.reconcile_run("operator", receipt.run_id, "Reviewed interrupted preparation")
    recovery = runtime_store.recover("operator", receipt.run_id, "recover", "Continue", "/shared")
    if release_old:
        runtime_store.release_blocked("operator", thread.id, "/shared")
    queued = runtime_store.admit(
        "operator", thread.id, InputRequest(request_id="queued", text="queued"), "/shared"
    )
    assert queued.run_id == recovery.id != old.run_id
    assert queued.disposition == "pending"
    runtime_store.claim(recovery.id, "new-owner", "/shared")
    initial = runtime_store.start(recovery.id, "new-owner")
    steering = runtime_store.admit(
        "operator", thread.id, InputRequest(request_id="steer", text="steer"), "/shared"
    )
    assert steering.run_id == recovery.id
    assert steering.disposition == "steering"
    calls = [ToolCallPart("question", {}, "new-question")]
    state = HarnessState.new(
        thread_id=thread.id, message_history=[ModelResponse(parts=calls, state="suspended")]
    )
    runtime_store.publish(
        recovery.id,
        "new-owner",
        state,
        incorporated=tuple(item.id for item in initial),
        status="waiting",
        requests=REQUESTS.dump_json(DeferredToolRequests(calls=calls)).decode(),
    )
    held = runtime_store.admit(
        "operator", thread.id, InputRequest(request_id="held", text="held"), "/shared"
    )
    assert held.run_id == recovery.id
    assert held.disposition == "held"
