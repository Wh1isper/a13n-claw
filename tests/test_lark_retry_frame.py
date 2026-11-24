"""Retry card actions use the real Lark websocket frame response format."""

import asyncio
import base64
import json

import pytest
from lark_oapi.ws.pb.pbbp2_pb2 import Frame

from a13n_claw.service_app.feishu import LarkAdapter, LarkSettings
from a13n_claw.service_app.lark_retry import card_body


@pytest.mark.parametrize(
    "outcome,expected", [(True, "success"), (False, "warning"), (None, "warning")]
)
def test_card_action_frame_acknowledges_only_confirmed_submission(outcome, expected):
    async def run():
        calls = []

        async def on_card(*fields):
            calls.append(fields)
            if outcome is None:
                await asyncio.sleep(3)
            return outcome

        async def receive(_message):
            raise AssertionError("card actions must not be admitted as ordinary messages")

        class Socket:
            response = None

            async def send(self, value):
                self.response = value

        socket = Socket()
        adapter = LarkAdapter(
            "connector",
            LarkSettings(app_id="app", bot_open_id="bot", senders=("user",)),
            "secret",
            on_card=on_card,
        )
        frame = Frame(SeqID=1, LogID=2, service=1, method=1)
        frame.headers.add(key="type", value="card")
        frame.payload = json.dumps(
            {
                "header": {"event_type": "card.action.trigger", "app_id": "app"},
                "event": {
                    "operator": {"open_id": "user"},
                    "context": {"open_chat_id": "chat", "open_message_id": "message"},
                    "action": {"value": {"card_id": "card", "token": "token"}},
                },
            }
        ).encode()
        try:
            await adapter._receive_frame(frame.SerializeToString(), socket, receive)
        finally:
            await adapter.close()
        response = Frame()
        response.ParseFromString(socket.response)
        envelope = json.loads(response.payload)
        assert envelope["code"] == 200
        assert json.loads(base64.b64decode(envelope["data"]))["toast"]["type"] == expected
        assert calls == [("card", "token", "user", "chat", "message")]

    asyncio.run(run())


def test_submitted_card_removes_retry_token():
    before = card_body("https://example.test/run", "card", "secret")
    after = card_body("https://example.test/run", "card", "secret", submitted=True)
    assert '"token": "secret"' in json.dumps(before)
    assert "secret" not in json.dumps(after)
    assert "Retry submitted" in json.dumps(after)
    unknown = card_body("https://example.test/run", "card", "secret", failure_code="private_code")
    assert "private_code" not in json.dumps(unknown)
    assert "The Main Run failed" in json.dumps(unknown)
