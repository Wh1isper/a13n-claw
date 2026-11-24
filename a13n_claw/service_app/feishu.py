"""Feishu event-only transport. Platform replies run in the Project CLI."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from hashlib import sha256
from typing import Literal
from urllib.parse import parse_qs, quote, urlparse

import httpx
from a13n_logging import get_logger
from lark_oapi.ws.pb.pbbp2_pb2 import Frame
from pydantic import BaseModel, ConfigDict, Field
from websockets.asyncio.client import ClientConnection, connect

logger = get_logger(__name__)


class LarkSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    app_id: str = Field(min_length=1, max_length=200)
    bot_open_id: str = Field(min_length=1, max_length=200)
    domain: Literal["https://open.feishu.cn", "https://open.larksuite.com"] = (
        "https://open.feishu.cn"
    )
    senders: tuple[str, ...] = Field(min_length=1, max_length=500)
    groups: tuple[str, ...] = Field(default=(), max_length=500)
    unmentioned_groups: tuple[str, ...] = Field(default=(), max_length=500)
    direct_messages: bool = True
    event_types: tuple[
        Literal[
            "im.message.receive_v1",
            "im.chat.member.bot.added_v1",
            "im.chat.member.user.added_v1",
            "drive.notice.comment_add_v1",
        ],
        ...,
    ] = (
        "im.message.receive_v1",
        "im.chat.member.bot.added_v1",
        "im.chat.member.user.added_v1",
        "drive.notice.comment_add_v1",
    )


class AttachmentRef(BaseModel):
    key: str
    name: str
    kind: Literal["image", "file"]


class InboundMessage(BaseModel):
    platform: Literal["lark"]
    connection: str
    event_id: str
    message_id: str
    chat_id: str
    topic_id: str | None
    sender_id: str
    reply_to: str | None
    root_id: str | None = None
    create_time: str | None = None
    previous_messages_snapshot: tuple[dict[str, str], ...] = ()
    is_group: bool
    mentioned: bool
    text: str
    attachments: tuple[AttachmentRef, ...]
    unavailable: tuple[str, ...]


class InboundEvent(BaseModel):
    platform: Literal["lark"] = "lark"
    connection: str
    event_id: str
    event_type: str
    chat_id: str
    sender_id: str
    payload: dict


type MessageHandler = Callable[[InboundMessage | InboundEvent], Awaitable[None]]
type StatusHandler = Callable[[str], Awaitable[None]]
type CardHandler = Callable[[str, str, str, str, str], Awaitable[bool]]


def reply_key(event_id: str) -> str:
    key = f"claw-lark-{event_id}"
    return key if len(key) <= 50 else f"claw-lark-{sha256(event_id.encode()).hexdigest()[:40]}"


def normalize_message(
    payload: dict,
    *,
    connection: str,
    app_id: str,
    bot_open_id: str,
) -> InboundMessage | None:
    header = payload.get("header", {})
    if header.get("event_type") != "im.message.receive_v1":
        return None
    if header.get("app_id") != app_id:
        raise ValueError("Lark event belongs to another application")
    event = payload["event"]
    sender, message = event["sender"], event["message"]
    if sender.get("sender_type") != "user":
        return None
    sender_id = sender["sender_id"]["open_id"]
    if sender_id == bot_open_id:
        return None
    content = json.loads(message["content"])
    kind = message["message_type"]
    attachments: list[AttachmentRef] = []
    unavailable: list[str] = []
    text = ""
    if kind == "text":
        text = content["text"]
    elif kind in {"image", "file", "audio", "media"}:
        key = content["image_key" if kind == "image" else "file_key"]
        names = {
            "image": "image.png",
            "file": "attachment.bin",
            "audio": "audio.ogg",
            "media": "video.mp4",
        }
        attachments.append(
            AttachmentRef(
                key=key,
                name=content.get("file_name", names[kind]),
                kind="image" if kind == "image" else "file",
            )
        )
    elif kind == "post":
        post = content
        if "content" not in post:
            post = next(
                (
                    value
                    for value in content.values()
                    if isinstance(value, dict) and "content" in value
                ),
                {},
            )
        parts = [post.get("title", "")]
        for row in post.get("content", []):
            for item in row:
                if item.get("tag") in {"text", "a", "at"}:
                    parts.append(item.get("text", item.get("user_name", "")))
                    if item.get("tag") == "a" and item.get("href"):
                        parts.append(item["href"])
                elif item.get("tag") == "img":
                    attachments.append(
                        AttachmentRef(key=item["image_key"], name="image.png", kind="image")
                    )
                else:
                    unavailable.append(str(item.get("tag", "unknown")))
        text = "\n".join(parts)
    else:
        unavailable.append(kind)
    mentions = message.get("mentions", [])
    mentioned = any(item.get("id", {}).get("open_id") == bot_open_id for item in mentions)
    for item in mentions:
        if item.get("id", {}).get("open_id") == bot_open_id:
            text = text.replace(item.get("key", "\0"), "")
    return InboundMessage(
        platform="lark",
        connection=connection,
        event_id=header["event_id"],
        message_id=message["message_id"],
        chat_id=message["chat_id"],
        topic_id=message.get("thread_id"),
        reply_to=message.get("parent_id") or None,
        root_id=message.get("root_id") or None,
        create_time=message.get("create_time"),
        sender_id=sender_id,
        is_group=message.get("chat_type") == "group",
        mentioned=mentioned,
        text=text.strip(),
        attachments=tuple(attachments),
        unavailable=tuple(unavailable),
    )


def normalize_event(payload: dict, *, connection: str, app_id: str) -> InboundEvent | None:
    header = payload.get("header", {})
    kind = header.get("event_type")
    if kind not in {
        "im.chat.member.bot.added_v1",
        "im.chat.member.user.added_v1",
        "drive.notice.comment_add_v1",
    }:
        return None
    if header.get("app_id") != app_id:
        raise ValueError("Lark event belongs to another application")
    event = payload["event"]
    operator = event.get("operator_id") or {}
    chat = event.get("chat_id")
    if kind == "drive.notice.comment_add_v1":
        notice = event.get("notice_meta") or {}
        if not isinstance(notice, dict):
            return None
        operator = notice.get("from_user_id") or {}
        token = notice.get("file_token")
        chat = f"drive/{token}" if isinstance(token, str) and token else None
    sender = operator.get("open_id") if isinstance(operator, dict) else None
    if not isinstance(sender, str) or not sender or not isinstance(chat, str) or not chat:
        return None
    event_id = header.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        return None
    return InboundEvent(
        connection=connection,
        event_id=event_id,
        event_type=kind,
        chat_id=chat,
        sender_id=sender,
        payload=event,
    )


def snapshot_text(item: dict) -> str:
    kind = item.get("msg_type") or item.get("message_type") or "unknown"
    body = item.get("body") or {}
    raw = body.get("content", "{}") if isinstance(body, dict) else "{}"
    try:
        content = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return f"[{kind}]"
    if not isinstance(content, dict):
        return f"[{kind}]"
    if kind == "text":
        return str(content.get("text", ""))[:2000]
    if kind == "post":
        post = (
            content
            if "content" in content
            else next(
                (
                    value
                    for value in content.values()
                    if isinstance(value, dict) and "content" in value
                ),
                {},
            )
        )
        parts = [str(post.get("title", ""))]
        for row in post.get("content", []):
            for part in row:
                if isinstance(part, dict):
                    tag = part.get("tag", "unknown")
                    parts.append(str(part.get("text") or part.get("user_name") or f"[{tag}]"))
        return " ".join(parts)[:2000]
    return f"[{kind}]"


class LarkAdapter:
    def __init__(
        self,
        connection_id: str,
        settings: LarkSettings,
        secret: str,
        *,
        client: httpx.AsyncClient | None = None,
        on_status: StatusHandler | None = None,
        on_card: CardHandler | None = None,
    ):
        self.connection_id = connection_id
        self.settings = settings
        self.secret = secret
        self.client = client or httpx.AsyncClient(timeout=20)
        self._owns_client = client is None
        self._closed = asyncio.Event()
        self._socket: ClientConnection | None = None
        self._token: str | None = None
        self._token_until = 0.0
        self._token_lock = asyncio.Lock()
        self._ping_seconds = 120.0
        self._retry_seconds = 30.0
        self._fragments: dict[str, tuple[float, list[bytes | None]]] = {}
        self.status = "disconnected"
        self._on_status = on_status
        self._on_card = on_card

    async def _transition(self, status: str) -> None:
        if self.status != status:
            self.status = status
            if self._on_status is not None:
                await self._on_status(status)

    async def exchange_token(self) -> tuple[str, int]:
        """Exchange the App secret on Control and return the token and provider lifetime."""
        response = await self.client.post(
            self.settings.domain + "/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self.settings.app_id, "app_secret": self.secret},
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or data.get("code") != 0:
            raise ValueError("Lark authentication rejected; check app credentials")
        token, lifetime = data.get("tenant_access_token"), data.get("expire")
        if not isinstance(token, str) or not token:
            raise ValueError("Lark authentication omitted the access token")
        if not isinstance(lifetime, int) or isinstance(lifetime, bool) or lifetime <= 0:
            raise ValueError("Lark authentication omitted the token lifetime")
        return token, lifetime

    async def access_token(self) -> str:
        async with self._token_lock:
            if self._token is not None and time.monotonic() < self._token_until:
                return self._token
            token, lifetime = await self.exchange_token()
            self._token = token
            self._token_until = time.monotonic() + max(0, lifetime - 60)
            return token

    async def snapshot(self, message: InboundMessage) -> tuple[dict[str, str], ...]:
        """Read a small, untrusted context window; callers bound time and handle failures."""
        try:
            cutoff = int(message.create_time or "")
        except ValueError:
            return ()
        token = await self.access_token()
        headers = {"Authorization": f"Bearer {token}"}

        async def fetch(path: str, params: dict | None = None) -> list[dict]:
            try:
                response = await self.client.get(
                    self.settings.domain + path, headers=headers, params=params
                )
                response.raise_for_status()
                body = response.json()
                if body.get("code") != 0:
                    return []
                return [
                    item
                    for item in body.get("data", {}).get("items", [])[:10]
                    if isinstance(item, dict)
                ]
            except (httpx.HTTPError, TypeError, ValueError, AttributeError):
                return []

        requests = []
        relations = []
        if parent := message.reply_to or message.root_id:
            requests.append(fetch(f"/open-apis/im/v1/messages/{quote(parent, safe='')}"))
            relations.append("parent")
        if message.topic_id:
            requests.append(
                fetch(
                    "/open-apis/im/v1/messages",
                    {
                        "container_id_type": "thread",
                        "container_id": message.topic_id,
                        "page_size": 10,
                        "sort_type": "ByCreateTimeDesc",
                    },
                )
            )
            relations.append("thread")
        requests.append(
            fetch(
                "/open-apis/im/v1/messages",
                {
                    "container_id_type": "chat",
                    "container_id": message.chat_id,
                    "page_size": 10,
                    "sort_type": "ByCreateTimeDesc",
                    "end_time": str(cutoff // 1000),
                },
            )
        )
        relations.append("chat_recent")
        records: dict[str, tuple[int, dict[str, str]]] = {}
        for relation, items in zip(relations, await asyncio.gather(*requests), strict=True):
            for item in items:
                identity = item.get("message_id")
                try:
                    timestamp = int(item.get("create_time", ""))
                except (TypeError, ValueError):
                    continue
                if (
                    not isinstance(identity, str)
                    or identity == message.message_id
                    or timestamp > cutoff
                ):
                    continue
                sender = item.get("sender") or {}
                sender_id = sender.get("id") if isinstance(sender, dict) else None
                if isinstance(sender_id, dict):
                    sender_id = sender_id.get("open_id")
                records.setdefault(
                    identity,
                    (
                        timestamp,
                        {
                            "message_id": identity,
                            "create_time": str(timestamp),
                            "speaker": "self"
                            if sender_id == self.settings.bot_open_id
                            else "external_user",
                            "relation": relation,
                            "content": snapshot_text(item),
                        },
                    ),
                )
        ordered = sorted(records.values(), key=lambda value: (value[0], value[1]["message_id"]))
        return tuple(record for _, record in ordered)

    async def react(self, message_id: str, emoji: str) -> None:
        token = await self.access_token()
        path = f"/open-apis/im/v1/messages/{quote(message_id, safe='')}/reactions"
        response = await self.client.post(
            self.settings.domain + path,
            json={"reaction_type": {"emoji_type": emoji}},
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        if response.json().get("code") != 0:
            raise ValueError("Feishu refused intake feedback")

    def _configure(self, values: dict) -> None:
        self._ping_seconds = max(1, float(values.get("PingInterval", self._ping_seconds)))
        self._retry_seconds = max(1, float(values.get("ReconnectInterval", self._retry_seconds)))

    async def run(self, receive: MessageHandler) -> None:
        while not self._closed.is_set():
            try:
                response = await self.client.post(
                    self.settings.domain + "/callback/ws/endpoint",
                    json={"AppID": self.settings.app_id, "AppSecret": self.secret},
                )
                response.raise_for_status()
                payload = response.json()
                if payload.get("code") != 0:
                    raise ValueError("Lark refused long-connection discovery")
                data = payload["data"]
                self._configure(data.get("ClientConfig") or {})
                url = data["URL"]
                service = int(parse_qs(urlparse(url).query)["service_id"][0])
                async with connect(url, max_size=20 * 1024 * 1024, proxy=None) as socket:
                    self._socket = socket
                    await self._transition("connected")
                    ping = asyncio.create_task(self._ping(socket, service))
                    try:
                        async for raw in socket:
                            if not isinstance(raw, bytes):
                                raise ValueError("Expected binary Lark frame")
                            await self._receive_frame(raw, socket, receive)
                    finally:
                        ping.cancel()
                        with suppress(asyncio.CancelledError):
                            await ping
            except asyncio.CancelledError:
                raise
            except Exception:
                # Exceptions can contain the token-bearing connection URL or message content.
                logger.warning(
                    "Feishu connection interrupted", extra={"connector_id": self.connection_id}
                )
            finally:
                self._socket = None
                await self._transition("disconnected")
                self._fragments.clear()
            if not self._closed.is_set():
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._closed.wait(), self._retry_seconds)

    async def _ping(self, socket: ClientConnection, service: int) -> None:
        while True:
            frame = Frame(SeqID=0, LogID=0, service=service, method=0)
            frame.headers.add(key="type", value="ping")
            await socket.send(frame.SerializeToString())
            await asyncio.sleep(self._ping_seconds)

    async def _receive_frame(
        self, raw: bytes, socket: ClientConnection, receive: MessageHandler
    ) -> None:
        frame = Frame()
        frame.ParseFromString(raw)
        headers = {item.key: item.value for item in frame.headers}
        if frame.method == 0:
            if headers.get("type") == "pong" and frame.payload:
                self._configure(json.loads(frame.payload))
            return
        payload = frame.payload
        count, sequence = int(headers.get("sum", "1")), int(headers.get("seq", "0"))
        if not 0 <= sequence < count <= 1024:
            raise ValueError("Invalid multipart Lark frame")
        if count > 1:
            now = time.monotonic()
            self._fragments = {
                key: value for key, value in self._fragments.items() if value[0] > now
            }
            if headers["message_id"] not in self._fragments and len(self._fragments) >= 64:
                raise ValueError("Too many incomplete Lark events")
            _, fragments = self._fragments.setdefault(
                headers["message_id"], (now + 10, [None] * count)
            )
            if len(fragments) != count:
                raise ValueError("Conflicting multipart frame")
            if fragments[sequence] is not None and fragments[sequence] != payload:
                raise ValueError("Conflicting multipart payload")
            fragments[sequence] = payload
            if (
                sum(
                    len(part)
                    for _, parts in self._fragments.values()
                    for part in parts
                    if part is not None
                )
                > 20 * 1024 * 1024
            ):
                self._fragments.clear()
                raise ValueError("Multipart Lark payload budget exceeded")
            if any(part is None for part in fragments):
                return
            payload = b"".join(part for part in fragments if part is not None)
            del self._fragments[headers["message_id"]]
        code = 200
        callback_data = None
        try:
            if headers.get("type") == "card":
                data = json.loads(payload)
                header, event = data.get("header", {}), data.get("event", {})
                action, context = event.get("action", {}), event.get("context", {})
                value, operator = action.get("value", {}), event.get("operator", {})
                fields = (
                    value.get("card_id"),
                    value.get("token"),
                    operator.get("open_id"),
                    context.get("open_chat_id"),
                    context.get("open_message_id"),
                )
                accepted = False
                if (
                    header.get("event_type") == "card.action.trigger"
                    and header.get("app_id") == self.settings.app_id
                    and self._on_card is not None
                    and all(isinstance(item, str) and 0 < len(item) <= 256 for item in fields)
                ):
                    try:
                        async with asyncio.timeout(2):
                            accepted = await self._on_card(*fields)
                    except TimeoutError:
                        pass
                callback_data = {
                    "toast": {
                        "type": "success" if accepted else "warning",
                        "content": "Retry submitted"
                        if accepted
                        else "Retry not confirmed. Check the Run.",
                    }
                }
            elif headers.get("type") == "event":
                data = json.loads(payload)
                kind = data.get("header", {}).get("event_type")
                if kind not in self.settings.event_types:
                    message = None
                elif kind == "im.message.receive_v1":
                    message = normalize_message(
                        data,
                        connection=self.connection_id,
                        app_id=self.settings.app_id,
                        bot_open_id=self.settings.bot_open_id,
                    )
                else:
                    message = normalize_event(
                        data, connection=self.connection_id, app_id=self.settings.app_id
                    )
                if message is not None:
                    await receive(message)
        except Exception:
            code = 500
            logger.warning(
                "Feishu event was not retained", extra={"connector_id": self.connection_id}
            )
        response: dict[str, int | str] = {"code": code}
        if callback_data is not None:
            response["data"] = base64.b64encode(json.dumps(callback_data).encode()).decode()
        frame.payload = json.dumps(response).encode()
        await socket.send(frame.SerializeToString())

    async def close(self) -> None:
        self._closed.set()
        if self._socket is not None:
            await self._socket.close()
        if self._owns_client:
            await self.client.aclose()
