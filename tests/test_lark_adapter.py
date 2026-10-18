"""Wire tests: real loopback WebSocket frames, offline HTTP transport, no Lark account."""

import asyncio
import json

import httpx
import pytest
from lark_oapi.ws.pb.pbbp2_pb2 import Frame
from websockets.asyncio.server import serve

from a13n_claw.lark import LarkAdapter, normalize_message
from a13n_claw.settings import LarkConnection
from a13n_claw.transport import Destination, OutboundMessage


def event():
    return {
        "header": {"app_id": "app", "event_id": "event", "event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_type": "user", "sender_id": {"open_id": "sender"}},
            "message": {
                "message_id": "message",
                "chat_id": "chat",
                "chat_type": "group",
                "message_type": "text",
                "content": json.dumps({"text": "@_user_1 hello"}),
                "mentions": [{"key": "@_user_1", "id": {"open_id": "bot"}}],
            },
        },
    }


def test_identity_mentions_and_app_scope():
    payload = event()
    message = normalize_message(payload, connection="connection", app_id="app", bot_open_id="bot")
    assert message is not None and message.mentioned and message.text == "hello"
    payload["event"]["message"]["mentions"] = []
    message = normalize_message(payload, connection="connection", app_id="app", bot_open_id="bot")
    assert message is not None and not message.mentioned
    payload["header"]["app_id"] = "other"
    with pytest.raises(ValueError, match="another application"):
        normalize_message(payload, connection="connection", app_id="app", bot_open_id="bot")
    payload["header"]["app_id"] = "app"
    payload["event"]["sender"]["sender_type"] = "app"
    assert (
        normalize_message(payload, connection="connection", app_id="app", bot_open_id="bot") is None
    )


@pytest.mark.anyio
async def test_long_connection_ack_follows_retention_and_close_is_clean(monkeypatch):
    monkeypatch.setenv("TEST_LARK_SECRET", "test-only")
    entered, committed, acknowledged = asyncio.Event(), asyncio.Event(), asyncio.Event()
    observations = []

    async def receive(message):
        observations.append(message)
        entered.set()
        await committed.wait()

    async def peer(socket):
        ping = Frame()
        ping.ParseFromString(await socket.recv())
        assert ping.method == 0
        frame = Frame(SeqID=1, LogID=2, service=7, method=1, payload=json.dumps(event()).encode())
        frame.headers.add(key="type", value="event")
        await socket.send(frame.SerializeToString())
        await entered.wait()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(socket.recv(), 0.05)
        committed.set()
        ack = Frame()
        ack.ParseFromString(await socket.recv())
        assert json.loads(ack.payload) == {"code": 200}
        assert ack.SeqID == 1
        acknowledged.set()
        await socket.wait_closed()

    async with serve(peer, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]

        async def http(request):
            assert request.url.path == "/callback/ws/endpoint"
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "URL": f"ws://127.0.0.1:{port}/?service_id=7",
                        "ClientConfig": {"PingInterval": 60},
                    },
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(http)) as client:
            adapter = LarkAdapter(
                "connection",
                LarkConnection(app_id="app", bot_open_id="bot", app_secret_env="TEST_LARK_SECRET"),
                client=client,
            )
            task = asyncio.create_task(adapter.run(receive))
            try:
                await asyncio.wait_for(acknowledged.wait(), 5)
                assert len(observations) == 1
            finally:
                await adapter.close()
                await asyncio.wait_for(task, 5)
            assert adapter.status == "disconnected"


@pytest.mark.anyio
@pytest.mark.parametrize("outcome", ["sent", "rejected", "timeout", "malformed", "server_error"])
async def test_send_outcome_and_no_automatic_retry(monkeypatch, outcome):
    monkeypatch.setenv("TEST_LARK_SECRET", "test-only")
    requests = []

    async def http(request):
        if request.url.path.endswith("tenant_access_token/internal"):
            return httpx.Response(
                200, json={"code": 0, "tenant_access_token": "fake-token", "expire": 7200}
            )
        requests.append(request)
        if outcome == "timeout":
            raise httpx.ReadTimeout("fake lost response")
        if outcome == "malformed":
            return httpx.Response(200, content=b"not-json")
        if outcome == "server_error":
            return httpx.Response(503)
        return httpx.Response(
            200, json={"code": 0 if outcome == "sent" else 999, "data": {"message_id": "sent-id"}}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(http)) as client:
        adapter = LarkAdapter(
            "connection",
            LarkConnection(app_id="app", bot_open_id="bot", app_secret_env="TEST_LARK_SECRET"),
            client=client,
        )
        result = await adapter.send(
            Destination(chat_id="chat", reply_to="original"),
            OutboundMessage(text="reply"),
            request_id="delivery-1",
        )
        expected = (
            "sent" if outcome == "sent" else "not_sent" if outcome == "rejected" else "unknown"
        )
        assert result.status == expected
        assert len(requests) == 1
        assert requests[0].url.path.endswith("/messages/original/reply")
        assert len(json.loads(requests[0].content)["uuid"]) == 32


