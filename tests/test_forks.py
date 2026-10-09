"""Forks copy portable continuation, not work, provider targets or authority."""

import pytest
from a13n_harness import HarnessState
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart

from a13n_claw.domain import ClawError, InputRequest, Principal
from a13n_claw.storage import Store


def source_checkpoint(store: Store):
    thread = store.create_thread("operator", "Original", "default")
    receipt = store.admit(
        "operator", thread.id, InputRequest(request_id="source", text="source"), "/shared"
    )
    store.claim(receipt.run_id, "source-owner", "/shared")
    store.start(receipt.run_id, "source-owner")
    state = HarnessState.new(
        thread_id=thread.id,
        message_history=[
            ModelRequest(parts=[UserPromptPart("Keep the history")]),
            ModelResponse(parts=[TextPart("Saved answer")]),
        ],
    )
    checkpoint = store.publish(
        receipt.run_id, "source-owner", state, incorporated=(receipt.id,), status="completed"
    )
    return thread, receipt, checkpoint, state


def test_fork_retains_source_and_selects_distinct_native_continuation(runtime_store: Store):
    source, receipt, checkpoint, state = source_checkpoint(runtime_store)
    fork = runtime_store.fork_thread("operator", checkpoint, "fork", "Branch", "default")
    assert runtime_store.fork_thread("operator", checkpoint, "fork", "Branch", "default") == fork
    assert fork.id != source.id
    assert fork.parent_thread_id == source.id and fork.parent_run_id == receipt.run_id
    assert fork.fork_checkpoint_id == checkpoint
    assert fork.checkpoint_id is not None and fork.checkpoint_id != checkpoint
    copied = runtime_store.checkpoint(fork.checkpoint_id)
    assert copied.thread_id == fork.id
    assert copied.message_history == state.message_history
    assert copied.environment_states == {}
    assert runtime_store.inputs("operator", fork.id) == []
    assert runtime_store.runs("operator", fork.id) == []
    assert runtime_store.targets("operator", fork.id) == []
    assert runtime_store.thread("operator", source.id).checkpoint_id == checkpoint
    submitted = runtime_store.admit(
        "operator",
        fork.id,
        InputRequest(request_id="fork-input", text="Continue separately"),
        "/shared",
    )
    assert (
        runtime_store.claim(submitted.run_id, "fork-owner", "/shared").checkpoint_id
        == fork.checkpoint_id
    )
    assert runtime_store.thread("operator", source.id).checkpoint_id == checkpoint
    with pytest.raises(ClawError, match="identity was reused"):
        runtime_store.fork_thread("operator", checkpoint, "fork", "Changed title", "default")


def test_fork_requires_source_read_and_explicit_current_profile_authority(runtime_store: Store):
    source, _, checkpoint, _ = source_checkpoint(runtime_store)
    actor = Principal(id="client", actions=("read", "submit", "create"), profile_ids=("default",))
    runtime_store.set_principal("operator", actor, "hash")
    with pytest.raises(ClawError, match="not permitted"):
        runtime_store.fork_thread("client", checkpoint, "fork", "Branch", "default")
    runtime_store.set_principal(
        "operator", actor.model_copy(update={"thread_ids": (source.id,)}), "hash"
    )
    fork = runtime_store.fork_thread("client", checkpoint, "fork", "Branch", "default")
    assert runtime_store.thread("client", fork.id).id == fork.id
    # Removing the original scope does not remove separately granted fork authority.
    actor = runtime_store.principal("client")
    runtime_store.set_principal(
        "operator", actor.model_copy(update={"thread_ids": (fork.id,)}), "hash"
    )
    receipt = runtime_store.admit(
        "client", fork.id, InputRequest(request_id="new", text="work"), "/shared"
    )
    assert runtime_store.claim(receipt.run_id, "owner", "/shared").status == "preparing"
