"""Native file-memory tools across real routing, fresh tasks, workers, and restart."""

import json

import pytest
import yaml
from a13n_harness_ui.configuration.mutation import ResourceMutationRequest
from a13n_harness_ui.surfaces import NewThreadDefaults
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart

from a13n_claw.messaging.capability import messaging_integrations
from a13n_claw.runtime.host import open_host
from tests.helpers import message, setup, streaming_model


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["one_thread", "per_channel", "per_trigger"])
async def test_native_memory_scopes_survive_routing_and_restart(tmp_path, monkeypatch, mode):
    steps = []
    observed = []

    async def model(messages, info):
        observed[:] = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if steps:
            return ModelResponse(parts=[steps.pop(0)])
        return ModelResponse(parts=[TextPart("Memory operation complete")])

    root, service, _ = await setup(tmp_path, monkeypatch, mode, streaming_model(model))
    first_binding = service.settings.channels["room"]
    other = first_binding.model_copy(
        update={
            "chat_id": "other-chat",
            "project_id": "project-other" if mode != "one_thread" else None,
        }
    )
    service.settings = service.settings.model_copy(
        update={"channels": {"room": first_binding, "other": other}}
    )

    async def receive(host, event, chat="chat"):
        await service.ingress.receive(message(event).model_copy(update={"chat_id": chat}))
        await service.reconcile()
        attempt = service.store.rows("SELECT * FROM attempts ORDER BY created DESC LIMIT 1")[0]
        operation = await host.wait_root_operation(attempt["receipt_id"], timeout_seconds=15)
        assert operation.status == "completed", operation
        assert not steps
        await service.reconcile()
        # This test drives memory, not model-selected business disposition.
        item = service.store.rows("SELECT * FROM inbox WHERE event_id=?", (event,))[0]
        if item["disposition"] == "pending":
            service.store.settle(item["id"], item["version"], "handled", "Memory verified")
        return attempt["thread_id"]

    def create(memory, path, content):
        return ToolCallPart(
            "memory_file_create", {"memory": memory, "path": path, "content": content}
        )

    def read(memory, path):
        return ToolCallPart("memory_file_view", {"memory": memory, "path": path})

    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        if mode != "one_thread":
            for key in ("project-channel", "project-other"):
                await host.mutate_configuration(
                    relative_path=f"projects/{key}.yaml",
                    request=ResourceMutationRequest(
                        content=yaml.safe_dump(
                            {
                                "schema_version": "1",
                                "kind": "project",
                                "id": key,
                                "name": key,
                                "roots": [{"path": str(tmp_path / "work")}],
                                "defaults": {"agent": "agent-claw"},
                            }
                        )
                    ),
                )
        steps[:] = [
            create("global", "shared.md", "Shared fact: orchard"),
            create("project", "channel.md", "Channel fact: cedar"),
        ]
        first = await receive(host, "first")
        project_id = service.settings.main_project_id if mode == "one_thread" else "project-channel"
        assert (await host.memory_file(path="shared.md")).text == "Shared fact: orchard"
        assert (
            await host.memory_file(path="channel.md", project_id=project_id)
        ).text == "Channel fact: cedar"
        steps[:] = [read("global", "shared.md"), read("project", "channel.md")]
        second = await receive(host, "second")
        assert (first != second) == (mode == "per_trigger")
        assert "orchard" in json.dumps(observed[-2].content)
        assert "cedar" in json.dumps(observed[-1].content)
        steps[:] = [read("global", "shared.md"), read("project", "channel.md")]
        third = await receive(host, "third", "other-chat")
        assert "orchard" in json.dumps(observed[-2].content)
        if mode == "one_thread":
            assert third == first
            assert "cedar" in json.dumps(observed[-1].content)
            await host.set_auto_followup(first, False)
            worker = await host.create_thread(
                coordinator_thread_id=first, defaults=NewThreadDefaults(project_id=project_id)
            )
            steps[:] = [
                read("project", "channel.md"),
                create("project", "worker.md", "Worker fact: birch"),
            ]
            receipt = await host.submit_thread(
                thread_id=worker.thread_id, prompt="Read and remember"
            )
            assert (
                await host.wait_root_operation(receipt.receipt_id, timeout_seconds=15)
            ).status == "completed"
            assert "cedar" in json.dumps(observed[-2].content)
            assert (
                await host.memory_file(path="worker.md", project_id=project_id)
            ).text == "Worker fact: birch"
        else:
            assert third != first
            assert "cedar" not in json.dumps(observed[-1].content)
            assert await host.memory_files(project_id="project-other") == ()
    async with open_host(
        root, service.settings, integrations=messaging_integrations(service)
    ) as host:
        service.bind(host)
        steps[:] = [read("global", "shared.md"), read("project", "channel.md")]
        restarted = await receive(host, "restarted")
        assert (restarted != first) == (mode == "per_trigger")
        assert "orchard" in json.dumps(observed[-2].content)
        assert "cedar" in json.dumps(observed[-1].content)
        if mode == "one_thread":
            assert (
                await host.memory_file(path="worker.md", project_id=project_id)
            ).text == "Worker fact: birch"
        # Read-only memory inspection must not offer organizer inference.
        status = await host.status()
        assert status.memory_organization.attempts == 0
