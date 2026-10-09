"""Independent child ownership, saved outcome delivery and native runtime tools."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from a13n_harness import HarnessState
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from a13n_claw.coordinator import Coordinator
from a13n_claw.domain import ClawError, InputRequest, Principal
from a13n_claw.runtime import Runtime
from a13n_claw.storage import Store


def configure(store: Store):
    store.save_resource(
        "operator", "profile", "child", {"model_id": "model", "environment_id": "local"}, 0
    )
    store.save_resource(
        "operator",
        "profile",
        "default",
        {
            "model_id": "model",
            "environment_id": "local",
            "child_profiles": ["child"],
        },
        1,
    )


def running_parent(store: Store):
    configure(store)
    thread = store.create_thread("operator", "Parent", "default")
    receipt = store.admit(
        "operator", thread.id, InputRequest(request_id="parent", text="work"), "/shared"
    )
    store.claim(receipt.run_id, "parent-owner", "/shared")
    store.start(receipt.run_id, "parent-owner")
    return thread, receipt


def finish_child(store: Store, child):
    store.claim(child.child_run_id, "child-owner", "/shared")
    inputs = store.start(child.child_run_id, "child-owner")
    store.publish(
        child.child_run_id,
        "child-owner",
        HarnessState.new(thread_id=child.child_thread_id),
        incorporated=tuple(item.id for item in inputs),
        status="completed",
        output="child saved result",
    )


def test_delegation_captures_bounded_profile_and_retries_same_admission(runtime_store: Store):
    thread, parent = running_parent(runtime_store)
    child = runtime_store.delegate_work(parent.run_id, "parent-owner", "call", "child", "Do this")
    assert (
        runtime_store.delegate_work(parent.run_id, "parent-owner", "call", "child", "Do this")
        == child
    )
    assert child.status == "queued" and child.output is None
    child_thread = runtime_store.thread("operator", child.child_thread_id)
    assert child_thread.parent_thread_id == thread.id
    assert child_thread.parent_run_id == parent.run_id
    assert child_thread.checkpoint_id is None
    with pytest.raises(ClawError, match="not in the captured"):
        runtime_store.delegate_work(parent.run_id, "parent-owner", "wrong", "default", "Do this")
    with pytest.raises(ClawError, match="identity was reused"):
        runtime_store.delegate_work(parent.run_id, "parent-owner", "call", "child", "Different")
    runtime_store.save_resource(
        "operator",
        "profile",
        "child",
        {"model_id": "model", "environment_id": "local", "agent": {"instructions": "Changed"}},
        1,
    )
    assert runtime_store.run("operator", child.child_run_id).composition.profile.version == 1
    finish_child(runtime_store, child)
    saved = runtime_store.child_progress(parent.run_id, "parent-owner", child.id)
    assert saved.status == "completed" and saved.output == "child saved result"
    assert runtime_store.thread("operator", thread.id).checkpoint_id is None


def test_child_notification_is_one_durable_input_even_after_restart(runtime_store: Store):
    thread, parent = running_parent(runtime_store)
    child = runtime_store.delegate_work(
        parent.run_id, "parent-owner", "call", "child", "Do this", notify=True
    )
    finish_child(runtime_store, child)
    runtime_store.deliver_child_results()
    reopened = Store(runtime_store.path)
    reopened.deliver_child_results()
    saved = reopened.delegations("operator", parent.run_id)[0]
    inputs = [
        item for item in reopened.inputs("operator", thread.id) if item.source == "child-result"
    ]
    assert len(inputs) == 1
    assert inputs[0].id == saved.delivery_input_id
    assert inputs[0].run_id == parent.run_id
    assert inputs[0].disposition == "steering"
    assert '"status":"completed"' in inputs[0].text


@pytest.mark.parametrize("policy,expected", [("keep", "queued"), ("cancel", "cancelled")])
def test_parent_cancel_propagation_is_explicit(runtime_store: Store, policy, expected):
    _, parent = running_parent(runtime_store)
    child = runtime_store.delegate_work(
        parent.run_id, "parent-owner", "call", "child", "Do this", cancel_policy=policy, notify=True
    )
    runtime_store.cancel("operator", parent.run_id)
    runtime_store.retire_owner(parent.run_id, "parent-owner")
    assert runtime_store.run("operator", child.child_run_id).status == expected
    if policy == "keep":
        finish_child(runtime_store, child)
    runtime_store.deliver_child_results()
    saved = runtime_store.delegations("operator", parent.run_id)[0]
    assert saved.delivery_input_id is None and saved.delivery_error == "parent_stopped"
    assert runtime_store.run("operator", parent.run_id).status == "cancelled"


def test_revoking_parent_scope_also_blocks_existing_child(runtime_store: Store):
    configure(runtime_store)
    actor = Principal(
        id="client", actions=("read", "submit", "create"), profile_ids=("default", "child")
    )
    runtime_store.set_principal("operator", actor, "hash")
    thread = runtime_store.create_thread("client", "Parent", "default")
    receipt = runtime_store.admit(
        "client", thread.id, InputRequest(request_id="work", text="work"), "/shared"
    )
    runtime_store.claim(receipt.run_id, "owner", "/shared")
    runtime_store.start(receipt.run_id, "owner")
    child = runtime_store.delegate_work(receipt.run_id, "owner", "call", "child", "Do this")
    actor = runtime_store.principal("client")
    runtime_store.set_principal(
        "operator", actor.model_copy(update={"thread_ids": (child.child_thread_id,)}), "hash"
    )
    with pytest.raises(ClawError, match="not permitted"):
        runtime_store.claim(child.child_run_id, "child-owner", "/shared")


@pytest.mark.anyio
async def test_production_runtime_delegates_and_collects_saved_child_result(
    runtime_store: Store, tmp_path
):
    configure(runtime_store)
    runtime_store.save_resource(
        "operator",
        "model",
        "child-model",
        {
            "provider": "openai",
            "model_name": "child",
            "credential_env": "TEST_KEY",
        },
        0,
    )
    runtime_store.save_resource(
        "operator", "profile", "child", {"model_id": "child-model", "environment_id": "local"}, 1
    )
    runtime_store.save_credential("operator", "TEST_KEY", "offline-fixture")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    parent_results = []

    async def child_model(messages, info):
        yield "Independently saved child outcome"

    async def parent_model(messages, info):
        results = [
            part
            for message in messages
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        inspected = next((part for part in results if part.tool_name == "inspect_delegation"), None)
        if inspected is not None:
            parent_results.append(inspected.content)
            yield "Collected saved result"
        else:
            delegated = next((part for part in results if part.tool_name == "delegate_work"), None)
            if delegated is None:
                yield {
                    0: DeltaToolCall(
                        name="delegate_work",
                        json_args='{"profile_id":"child","prompt":"Compute the answer"}',
                        tool_call_id="delegate",
                    )
                }
            else:
                import json

                content = delegated.content
                if isinstance(content, str):
                    content = json.loads(content)
                yield {
                    0: DeltaToolCall(
                        name="inspect_delegation",
                        json_args=json.dumps({"delegation_id": content["id"], "wait_seconds": 10}),
                        tool_call_id="inspect",
                    )
                }

    @asynccontextmanager
    async def models(definition, credential):
        yield FunctionModel(
            stream_function=child_model if definition.model_name == "child" else parent_model
        )

    runtime = Runtime(runtime_store, tmp_path, models=models)
    thread = runtime_store.create_thread("operator", "Parent", "default")
    receipt = runtime_store.admit(
        "operator",
        thread.id,
        InputRequest(request_id="parent", text="Delegate work"),
        str(workspace),
    )
    coordinator = Coordinator(runtime_store, str(workspace), runtime)
    await coordinator.start()
    try:
        async with asyncio.timeout(20):
            while runtime_store.run("operator", receipt.run_id).status not in {
                "completed",
                "failed",
            }:
                await asyncio.sleep(0.02)
        result = runtime_store.run("operator", receipt.run_id)
        assert result.status == "completed", result.error
        child = runtime_store.delegations("operator", receipt.run_id)[0]
        assert child.status == "completed"
        assert child.output == "Independently saved child outcome"
        assert parent_results
        assert result.output == "Collected saved result"
        assert (
            runtime_store.thread("operator", thread.id).checkpoint_id
            != runtime_store.thread("operator", child.child_thread_id).checkpoint_id
        )
    finally:
        await coordinator.close()
