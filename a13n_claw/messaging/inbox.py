"""Inbox policy and business operations under the application ownership boundary."""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING, Any, Literal

from a13n_claw.async_utils import database
from a13n_claw.messaging.transport import Destination, OutboundMessage
from a13n_claw.runtime.observations import exact_operation_status

if TYPE_CHECKING:
    from a13n_claw.runtime.service import ClawService


class Inbox:
    def __init__(self, service: ClawService):
        self.service = service

    async def authorized_body(self, item: dict[str, Any]) -> dict[str, Any]:
        body = json.loads(item["body"]) if item["body"] else {}
        if "operator_attention" not in body:
            return self.service.ingress.source(item)[1]
        instance = await database(self.service.store.instance)
        worker = await self.service.host.get_thread(body["operator_attention"]["worker_thread_id"])
        if (
            self.service.settings.mode != "one_thread"
            or not instance["main_thread_id"]
            or worker.thread.coordinator_thread_id != instance["main_thread_id"]
            or worker.thread.configuration.project_id != item["project_id"]
        ):
            raise ValueError("Worker attention no longer belongs to canonical Main")
        return body

    async def authorize_caller(self, thread_id: str, run_id: str) -> str:
        detail = await self.service.host.get_thread(thread_id)
        if detail.thread.parent_thread_id is not None:
            raise ValueError("Subagents have no Claw messaging authority")
        if self.service.settings.mode == "one_thread":
            instance = await database(self.service.store.instance)
            if thread_id != instance["main_thread_id"] or detail.thread.role != "coordinator":
                raise ValueError("Only canonical Main has Claw messaging authority")
        operation = await self.service.host.active_root_operation(thread_id)
        if operation is None or operation.run_id != run_id:
            raise ValueError("Caller no longer owns the active Host execution")
        return operation.receipt.receipt_id

    async def readable(self, thread_id: str, receipt_id: str, item_id: str) -> dict[str, Any]:
        row = await database(self.service.store.readable, item_id, receipt_id)
        if row["thread_id"] != thread_id or row["mode"] != self.service.settings.mode:
            raise ValueError("Inbox item is outside the caller's routed context")
        detail = await self.service.host.get_thread(thread_id)
        if detail.thread.configuration.project_id != row["project_id"]:
            raise ValueError("Thread Project no longer matches the retained Inbox context")
        body = await self.authorized_body(row)
        if "control" in body:
            raise ValueError("IM controls are handled by the Host boundary, not Agent tools")
        return row

    async def read(self, thread_id: str, run_id: str, item_id: str) -> dict[str, Any]:
        async with self.service.lock:
            receipt = await self.authorize_caller(thread_id, run_id)
            row = await self.readable(thread_id, receipt, item_id)
            body = await self.authorized_body(row)
            return {
                **{
                    key: row[key]
                    for key in ("id", "channel_id", "version", "disposition", "reason")
                },
                **(
                    {"operator_attention": body["operator_attention"]}
                    if "operator_attention" in body
                    else {"message": body["message"]}
                ),
                "unavailable": body["unavailable"],
                "files": [{"index": i, "name": f["name"]} for i, f in enumerate(body["files"])],
            }

    async def context(self, thread_id: str, run_id: str, item_id: str) -> dict[str, Any]:
        """Read bounded retained Lark context without expanding ordinary Inbox authority."""
        async with self.service.lock:
            receipt = await self.authorize_caller(thread_id, run_id)
            anchor = await self.readable(thread_id, receipt, item_id)
            message, _ = self.service.ingress.source(anchor)
            if message.platform != "lark":
                raise ValueError("Retained conversation context is available for Lark only")
            rows = await database(
                self.service.store.rows,
                "SELECT * FROM inbox WHERE thread_id=? AND channel_id=? AND project_id=? "
                "AND mode=? AND (created,id)<(?,?) AND body IS NOT NULL "
                "AND json_extract(body,'$.control') IS NULL "
                "AND (json_extract(body,'$.message.topic_id') IS ? "
                "OR json_extract(body,'$.message.message_id')=?) "
                "ORDER BY (json_extract(body,'$.message.message_id')=?) DESC,created DESC,id DESC "
                "LIMIT 20",
                (
                    thread_id,
                    anchor["channel_id"],
                    anchor["project_id"],
                    anchor["mode"],
                    anchor["created"],
                    anchor["id"],
                    message.topic_id,
                    message.reply_to,
                    message.reply_to,
                ),
            )
            result = []
            budget = 10_000
            for candidate in rows:
                try:
                    row = await self.readable(thread_id, receipt, candidate["id"])
                    prior, _ = self.service.ingress.source(row)
                except ValueError:
                    continue
                if (prior.platform, prior.connection, prior.chat_id) != (
                    message.platform,
                    message.connection,
                    message.chat_id,
                ):
                    continue
                text = prior.text[: min(2000, budget)]
                result.append(
                    {
                        "id": row["id"],
                        "message_id": prior.message_id,
                        "sender_id": prior.sender_id,
                        "text": text,
                        "truncated": len(text) < len(prior.text),
                        "is_reply_target": prior.message_id == message.reply_to,
                    }
                )
                budget -= len(text)
                if len(result) == 10 or budget == 0:
                    break
            return {
                "items": result,
                "reply_available": any(value["is_reply_target"] for value in result),
                "scope": "Retained readable messages in this Thread and Channel only; "
                "reply target first, then recent messages. No platform history is fetched.",
            }

    async def file(
        self, thread_id: str, run_id: str, item_id: str, index: int
    ) -> tuple[str, bytes]:
        async with self.service.lock:
            receipt = await self.authorize_caller(thread_id, run_id)
            row = await self.readable(thread_id, receipt, item_id)
            _, body = self.service.ingress.source(row)
            if not 0 <= index < len(body["files"]):
                raise ValueError("Attachment index is unavailable")
            file = body["files"][index]
            return file["name"], base64.b64decode(file["data"], validate=True)

    async def list(self, thread_id: str, run_id: str) -> list[dict[str, Any]]:
        async with self.service.lock:
            receipt = await self.authorize_caller(thread_id, run_id)
            result = []
            cursor: tuple[float, str] = (0, "")
            while len(result) < 50:
                rows = await database(
                    self.service.store.rows,
                    "SELECT * FROM inbox WHERE thread_id=? AND disposition='pending' "
                    "AND (created,id)>(?,?) AND NOT EXISTS (SELECT 1 FROM deliveries d "
                    "WHERE d.item_id=inbox.id AND d.status IN ('sending','unknown') "
                    "AND NOT EXISTS (SELECT 1 FROM feedback_deliveries f "
                    "WHERE f.delivery_id=d.id)) "
                    "ORDER BY created,id LIMIT 50",
                    (thread_id, *cursor),
                )
                if not rows:
                    break
                for row in rows:
                    cursor = (row["created"], row["id"])
                    try:
                        await self.readable(thread_id, receipt, row["id"])
                    except ValueError:
                        continue
                    result.append(
                        {key: row[key] for key in ("id", "channel_id", "version", "disposition")}
                    )
                    if len(result) == 50:
                        break
            return result

    async def settle(
        self,
        thread_id: str,
        run_id: str,
        item_id: str,
        expected_version: int,
        disposition: Literal["handled", "ignored", "deferred", "pending"],
        reason: str,
        dependency: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with self.service.lock:
            receipt = await self.authorize_caller(thread_id, run_id)
            await self.readable(thread_id, receipt, item_id)
            if disposition == "deferred":
                if not dependency or dependency.get("kind") not in {"time", "operation", "human"}:
                    raise ValueError(
                        "Deferral requires an explicit time, operation, or human dependency"
                    )
                if dependency["kind"] == "time" and not isinstance(
                    dependency.get("at"), (int, float)
                ):
                    raise ValueError("Time dependency requires Unix seconds")
                if dependency["kind"] == "operation":
                    if not isinstance(dependency.get("receipt_id"), str) or not isinstance(
                        dependency.get("thread_id"), str
                    ):
                        raise ValueError("Operation dependency requires thread_id and receipt_id")
                    status = await exact_operation_status(
                        self.service.host, dependency["thread_id"], dependency["receipt_id"]
                    )
                    target = await self.service.host.get_thread(dependency["thread_id"])
                    if status is None or (
                        target.thread.thread_id != thread_id
                        and target.thread.coordinator_thread_id != thread_id
                    ):
                        raise ValueError(
                            "Dependency is not an exact operation of this Thread or its worker"
                        )
            row = await database(
                self.service.store.settle,
                item_id,
                expected_version,
                disposition,
                reason,
                receipt_id=receipt,
                dependency=dependency,
            )
            await self.service._dependencies()
            self.service.wake.set()
            return {key: row[key] for key in ("id", "version", "disposition", "reason")}

    async def request_send(
        self,
        thread_id: str,
        run_id: str,
        item_id: str,
        request_key: str,
        text: str,
        *,
        file: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if request_key.startswith("claw:"):
            raise ValueError("Delivery request prefix is reserved for service interactions")
        body = OutboundMessage.model_validate({"text": text, "file": file})
        async with self.service.lock:
            receipt = await self.authorize_caller(thread_id, run_id)
            item = await self.readable(thread_id, receipt, item_id)
            message, _ = self.service.ingress.source(item)
            unknown = await database(
                self.service.store.rows,
                "SELECT id FROM deliveries WHERE item_id=? AND status='unknown' AND request_key!=? "
                "AND NOT EXISTS (SELECT 1 FROM feedback_deliveries f "
                "WHERE f.delivery_id=deliveries.id)",
                (item_id, request_key),
            )
            if unknown:
                raise ValueError(
                    "An unknown delivery must be reconciled before another send for this item"
                )
            row = await database(
                self.service.store.request_delivery,
                request_key=request_key,
                channel_id=item["channel_id"],
                destination=Destination(
                    chat_id=message.chat_id, reply_to=message.message_id
                ).model_dump(),
                thread_id=thread_id,
                item_id=item_id,
                body=body.model_dump(),
            )
        return await self.service.delivery.send(row["id"])
