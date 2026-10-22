"""Ingress policy and business operations under the application ownership boundary."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from typing import TYPE_CHECKING, Any

from a13n_claw.async_utils import database
from a13n_claw.configuration.models import ChannelBinding, GitHubConnection, connection_identity
from a13n_claw.encoding import encode
from a13n_claw.messaging import controls
from a13n_claw.messaging.transport import InboundMessage

if TYPE_CHECKING:
    from a13n_claw.runtime.service import ClawService


class Ingress:
    def __init__(self, service: ClawService):
        self.service = service

    def binding(self, message: InboundMessage) -> tuple[str, ChannelBinding] | None:
        root = None
        for key, binding in self.service.settings.channels.items():
            if (binding.connection, binding.chat_id) != (message.connection, message.chat_id):
                continue
            # An explicit topic binding, including a disabled one, always wins.
            if binding.topic_id == message.topic_id:
                return key, binding
            if message.platform == "lark" and binding.topic_id is None:
                root = key, binding
        return root

    def authorize(self, message: InboundMessage, channel_id: str) -> ChannelBinding:
        binding = self.service.settings.channels.get(channel_id)
        connection = self.service.settings.connections.get(message.connection)
        if connection is None or not connection.enabled or binding is None or not binding.enabled:
            raise ValueError("Connection or Channel is disabled or unavailable")
        matched = self.binding(message)
        if matched is None or matched[0] != channel_id or message.platform != connection.platform:
            raise ValueError("Retained source no longer matches the Channel binding")
        if isinstance(connection, GitHubConnection) and (
            message.account_id != connection.account_login.casefold()
        ):
            raise ValueError("Retained GitHub account identity changed")
        allowed = (
            message.sender_id.casefold() in {s.casefold() for s in binding.allowed_senders}
            if message.platform == "github"
            else message.sender_id in binding.allowed_senders
        )
        if not allowed:
            raise ValueError("Sender is not authorized")
        return binding

    async def receive(self, message: InboundMessage) -> None:
        """Return only after business acceptance; no model work delays transport acknowledgement."""
        async with self.service.intake_lock:
            if self.service.stopping:
                raise ValueError("Claw is shutting down")
            fingerprint = hashlib.sha256(
                encode(message.model_dump(mode="json")).encode()
            ).hexdigest()
            previous = await database(
                self.service.store.rows,
                "SELECT * FROM inbox WHERE connection=? AND event_id=?",
                (message.connection, message.event_id),
            )
            if previous:
                if previous[0]["fingerprint"] != fingerprint:
                    raise ValueError("Conflicting reuse of platform event identity")
                return
            matched = self.binding(message)
            channel_id, binding = matched if matched else (None, None)
            reason = "Unbound Channel"
            eligible = False
            if binding is not None and channel_id is not None:
                try:
                    self.authorize(message, channel_id)
                    mentions_only = binding.mentions_only
                    if mentions_only is None:
                        mentions_only = self.service.settings.connections[
                            message.connection
                        ].mentions_only
                    eligible = controls.is_control(message.text) or not (
                        message.is_group and mentions_only and not message.mentioned
                    )
                    reason = "" if eligible else "A bot mention is required"
                    if (
                        controls.is_control(message.text)
                        and message.sender_id not in binding.operators
                    ):
                        eligible, reason = False, "Channel operator grant required"
                except ValueError as exc:
                    reason = str(exc)
            body = None
            if eligible:
                body = {
                    "message": message.model_dump(mode="json"),
                    "connection_identity": connection_identity(
                        self.service.settings.connections[message.connection]
                    ),
                    "files": [],
                    "unavailable": list(message.unavailable),
                    "attachments_pending": bool(message.attachments)
                    and not controls.is_control(message.text),
                }
                if controls.is_control(message.text):
                    body["control"] = message.text.removeprefix("/claw").strip()
            item = await database(
                self.service.store.accept,
                connection=message.connection,
                event_id=message.event_id,
                fingerprint=fingerprint,
                channel_id=channel_id,
                mode=self.service.settings.mode,
                project_id=(
                    self.service.settings.main_project_id
                    if self.service.settings.mode == "one_thread"
                    else binding.project_id
                    if binding
                    else None
                ),
                body=body,
                reason=reason,
            )
            if eligible:
                await self.service.feedback.status(item, "retained")
            self.service.wake.set()

    async def collect_attachments(self, item: dict[str, Any]) -> None:
        """Collect after ingress commit/ACK, before dispatch; downloads have no send effect."""
        message, body = self.source(item)
        if not body.get("attachments_pending"):
            return
        deadline = time.monotonic() + 15
        total = 0
        for attachment in message.attachments[:8]:
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                async with asyncio.timeout(remaining):
                    data = await self.service.adapters[message.connection].download(
                        message.message_id, attachment
                    )
                if len(data) > 10 * 1024 * 1024 or total + len(data) > 20 * 1024 * 1024:
                    raise ValueError("Attachment budget exceeded")
                total += len(data)
                body["files"].append(
                    {"name": attachment.name, "data": base64.b64encode(data).decode()}
                )
            except Exception:
                body["unavailable"].append(f"Attachment unavailable: {attachment.name}")
        if len(message.attachments) > 8:
            body["unavailable"].append("Only the first eight attachments can be retained")
        body["attachments_pending"] = False
        await database(self.service.store.update_body, item["id"], body)
        item["body"] = encode(body)

    def source(self, item: dict[str, Any]) -> tuple[InboundMessage, dict[str, Any]]:
        if item["mode"] != self.service.settings.mode:
            raise ValueError("Retained work belongs to another mode")
        if item["body"] is None:
            raise ValueError("Inbox content was filtered or pruned; submit a new message")
        body = json.loads(item["body"])
        if "operator_attention" in body:
            raise ValueError("Operator attention has no external messaging destination")
        message = InboundMessage.model_validate(body["message"])
        self.authorize(message, item["channel_id"])
        if "connection_identity" in body and body["connection_identity"] != connection_identity(
            self.service.settings.connections[message.connection]
        ):
            raise ValueError("Retained connection identity changed")
        return message, body
