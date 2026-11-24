"""Extended Lark events require an authorized operator and conversation."""

import pytest

from a13n_claw.service_app.connectors import allows_lark_event
from a13n_claw.service_app.feishu import LarkSettings, normalize_event


@pytest.mark.parametrize(
    "kind,body,conversation",
    [
        ("im.chat.member.bot.added_v1", {"chat_id": "oc_group"}, "oc_group"),
        ("im.chat.member.user.added_v1", {"chat_id": "oc_group", "users": []}, "oc_group"),
        (
            "drive.notice.comment_add_v1",
            {
                "notice_meta": {"file_token": "doc_1", "from_user_id": {"open_id": "ou_operator"}},
                "comment_id": "c",
            },
            "drive/doc_1",
        ),
    ],
)
def test_extended_event_normalization_and_allowlists(kind, body, conversation):
    payload = {
        "header": {"app_id": "app", "event_id": "event", "event_type": kind},
        "event": {**body, "operator_id": {"open_id": "ou_operator"}},
    }
    event = normalize_event(payload, connection="connector", app_id="app")
    assert event is not None
    assert event.chat_id == conversation and event.payload == payload["event"]
    settings = LarkSettings(
        app_id="app", bot_open_id="ou_bot", senders=("ou_operator",), groups=("oc_group",)
    )
    assert allows_lark_event(settings, event)
    assert not allows_lark_event(settings.model_copy(update={"senders": ("ou_other",)}), event)
    assert not allows_lark_event(settings.model_copy(update={"event_types": ()}), event)
    if not conversation.startswith("drive/"):
        assert not allows_lark_event(settings.model_copy(update={"groups": ()}), event)
    with pytest.raises(ValueError, match="another application"):
        normalize_event(payload, connection="connector", app_id="different")


def test_unattributed_extended_event_is_not_admitted():
    payload = {
        "header": {
            "app_id": "app",
            "event_id": "event",
            "event_type": "im.chat.member.bot.added_v1",
        },
        "event": {"chat_id": "oc_group"},
    }
    assert normalize_event(payload, connection="connector", app_id="app") is None


def test_websocket_dispatch_respects_event_types_and_acknowledges():
    import asyncio
    import json

    from lark_oapi.ws.pb.pbbp2_pb2 import Frame

    from a13n_claw.service_app.feishu import InboundEvent, LarkAdapter

    async def run():
        settings = LarkSettings(
            app_id="app",
            bot_open_id="ou_bot",
            senders=("ou_operator",),
            groups=("oc_group",),
            event_types=("im.chat.member.user.added_v1",),
        )
        adapter = LarkAdapter("connector", settings, "synthetic-secret")
        received = []
        sent = []

        async def receive(event):
            received.append(event)

        class Socket:
            async def send(self, data):
                sent.append(data)

        async def deliver(kind):
            frame = Frame(
                SeqID=1,
                LogID=1,
                service=1,
                method=1,
                payload=json.dumps(
                    {
                        "header": {"app_id": "app", "event_id": "evt", "event_type": kind},
                        "event": {"chat_id": "oc_group", "operator_id": {"open_id": "ou_operator"}},
                    }
                ).encode(),
            )
            frame.headers.add(key="type", value="event")
            await adapter._receive_frame(frame.SerializeToString(), Socket(), receive)
            ack = Frame()
            ack.ParseFromString(sent[-1])
            assert json.loads(ack.payload) == {"code": 200}

        try:
            await deliver("im.chat.member.bot.added_v1")
            assert not received
            await deliver("im.chat.member.user.added_v1")
            assert len(received) == 1 and isinstance(received[0], InboundEvent)
        finally:
            await adapter.close()

    asyncio.run(run())
