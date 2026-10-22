"""Native mixed decisions through IM, including rejection and uncertain admission."""

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace

import pytest
import yaml
from a13n_harness.context import AgentContext
from a13n_harness_ui.configuration.mutation import ResourceMutationRequest
from a13n_harness_ui.errors import HarnessUiError
from pydantic_ai import Tool
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.toolsets import FunctionToolset

from a13n_claw.messaging.capability import messaging_integrations
from a13n_claw.runtime.host import open_host
from tests.helpers import command, enable_operator, message, setup, streaming_model


@asynccontextmanager
async def decision_service(tmp_path, monkeypatch, mode="per_trigger"):
    effects = []
    returned = []

    async def approved_effect(value: str) -> str:
        effects.append(value)
        return "Effect recorded"

    @dataclass
    class ApprovalFixture(AbstractCapability[AgentContext]):
        def get_toolset(self) -> FunctionToolset[AgentContext]:
            return FunctionToolset(tools=[Tool(approved_effect, requires_approval=True)])

    async def model(messages, info):
        results = [p for m in messages for p in m.parts if isinstance(p, ToolReturnPart)]
        if results:
            returned.extend(results)
            return ModelResponse(parts=[TextPart("Decisions processed")])
        return ModelResponse(
            parts=[
                ToolCallPart("approved_effect", {"value": "approved once"}),
                ToolCallPart(
                    "ask_user_question",
                    {
                        "questions": [
                            {
                                "header": "Choice",
                                "question": "Which option?",
                                "options": [
                                    {"label": "One", "description": "First option"},
                                    {"label": "Two", "description": "Second option"},
                                ],
                            }
                        ]
                    },
                ),
            ]
        )

    root, service, transport = await setup(
        tmp_path, monkeypatch, mode, model=streaming_model(model)
    )
    enable_operator(service)
    binding = service.settings.channels["room"].model_copy(
        update={"allowed_senders": ("user", "observer")}
    )
    service.settings = service.settings.model_copy(
        update={
            "channels": {
                "room": binding,
                "other": binding.model_copy(update={"chat_id": "other-chat"}),
            }
        }
    )
    integrations = messaging_integrations(service)
    integrations = replace(
        integrations, capabilities={**integrations.capabilities, "ApprovalFixture": ApprovalFixture}
    )
    async with open_host(root, service.settings, integrations=integrations) as host:
        service.bind(host)
        agent = yaml.safe_load((root / "host/agents/claw.yaml").read_text())
        agent["capabilities"].append({"capability": "ApprovalFixture"})
        await host.mutate_configuration(
            relative_path="agents/claw.yaml",
            request=ResourceMutationRequest(content=yaml.safe_dump(agent)),
        )
        if mode != "one_thread":
            await host.mutate_configuration(
                relative_path="projects/channel.yaml",
                request=ResourceMutationRequest(
                    content=yaml.safe_dump(
                        {
                            "schema_version": "1",
                            "kind": "project",
                            "id": "project-channel",
                            "name": "Channel",
                            "roots": [{"path": str(tmp_path / "work")}],
                            "defaults": {"agent": "agent-claw"},
                        }
                    )
                ),
            )
        await service.ingress.receive(message())
        await service.reconcile()
        attempt = service.store.rows("SELECT * FROM attempts")[0]
        item = service.store.rows("SELECT * FROM inbox")[0]
        operation = await host.wait_root_operation(attempt["receipt_id"], timeout_seconds=20)
        assert operation.status == "suspended", operation.model_dump_json()
        await command(service, "decisions", f"/claw decisions {item['id']}")
        pending = json.loads(transport.sent[-1][1].text)
        requests = {request["kind"]: request["request_id"] for request in pending["requests"]}
        assert set(requests) == {"approval", "question"}
        assert effects == []
        response = {
            "expected_continuation_id": pending["continuation_id"],
            "responses": [
                {"kind": "approval", "request_id": requests["approval"], "approved": True},
                {"kind": "question", "request_id": requests["question"], "response": "One"},
            ],
        }
        yield service, transport, service.store.item(item["id"]), response, effects, returned


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["one_thread", "per_channel", "per_trigger"])
@pytest.mark.parametrize("approved", [True, False])
async def test_mixed_decisions_allow_deny_and_duplicate_preserve_native_batch(
    tmp_path, monkeypatch, mode, approved
):
    async with decision_service(tmp_path, monkeypatch, mode) as data:
        service, transport, item, response, effects, returned = data
        response["responses"][0]["approved"] = approved
        incoming = await command(
            service, "answer", f"/claw answer {item['id']} {json.dumps(response)}"
        )
        receipt = json.loads(transport.sent[-1][1].text)
        result = await service.host.wait_root_operation(receipt["receipt_id"], timeout_seconds=20)
        assert result.status == "completed", result.model_dump_json()
        assert effects == (["approved once"] if approved else [])
        assert {part.tool_name for part in returned} == {"approved_effect", "ask_user_question"}
        assert await service.host.thread_decisions(thread_id=item["thread_id"]) is None
        await service.ingress.receive(incoming)
        await service.reconcile()
        await command(service, "stale", f"/claw answer {item['id']} {json.dumps(response)}")
        assert len(service.store.rows("SELECT * FROM attempts WHERE kind='continue'")) == 1
        assert len(service.store.rows("SELECT * FROM targets")) == 1
        assert effects == (["approved once"] if approved else [])


