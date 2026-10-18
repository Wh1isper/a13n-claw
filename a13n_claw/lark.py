"""Feishu/Lark wire adapter. Async transport keeps SDK-global loops out of the Host.

The published SDK supplies the protobuf frame. Claw owns only this connection's
async lifecycle and acknowledges events after the product handler has committed.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import time
from contextlib import suppress
from urllib.parse import parse_qs, quote, urlparse

import httpx
from lark_oapi.ws.pb.pbbp2_pb2 import Frame
from websockets.asyncio.client import ClientConnection, connect

from a13n_claw.settings import LarkConnection
from a13n_claw.transport import (
    AttachmentRef,
    Destination,
    InboundMessage,
    MessageHandler,
    OutboundMessage,
    SendResult,
)

logger = logging.getLogger("a13n_claw.lark")


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
    elif kind in {"image", "file"}:
        key = content["image_key" if kind == "image" else "file_key"]
        attachments.append(
            AttachmentRef(key=key, name=content.get("file_name", "image.png"), kind=kind)
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
        settings: LarkConnection,
        *,
        client: httpx.AsyncClient | None = None,
    ):
        self.connection_id = connection_id
        self.settings = settings
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

    def _secret(self) -> str:
        secret = os.environ.get(self.settings.app_secret_env)
        if not secret:
            raise ValueError("Configured Lark app secret environment variable is missing")
        return secret

    async def _access_token(self) -> str:
        async with self._token_lock:
            if self._token is not None and time.monotonic() < self._token_until:
                return self._token
            response = await self.client.post(
                self.settings.domain + "/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": self.settings.app_id, "app_secret": self._secret()},
            )
            response.raise_for_status()
            data = response.json()
            if data.get("code") != 0:
                raise ValueError("Lark authentication rejected; check app credentials")
            token = data["tenant_access_token"]
            if not isinstance(token, str) or not token:
                raise ValueError("Lark authentication omitted the access token")
            self._token = token
            self._token_until = time.monotonic() + max(0, data.get("expire", 0) - 60)
            return token

    async def send(
        self,
        destination: Destination,
        message: OutboundMessage,
        *,
        request_id: str,
    ) -> SendResult:
        # Authentication has no message effect. No send retry follows an uncertain response.
        try:
            token = await self._access_token()
        except Exception:
            return SendResult("not_sent", "Lark authentication unavailable")
        body = {
            "msg_type": "text",
            "content": json.dumps({"text": message.text}, ensure_ascii=False),
            "uuid": hashlib.sha256(request_id.encode()).hexdigest()[:32],
        }
        if message.file is not None:
            # Uploading creates a resource, not a message. A failed upload cannot have
            # delivered a reply, and the durable delivery remains the retry boundary.
            try:
                uploaded = await self.client.post(
                    self.settings.domain + "/open-apis/im/v1/files",
                    data={"file_type": "stream", "file_name": message.file.name},
                    files={"file": (message.file.name, base64.b64decode(message.file.data))},
                    headers={"Authorization": f"Bearer {token}"},
                )
                uploaded.raise_for_status()
                payload = uploaded.json()
                key = payload.get("data", {}).get("file_key")
                if payload.get("code") != 0 or not isinstance(key, str) or not key:
                    return SendResult("not_sent", "Lark file upload rejected")
                body["msg_type"] = "file"
                body["content"] = json.dumps({"file_key": key})
            except Exception:
                return SendResult("not_sent", "File upload unavailable; no message submitted")
        if destination.reply_to:
            path = f"/open-apis/im/v1/messages/{quote(destination.reply_to, safe='')}/reply"
            params = None
        else:
            path = "/open-apis/im/v1/messages"
            body["receive_id"] = destination.chat_id
            params = {"receive_id_type": "chat_id"}
        try:
            response = await self.client.post(
                self.settings.domain + path,
                json=body,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            if response.status_code >= 500:
                return SendResult("unknown", "Lark returned a server error after submission")
            data = response.json()
            if response.is_success and data.get("code") == 0:
                message_id = data.get("data", {}).get("message_id")
                if isinstance(message_id, str) and message_id:
                    return SendResult("sent", message_id)
                return SendResult("unknown", "Successful response omitted message identity")
            return SendResult("not_sent", f"Lark rejected message with code {data.get('code')}")
        except Exception:
            return SendResult("unknown", "Lark send response unavailable; reconcile before retry")

    async def download(self, message_id: str, attachment: AttachmentRef) -> bytes:
        token = await self._access_token()
        path = (
            f"/open-apis/im/v1/messages/{quote(message_id, safe='')}/resources/"
            f"{quote(attachment.key, safe='')}"
        )
        async with self.client.stream(
            "GET",
            self.settings.domain + path,
            params={"type": attachment.kind},
            headers={"Authorization": f"Bearer {token}"},
        ) as response:
            response.raise_for_status()
            result = bytearray()
            async for chunk in response.aiter_bytes():
                result.extend(chunk)
                if len(result) > 10 * 1024 * 1024:
                    raise ValueError("Attachment exceeds the Host 10 MiB limit")
            return bytes(result)

    def _configure(self, values: dict) -> None:
        self._ping_seconds = max(1, float(values.get("PingInterval", self._ping_seconds)))
        self._retry_seconds = max(1, float(values.get("ReconnectInterval", self._retry_seconds)))

    async def run(self, receive: MessageHandler) -> None:
        self._secret()
        while not self._closed.is_set():
            try:
                response = await self.client.post(
                    self.settings.domain + "/callback/ws/endpoint",
                    json={"AppID": self.settings.app_id, "AppSecret": self._secret()},
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
                    self.status = "connected"
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
                logger.warning("Lark connection %s interrupted; reconnecting", self.connection_id)
            finally:
                self._socket = None
                self.status = "disconnected"
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
            logger.warning("Lark event was not retained for %s", self.connection_id)
        frame.payload = json.dumps({"code": code}).encode()
        await socket.send(frame.SerializeToString())

    async def close(self) -> None:
        self._closed.set()
        if self._socket is not None:
            await self._socket.close()
        if self._owns_client:
            await self.client.aclose()
