"""Business boundaries against the real embedded App, with a deterministic model only."""

from types import SimpleNamespace

from a13n_harness_ui.model_runtime import HarnessUiModelResolver
from pydantic_ai.messages import TextPart, ToolCallPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel

from a13n_claw.configuration.bootstrap import initialize
from a13n_claw.configuration.models import ClawSettings
from a13n_claw.database.store import BusinessStore
from a13n_claw.messaging.transport import InboundMessage, SendResult
from a13n_claw.runtime.service import ClawService


def settings(mode="one_thread"):
    return ClawSettings.model_validate(
        {
            "mode": mode,
            "retry_seconds": 1,
            "connections": {"lark": {"app_id": "test-app", "bot_open_id": "bot"}},
            "channels": {
                "room": {
                    "connection": "lark",
                    "chat_id": "chat",
                    "allowed_senders": ["user"],
                    "shared_context_acknowledged": True,
                    "project_id": "project-channel" if mode != "one_thread" else None,
                }
            },
        }
    )


def message(event="event-1", **changes):
    return InboundMessage(
        platform="lark",
        connection="lark",
        event_id=event,
        message_id=f"msg-{event}",
        chat_id="chat",
        sender_id="user",
        text="Please reply",
        **changes,
    )


def adapter():
    sent = []

    async def send(destination, body, *, request_id):
        sent.append((destination, body, request_id))
        return SendResult("sent", "platform-message-id")

    async def download(*args):
        return b"attachment"

    async def close():
        pass

    return SimpleNamespace(send=send, download=download, close=close, sent=sent)


def streaming_model(function):
    async def stream(messages, info):
        response = await function(messages, info)
        for index, part in enumerate(response.parts):
            if isinstance(part, TextPart):
                yield part.content
            elif isinstance(part, ToolCallPart):
                yield {index: DeltaToolCall(name=part.tool_name, json_args=part.args_as_json_str())}

    return FunctionModel(stream_function=stream)


async def setup(tmp_path, monkeypatch, mode="one_thread", model=None):
    monkeypatch.setenv("A13N_OFFICIAL_MODELS_AUTO_UPDATE", "0")

    async def resolve(self, model_id, *, thread_id):
        return model or TestModel(call_tools=[], custom_output_text="Not broadcast")

    monkeypatch.setattr(HarnessUiModelResolver, "resolve", resolve)
    root, workspace = tmp_path / "claw", tmp_path / "work"
    workspace.mkdir()
    await initialize(root, workspace, model_route="openai:gpt-4o")
    config = settings(mode)
    store = BusinessStore(root / "business.sqlite3", config)
    transport = adapter()
    service = ClawService(config, store, {"lark": transport})
    return root, service, transport


def enable_operator(service):
    binding = service.settings.channels["room"].model_copy(update={"operators": ("user",)})
    service.settings = service.settings.model_copy(update={"channels": {"room": binding}})


async def command(service, event, text, **changes):
    incoming = message(event, is_group=True).model_copy(update={"text": text, **changes})
    await service.ingress.receive(incoming)
    await service.reconcile()
    return incoming
