"""Feishu event-only transport. Platform replies run in the Project CLI."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
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
    is_group: bool
    mentioned: bool
    text: str
    attachments: tuple[AttachmentRef, ...]
    unavailable: tuple[str, ...]


type MessageHandler = Callable[[InboundMessage], Awaitable[None]]
type StatusHandler = Callable[[str], Awaitable[None]]


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
        sender_id=sender_id,
        is_group=message.get("chat_type") == "group",
        mentioned=mentioned,
        text=text.strip(),
        attachments=tuple(attachments),
        unavailable=tuple(unavailable),
    )


class LarkAdapter:
    def __init__(
        self,
        connection_id: str,
        settings: LarkSettings,
        secret: str,
        *,
        client: httpx.AsyncClient | None = None,
        on_status: StatusHandler | None = None,
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

    async def _transition(self, status: str) -> None:
        if self.status != status:
            self.status = status
            if self._on_status is not None:
                await self._on_status(status)

    async def access_token(self) -> str:
        async with self._token_lock:
            if self._token is not None and time.monotonic() < self._token_until:
                return self._token
            response = await self.client.post(
                self.settings.domain + "/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": self.settings.app_id, "app_secret": self.secret},
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or data.get("code") != 0:
                raise ValueError("Lark authentication rejected; check app credentials")
            token = data.get("tenant_access_token")
            if not isinstance(token, str) or not token:
                raise ValueError("Lark authentication omitted the access token")
            self._token = token
            self._token_until = time.monotonic() + max(0, data.get("expire", 0) - 60)
            return token

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
        try:
            if headers.get("type") == "event":
                message = normalize_message(
                    json.loads(payload),
                    connection=self.connection_id,
                    app_id=self.settings.app_id,
                    bot_open_id=self.settings.bot_open_id,
                )
                if message is not None:
                    await receive(message)
        except Exception:
            code = 500
            logger.warning(
                "Feishu event was not retained", extra={"connector_id": self.connection_id}
            )
        frame.payload = json.dumps({"code": code}).encode()
        await socket.send(frame.SerializeToString())

    async def close(self) -> None:
        self._closed.set()
        if self._socket is not None:
            await self._socket.close()
        if self._owns_client:
            await self.client.aclose()
