"""Platform-neutral messages and the small transport boundary used by product services."""

from __future__ import annotations

import base64
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from pydantic import Field, model_validator

from a13n_claw.settings import ProductModel


class AttachmentRef(ProductModel):
    key: str
    name: str
    kind: Literal["image", "file"]


class InboundMessage(ProductModel):
    platform: str
    connection: str
    event_id: str
    message_id: str
    chat_id: str
    topic_id: str | None = None
    sender_id: str
    account_id: str | None = Field(default=None, exclude_if=lambda value: value is None)
    is_group: bool = False
    mentioned: bool = False
    text: str = Field(default="", max_length=100_000)
    attachments: tuple[AttachmentRef, ...] = ()
    unavailable: tuple[str, ...] = ()


class Destination(ProductModel):
    chat_id: str
    reply_to: str | None = None


class OutboundFile(ProductModel):
    name: str = Field(min_length=1, max_length=255)
    data: str = Field(max_length=14_000_000)

    @model_validator(mode="after")
    def bounded_file(self) -> OutboundFile:
        if len(base64.b64decode(self.data, validate=True)) > 10 * 1024 * 1024:
            raise ValueError("Files are limited to 10 MiB")
        return self


class OutboundMessage(ProductModel):
    text: str = Field(default="", max_length=20_000)
    file: OutboundFile | None = None
    reaction: str | None = Field(
        default=None, min_length=1, max_length=64, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def one_content(self) -> OutboundMessage:
        if sum((bool(self.text), self.file is not None, self.reaction is not None)) != 1:
            raise ValueError("Send text, one file, or one reaction")
        return self


@dataclass(frozen=True)
class SendResult:
    status: Literal["sent", "not_sent", "unknown"]
    evidence: str


type MessageHandler = Callable[[InboundMessage], Awaitable[None]]


class MessagingAdapter(Protocol):
    """Adapters own connection and wire formats, never routing, Inbox, or execution."""

    @property
    def status(self) -> str: ...

    async def run(self, receive: MessageHandler) -> None: ...

    async def send(
        self,
        destination: Destination,
        message: OutboundMessage,
        *,
        request_id: str,
    ) -> SendResult: ...

    async def download(self, message_id: str, attachment: AttachmentRef) -> bytes: ...

    async def close(self) -> None: ...
