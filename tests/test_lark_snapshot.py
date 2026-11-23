"""Lark context is bounded, chronological source data and never changes intake."""

import asyncio
import json
import time

import httpx

from a13n_claw.service_app.feishu import InboundMessage, LarkAdapter, LarkSettings, snapshot_text


def test_snapshot_deduplicates_orders_and_excludes_current_and_future():
    def item(identity, stamp, sender, content):
        return {
            "message_id": identity,
            "create_time": str(stamp),
            "msg_type": "text",
            "sender": {"id": sender},
            "body": {"content": json.dumps({"text": content})},
        }

    parent = item("parent", 1000, "bot", "Earlier")
    thread = item("thread", 2000, "user", "Middle")
    current = item("current", 3000, "user", "Current")
    future = item("future", 4000, "user", "Future")

    def handle(request):
        if request.url.path.endswith("/parent"):
            data = [parent]
        elif request.url.params.get("container_id_type") == "thread":
            data = [thread, parent, current, future]
        else:
            assert request.url.params["end_time"] == "3"
            data = [thread, parent]
        return httpx.Response(200, json={"code": 0, "data": {"items": data}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            adapter = LarkAdapter(
                "connector",
                LarkSettings(app_id="app", bot_open_id="bot", senders=("user",)),
                "synthetic-secret",
                client=client,
            )
            adapter._token, adapter._token_until = "synthetic-token", time.monotonic() + 100
            message = InboundMessage(
                platform="lark",
                connection="connector",
                event_id="event",
                message_id="current",
                chat_id="chat",
                topic_id="topic",
                sender_id="user",
                reply_to="parent",
                create_time="3000",
                is_group=True,
                mentioned=True,
                text="Current",
                attachments=(),
                unavailable=(),
            )
            rows = await adapter.snapshot(message)
            assert [(row["message_id"], row["relation"], row["speaker"]) for row in rows] == [
                ("parent", "parent", "self"),
                ("thread", "thread", "external_user"),
            ]
            assert [row["content"] for row in rows] == ["Earlier", "Middle"]

    asyncio.run(run())


def test_snapshot_flattens_post_and_attachment_placeholders():
    content = {"title": "Title", "content": [[{"tag": "text", "text": "hello"}, {"tag": "img"}]]}
    assert (
        snapshot_text({"msg_type": "post", "body": {"content": json.dumps(content)}})
        == "Title hello [img]"
    )
    assert snapshot_text({"msg_type": "file", "body": {"content": "{}"}}) == "[file]"