@pytest.mark.anyio
@pytest.mark.parametrize("upload_ok", [True, False])
async def test_file_upload_precedes_one_delivery(monkeypatch, upload_ok):
    import base64

    from a13n_claw.transport import OutboundFile

    monkeypatch.setenv("TEST_LARK_SECRET", "test-only")
    requests = []

    async def http(request):
        requests.append(request)
        if request.url.path.endswith("tenant_access_token/internal"):
            return httpx.Response(
                200, json={"code": 0, "tenant_access_token": "fake", "expire": 7200}
            )
        if request.url.path.endswith("/files"):
            assert b"report.txt" in request.content and b"the result" in request.content
            if not upload_ok:
                raise httpx.ReadTimeout("upload response lost")
            return httpx.Response(200, json={"code": 0, "data": {"file_key": "file-key"}})
        body = json.loads(request.content)
        assert body["msg_type"] == "file"
        assert json.loads(body["content"]) == {"file_key": "file-key"}
        return httpx.Response(200, json={"code": 0, "data": {"message_id": "reply-id"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(http)) as client:
        adapter = LarkAdapter(
            "connection",
            LarkConnection(
                app_id="app",
                bot_open_id="bot",
                app_secret_env="TEST_LARK_SECRET",
            ),
            client=client,
        )
        result = await adapter.send(
            Destination(chat_id="chat", reply_to="original"),
            OutboundMessage(
                file=OutboundFile(name="report.txt", data=base64.b64encode(b"the result").decode())
            ),
            request_id="delivery-file",
        )
        assert result.status == ("sent" if upload_ok else "not_sent")
        assert len(requests) == (3 if upload_ok else 2)


@pytest.mark.anyio
async def test_multipart_event_bounds_and_conflicting_fragments():
    from types import SimpleNamespace

    import httpx
    from lark_oapi.ws.pb.pbbp2_pb2 import Frame

    from a13n_claw.lark import LarkAdapter
    from a13n_claw.settings import LarkConnection

    async def unused(*args):
        raise AssertionError("Incomplete payload must not be dispatched or acknowledged")

    def frame(message_id, count, sequence, payload=b"fragment"):
        value = Frame(SeqID=0, LogID=0, service=1, method=1, payload=payload)
        for key, data in {
            "message_id": message_id,
            "sum": str(count),
            "seq": str(sequence),
            "type": "event",
        }.items():
            value.headers.add(key=key, value=data)
        return value.SerializeToString()

    async with httpx.AsyncClient() as client:
        adapter = LarkAdapter(
            "lark", LarkConnection(app_id="app", bot_open_id="bot"), client=client
        )
        socket = SimpleNamespace(send=unused)
        with pytest.raises(ValueError, match="Invalid multipart"):
            await adapter._receive_frame(frame("invalid", 0, 0), socket, unused)
        await adapter._receive_frame(frame("one", 2, 0), socket, unused)
        with pytest.raises(ValueError, match="Conflicting multipart payload"):
            await adapter._receive_frame(frame("one", 2, 0, b"different"), socket, unused)
        for index in range(63):
            await adapter._receive_frame(frame(str(index), 2, 0), socket, unused)
        with pytest.raises(ValueError, match="Too many"):
            await adapter._receive_frame(frame("overflow", 2, 0), socket, unused)
        adapter._fragments.clear()
        await adapter._receive_frame(
            frame("large", 2, 0, b"a" * (11 * 1024 * 1024)), socket, unused
        )
        with pytest.raises(ValueError, match="budget exceeded"):
            await adapter._receive_frame(
                frame("large", 2, 1, b"a" * (11 * 1024 * 1024)), socket, unused
            )
        assert adapter._fragments == {}