@pytest.mark.anyio
async def test_partial_native_batch_denies_omitted_approval(tmp_path, monkeypatch):
    async with decision_service(tmp_path, monkeypatch) as data:
        service, transport, item, response, effects, returned = data
        response["responses"] = response["responses"][1:]
        await command(service, "partial", f"/claw answer {item['id']} {json.dumps(response)}")
        receipt = json.loads(transport.sent[-1][1].text)
        result = await service.host.wait_root_operation(receipt["receipt_id"], timeout_seconds=20)
        assert result.status == "completed", result.model_dump_json()
        assert effects == []
        denied = next(part for part in returned if part.tool_name == "approved_effect")
        assert "No approval" in str(denied.content)
        assert await service.host.thread_decisions(thread_id=item["thread_id"]) is None


@pytest.mark.anyio
@pytest.mark.parametrize("invalid", ["unknown_id", "kind", "duplicate"])
@pytest.mark.parametrize("disposition", ["pending", "handled"])
async def test_invalid_decision_is_rejected_without_poisoning_original_work(
    tmp_path, monkeypatch, invalid, disposition
):
    async with decision_service(tmp_path, monkeypatch) as data:
        service, transport, item, response, effects, _ = data
        if disposition == "handled":
            item = service.store.settle(item["id"], item["version"], "handled", "Business settled")
        valid = json.loads(json.dumps(response))
        if invalid == "unknown_id":
            response["responses"][0]["request_id"] = "not-pending"
        elif invalid == "kind":
            response["responses"][0] = {
                "kind": "question",
                "request_id": response["responses"][0]["request_id"],
                "response": "Not an approval",
            }
        else:
            response["responses"].append(response["responses"][0])
        await command(service, "invalid", f"/claw answer {item['id']} {json.dumps(response)}")
        current = service.store.item(item["id"])
        assert current["blocked"] is None
        assert current["disposition"] == item["disposition"]
        assert not service.store.rows("SELECT * FROM attempts WHERE status='unknown'")
        assert effects == []
        pending = await service.host.thread_decisions(thread_id=item["thread_id"])
        assert pending.continuation_id == valid["expected_continuation_id"]
        await command(service, "corrected", f"/claw answer {item['id']} {json.dumps(valid)}")
        receipt = json.loads(transport.sent[-1][1].text)
        result = await service.host.wait_root_operation(receipt["receipt_id"], timeout_seconds=20)
        assert result.status == "completed"
        assert effects == ["approved once"]


@pytest.mark.anyio
async def test_decision_authority_uses_actor_channel_and_current_continuation(
    tmp_path, monkeypatch
):
    async with decision_service(tmp_path, monkeypatch) as data:
        service, transport, item, response, effects, _ = data
        text = f"/claw answer {item['id']} {json.dumps(response)}"
        await command(service, "observer", text, sender_id="observer")
        rejected = service.store.rows("SELECT * FROM inbox WHERE event_id='observer'")[0]
        assert rejected["disposition"] == "ignored" and rejected["body"] is None
        assert "operator grant" in rejected["reason"]
        await command(service, "cross-channel", text, chat_id="other-chat")
        assert "outside this Channel" in transport.sent[-1][1].text
        response["expected_continuation_id"] = "0" * 64
        await command(service, "stale", f"/claw answer {item['id']} {json.dumps(response)}")
        assert "continuation" in transport.sent[-1][1].text
        assert not service.store.rows("SELECT * FROM attempts WHERE kind='continue'")
        assert effects == []
        assert await service.host.thread_decisions(thread_id=item["thread_id"]) is not None


@pytest.mark.anyio
@pytest.mark.parametrize("code", ["transport_lost", "app_stopping"])
async def test_lost_decision_receipt_stays_unknown_after_native_acceptance(
    tmp_path, monkeypatch, code
):
    async with decision_service(tmp_path, monkeypatch) as data:
        service, _, item, response, effects, _ = data
        respond = service.host.respond_decisions
        accepted = []

        async def lost(**kwargs):
            receipt = await respond(**kwargs)
            accepted.append(receipt)
            raise HarnessUiError("Response lost", code=code)

        monkeypatch.setattr(service.host, "respond_decisions", lost)
        await command(service, "lost", f"/claw answer {item['id']} {json.dumps(response)}")
        await service.host.wait_root_operation(accepted[0].receipt_id, timeout_seconds=20)
        assert effects == ["approved once"]
        assert len(service.store.rows("SELECT * FROM attempts WHERE status='unknown'")) == 2
        assert service.store.item(item["id"])["blocked"] is not None
        await service.reconcile()
        assert len(accepted) == 1
        assert service.store.item(item["id"])["blocked"] is not None
