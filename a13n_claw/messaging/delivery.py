"""Delivery policy and business operations under the application ownership boundary."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from a13n_harness_ui.errors import HarnessUiError

from a13n_claw.async_utils import database
from a13n_claw.messaging.transport import Destination, OutboundMessage

if TYPE_CHECKING:
    from a13n_claw.runtime.service import ClawService


class Delivery:
    def __init__(self, service: ClawService):
        self.service = service
        self._cursor: tuple[float, str] = (0, "")

    async def reconcile(self) -> None:
        """Rotate bounded pages so revoked destinations cannot starve later sends."""
        rows = await database(
            self.service.store.rows,
            "SELECT id,created FROM deliveries WHERE status='pending' "
            "AND (created,id)>(?,?) ORDER BY created,id LIMIT 50",
            self._cursor,
        )
        # This cursor controls scan fairness only; durable status still owns retry safety.
        self._cursor = (rows[-1]["created"], rows[-1]["id"]) if len(rows) == 50 else (0, "")
        for row in rows:
            await self.send(row["id"])

    async def send(self, delivery_id: str) -> dict[str, Any]:
        async with self.service.lock:
            row = await database(self.service.store.delivery, delivery_id)
            if row["status"] != "pending":
                return row
            try:
                if row["item_id"] is None:
                    connection, destination = await self.service.scheduler.delivery_destination(row)
                else:
                    item = await database(self.service.store.item, row["item_id"])
                    message, source = self.service.ingress.source(item)
                    await self.service.feedback.authorize_delivery(row, item)
                    if (
                        "control" in source
                        and message.sender_id
                        not in self.service.settings.channels[item["channel_id"]].operators
                    ):
                        raise ValueError("Control disclosure grant was revoked")
                    destination = Destination.model_validate_json(row["destination"])
                    if (
                        destination.chat_id != message.chat_id
                        or destination.reply_to != message.message_id
                    ):
                        raise ValueError("Retained destination differs from original source")
                    connection = message.connection
                adapter = self.service.adapters[connection]
            except (ValueError, KeyError, HarnessUiError):
                return {**row, "blocked": "Current destination authority is unavailable"}
            if not await database(self.service.store.claim_delivery, delivery_id):
                return await database(self.service.store.delivery, delivery_id)
            self.service.active_sends += 1
            self.service.sends_idle.clear()
        # Sending does not hold the product lock. Reload joins this admitted generation
        # before closing its clients; duplicate requests can still inspect the sending row.
        try:
            try:
                result = await adapter.send(
                    destination,
                    OutboundMessage.model_validate_json(row["body"]),
                    request_id=delivery_id,
                )
            except BaseException:
                await database(
                    self.service.store.finish_delivery,
                    delivery_id,
                    "unknown",
                    "Send returned no conclusive response",
                )
                raise
            await database(
                self.service.store.finish_delivery, delivery_id, result.status, result.evidence
            )
            return await database(self.service.store.delivery, delivery_id)
        finally:
            self.service.active_sends -= 1
            if self.service.active_sends == 0:
                self.service.sends_idle.set()
