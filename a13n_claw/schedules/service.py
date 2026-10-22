"""Scheduled business requests over the public Host; one fresh root per occurrence."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.surfaces import DecisionResponseBatch

from a13n_claw.async_utils import database
from a13n_claw.configuration.models import LarkConnection, connection_identity
from a13n_claw.encoding import encode
from a13n_claw.messaging.transport import Destination
from a13n_claw.runtime.observations import REJECTED, retained_output
from a13n_claw.schedules.models import (
    ScheduleCatalog,
    ScheduleDefinition,
    ScheduleSource,
    ScheduleTask,
    read_catalog,
    write_catalog,
)
from a13n_claw.schedules.store import ScheduleStore

if TYPE_CHECKING:
    from a13n_claw.runtime.service import ClawService


class Scheduler:
    def __init__(self, service: ClawService, path: Path):
        self.service = service
        self.path = path
        self.store = ScheduleStore(service.store)
        self.catalog = ScheduleCatalog()
        self.digest: str | None = None
        self.error: str | None = None

    async def reload(self) -> dict[str, Any]:
        candidate, digest = await database(read_catalog, self.path)
        # Validate the complete candidate before changing the live catalog.
        for definition in candidate.schedules.values():
            if definition.enabled:
                self.authorize(definition)
        self.catalog, self.digest = candidate, digest
        self.error = None
        return self.view()

    def view(self) -> dict[str, Any]:
        return {
            "digest": self.digest,
            "catalog": self.catalog.model_dump(mode="json"),
            "error": self.error,
        }

    def authorize(self, definition: ScheduleDefinition) -> dict | None:
        settings = self.service.settings
        if definition.author != "operator":
            source = settings.channels.get(definition.source_channel or "")
            if source is None or not source.enabled or definition.author not in source.operators:
                raise ValueError("Schedule author no longer has Channel operator authority")
            connection = settings.connections[source.connection]
            if not isinstance(connection, LarkConnection) or not connection.enabled:
                raise ValueError("Schedule source connection is unavailable")
            if definition.source != ScheduleSource(
                connection=source.connection,
                app_id=connection.app_id,
                domain=connection.domain,
                chat_id=source.chat_id,
                topic_id=source.topic_id,
            ):
                raise ValueError("Schedule source identity changed")
            project = (
                settings.main_project_id if settings.mode == "one_thread" else source.project_id
            )
            if definition.project_id != project or (
                definition.destination is not None
                and definition.destination.channel_id != definition.source_channel
            ):
                raise ValueError("IM schedule exceeds its source context")
            if definition.agent_id is not None or definition.model_id is not None:
                raise ValueError("IM schedules use the source Project's native defaults")
        if definition.destination is None:
            return None
        channel = settings.channels.get(definition.destination.channel_id)
        if (
            channel is None
            or not channel.enabled
            or not settings.connections[channel.connection].enabled
        ):
            raise ValueError("Schedule destination is unavailable")
        return {
            "channel_id": definition.destination.channel_id,
            "connection": channel.connection,
            "connection_identity": connection_identity(settings.connections[channel.connection]),
            "chat_id": channel.chat_id,
            "topic_id": channel.topic_id,
            "reply_to": definition.destination.reply_to or channel.topic_id,
        }

    async def replace(
        self, catalog: ScheduleCatalog, expected_digest: str | None
    ) -> dict[str, Any]:
        for definition in catalog.schedules.values():
            if definition.enabled:
                self.authorize(definition)
        digest = await database(write_catalog, self.path, catalog, expected_digest)
        self.catalog, self.digest = catalog, digest
        self.error = None
        self.service.wake.set()
        return self.view()

    async def put(
        self, schedule_id: str, definition: ScheduleDefinition | None, expected_digest: str | None
    ) -> dict[str, Any]:
        # Work from disk, so a caller cannot accidentally delete an un-reloaded external edit.
        catalog, digest = await database(read_catalog, self.path)
        if digest != expected_digest:
            raise ValueError("Schedule source changed; refetch before editing")
        values = dict(catalog.schedules)
        if definition is None:
            if schedule_id not in values:
                raise ValueError("Unknown schedule")
            del values[schedule_id]
        else:
            values[schedule_id] = definition
        return await self.replace(ScheduleCatalog(schedules=values), expected_digest)

    async def trigger(self, schedule_id: str, request_key: str) -> dict[str, Any]:
        definition = self.catalog.schedules.get(schedule_id)
        if definition is None or not definition.enabled:
            raise ValueError("Schedule is absent or paused")
        row = await database(
            self.store.trigger,
            schedule_id,
            definition,
            self.authorize(definition),
            request_key,
            time.time(),
        )
        self.service.wake.set()
        return row

    async def reconcile(self) -> None:
        # The Claw service lock serializes config, discovery, dispatch and receipt publication.
        await self.observe()
        destinations = {}
        eligible = {}
        for key, definition in self.catalog.schedules.items():
            try:
                destinations[key] = self.authorize(definition)
                eligible[key] = definition
            except ValueError as exc:
                self.error = str(exc)
        await database(
            self.store.discover, ScheduleCatalog(schedules=eligible), destinations, time.time()
        )
        for row in await database(self.store.pending, time.time()):
            current = self.catalog.schedules.get(row["schedule_id"])
            if current is None or not current.enabled:
                await database(
                    self.store.status,
                    row["id"],
                    "skipped",
                    "Schedule removed or paused before dispatch",
                )
                continue
            definition = ScheduleDefinition.model_validate_json(row["definition"])
            try:
                destination = self.authorize(definition)
                if destination != (json.loads(row["destination"]) if row["destination"] else None):
                    raise ValueError("Retained destination changed; no redirection is allowed")
                if await database(self.store.earlier_open, row):
                    if definition.overlap == "skip":
                        await database(
                            self.store.status,
                            row["id"],
                            "skipped",
                            "Earlier occurrence is unresolved",
                        )
                    continue
                thread_id = await self.service.ensure_target(
                    f"schedule:{row['id']}",
                    definition.project_id,
                    agent_id=definition.agent_id,
                    model_id=definition.model_id,
                )
                detail = await self.service.host.get_thread(thread_id)
                if (
                    detail.deferred_requests
                    or await self.service.host.active_root_operation(thread_id) is not None
                ):
                    raise ValueError("Occurrence Thread is busy or awaiting decisions")
            except (ValueError, HarnessUiError) as exc:
                await database(
                    self.store.status,
                    row["id"],
                    "pending",
                    str(exc),
                    delay=self.service.settings.retry_seconds,
                )
                continue
            await database(self.store.start, row["id"], thread_id)
            prompt = (
                f"Scheduled task {row['schedule_id']} / occurrence {row['id']}. "
                f"Authorized by {definition.author}; timezone {definition.timing.timezone}. "
                "This is independent scheduled work, not an incoming chat or a managed worker. "
                "Return the task result as your final answer. The service handles only the "
                "configured result notification; do not use Claw Inbox/reply tools "
                "or send a duplicate notification.\n\n" + definition.prompt
            )
            try:
                receipt = await self.service.host.submit_thread(thread_id=thread_id, prompt=prompt)
            except HarnessUiError as exc:
                safe = exc.code in REJECTED
                await database(
                    self.store.status,
                    row["id"],
                    "pending" if safe else "unknown",
                    exc.code,
                    delay=self.service.settings.retry_seconds,
                )
            except BaseException:
                await database(
                    self.store.status, row["id"], "unknown", "Host admission outcome unavailable"
                )
                raise
            else:
                await database(
                    self.store.status, row["id"], "accepted", receipt_id=receipt.receipt_id
                )

    async def observe(self) -> None:
        rows = await database(
            self.service.store.rows,
            "SELECT * FROM occurrences WHERE status IN ('accepted','unknown') "
            "AND receipt_id IS NOT NULL",
        )
        for row in rows:
            try:
                operation = await self.service.host.get_root_operation(row["receipt_id"])
            except HarnessUiError:
                try:
                    detail = await self.service.host.get_thread(row["thread_id"])
                except HarnessUiError:
                    continue
                saved = detail.thread.last_execution
                if (
                    saved is None
                    or saved.execution_id != row["receipt_id"]
                    or saved.status == "unknown"
                ):
                    await database(
                        self.store.status,
                        row["id"],
                        "unknown",
                        "Exact saved operation evidence unavailable",
                    )
                    continue
                status = saved.status
                result = (
                    await retained_output(self.service.host, row["thread_id"], detail)
                    if status == "completed"
                    else None
                ) or (
                    f"Exact operation {saved.execution_id}: {status}. "
                    f"{saved.error_message or ''} "
                    f"Inspect Thread {row['thread_id']} for retained output."
                )
            else:
                if operation.receipt.thread_id != row["thread_id"]:
                    raise ValueError("Occurrence receipt belongs to another Thread")
                status = operation.status
                output = (
                    operation.outcome.execution.output if operation.outcome is not None else None
                )
                result = (
                    output
                    if isinstance(output, str)
                    else encode(output)
                    if output is not None
                    else f"Operation {status}; inspect Thread {row['thread_id']}"
                )
            if status == "suspended":
                await database(
                    self.store.status,
                    row["id"],
                    "waiting",
                    "Native decision/continuation required; inspect the occurrence Thread",
                )
            elif status in {"completed", "failed", "cancelled"}:
                await database(self.store.complete, row["id"], status, result)

    async def waiting(self, thread_id: str) -> dict[str, Any] | None:
        await self.observe()
        rows = await database(
            self.service.store.rows,
            "SELECT * FROM occurrences WHERE thread_id=? "
            "AND status IN ('calling','accepted','waiting','unknown')",
            (thread_id,),
        )
        if rows and rows[0]["status"] != "waiting":
            raise ValueError("Scheduled work is active or uncertain; reconcile before continuing")
        return rows[0] if rows else None

    async def respond(self, thread_id: str, response: DecisionResponseBatch):
        row = await self.waiting(thread_id)
        return await self.continue_work(thread_id, row, response=response)

    async def continue_work(
        self,
        thread_id: str,
        row: dict[str, Any] | None,
        *,
        prompt: str | None = None,
        attachment_ids: tuple[str, ...] = (),
        response: DecisionResponseBatch | None = None,
    ):
        # The caller holds the service lock. Keep ambiguity durable before either Host call.
        if row is not None:
            definition = ScheduleDefinition.model_validate_json(row["definition"])
            self.authorize(definition)
            if response is not None:
                await self.service.host.thread_decisions(
                    thread_id=thread_id,
                    expected_continuation_id=response.expected_continuation_id,
                )
            await database(self.store.continuing, row["id"])
        try:
            if response is not None:
                receipt = await self.service.host.respond_decisions(
                    thread_id=thread_id, response=response
                )
            else:
                assert prompt is not None
                receipt = await self.service.host.submit_thread(
                    thread_id=thread_id, prompt=prompt, attachment_ids=attachment_ids
                )
        except BaseException:
            if row is not None:
                await database(
                    self.store.status,
                    row["id"],
                    "unknown",
                    "Continuation admission outcome requires operator reconciliation",
                )
            raise
        if row is not None:
            await database(self.store.status, row["id"], "accepted", receipt_id=receipt.receipt_id)
            self.service.wake.set()
        return receipt

    async def attach(self, occurrence_id: str, receipt_id: str) -> dict[str, Any]:
        row = await database(self.store.get, occurrence_id)
        if row["status"] not in {"waiting", "unknown"}:
            raise ValueError("Only waiting or uncertain work accepts a reconciled receipt")
        operation = await self.service.host.get_root_operation(receipt_id)
        if operation.receipt.thread_id != row["thread_id"] or receipt_id == row["receipt_id"]:
            raise ValueError("Continuation must be a new operation of the same occurrence Thread")
        await database(self.store.status, occurrence_id, "accepted", receipt_id=receipt_id)
        self.service.wake.set()
        return await database(self.store.get, occurrence_id)

    async def cancel(self, occurrence_id: str) -> dict[str, Any]:
        row = await database(self.store.get, occurrence_id)
        if row["status"] == "pending":
            await database(
                self.store.status, occurrence_id, "skipped", "Cancelled before admission"
            )
        elif row["status"] == "waiting":
            if await self.service.host.active_root_operation(row["thread_id"]) is not None:
                raise ValueError("Occurrence Thread has active work; attach its receipt first")
            await database(
                self.store.complete,
                occurrence_id,
                "cancelled",
                "Waiting scheduled work abandoned by operator",
            )
        elif row["status"] == "accepted" and row["receipt_id"]:
            return (await self.service.host.cancel_root_operation(row["receipt_id"])).model_dump(
                mode="json"
            )
        else:
            raise ValueError("Occurrence cannot be cancelled; inspect its exact state")
        return await database(self.store.get, occurrence_id)

    async def delivery_destination(self, delivery: dict[str, Any]) -> tuple[str, Destination]:
        rows = await database(
            self.service.store.rows,
            "SELECT o.* FROM occurrences o JOIN job_deliveries d ON d.occurrence_id=o.id "
            "WHERE d.delivery_id=?",
            (delivery["id"],),
        )
        if not rows:
            raise ValueError("Delivery has no authorized schedule occurrence")
        row = rows[0]
        definition = ScheduleDefinition.model_validate_json(row["definition"])
        current = self.catalog.schedules.get(row["schedule_id"])
        if current is None or not current.enabled:
            raise ValueError("Schedule is removed or paused")
        destination = self.authorize(definition)
        if destination is None or destination != json.loads(row["destination"]):
            raise ValueError("Schedule destination authority changed")
        expected = Destination(chat_id=destination["chat_id"], reply_to=destination["reply_to"])
        if (
            expected != Destination.model_validate_json(delivery["destination"])
            or destination["channel_id"] != delivery["channel_id"]
        ):
            raise ValueError("Scheduled delivery destination mismatch")
        return destination["connection"], expected

    async def manage(
        self,
        thread_id: str,
        run_id: str,
        item_id: str,
        action: str,
        schedule_id: str | None = None,
        task: ScheduleTask | None = None,
        expected_digest: str | None = None,
        request_key: str | None = None,
    ) -> dict[str, Any]:
        async with self.service.lock:
            receipt = await self.service.inbox.authorize_caller(thread_id, run_id)
            row = await self.service.inbox.readable(thread_id, receipt, item_id)
            message, _ = self.service.ingress.source(row)
            channel = self.service.settings.channels[row["channel_id"]]
            if message.sender_id not in channel.operators:
                raise ValueError("Scheduling requires a Channel operator's request")
            connection = self.service.settings.connections[channel.connection]
            if not isinstance(connection, LarkConnection):
                raise ValueError("Agent schedule management requires a Lark operator source")
            source = ScheduleSource(
                connection=channel.connection,
                app_id=connection.app_id,
                domain=connection.domain,
                chat_id=message.chat_id,
                topic_id=channel.topic_id,
            )
            # Read disk under the same editor lock. Include un-reloaded edits in CAS checks.
            catalog, digest = await database(read_catalog, self.path)
            owned = {
                key: value
                for key, value in catalog.schedules.items()
                if value.author == message.sender_id
                and value.source_channel == row["channel_id"]
                and value.source == source
            }
            if action == "list":
                return {
                    "digest": digest,
                    "schedules": {
                        key: value.model_dump(mode="json") for key, value in owned.items()
                    },
                }
            if schedule_id is None:
                raise ValueError("Schedule ID is required")
            if schedule_id in catalog.schedules and schedule_id not in owned:
                raise ValueError("Schedule belongs to another author or source")
            if action == "trigger":
                if schedule_id not in owned or not request_key:
                    raise ValueError("Trigger requires an owned schedule and stable request_key")
                return await self.trigger(schedule_id, request_key)
            if action == "remove":
                if schedule_id not in owned:
                    raise ValueError("Unknown owned schedule")
                await self.put(schedule_id, None, expected_digest)
            elif action == "put":
                if task is None:
                    raise ValueError("put requires a task definition")
                if task.destination is not None:
                    raise ValueError("IM schedules use their original Channel destination")
                definition = ScheduleDefinition.model_validate(
                    {
                        **task.model_dump(mode="json"),
                        "destination": {"channel_id": row["channel_id"]},
                        "project_id": row["project_id"],
                        "author": message.sender_id,
                        "source_channel": row["channel_id"],
                        "source": source.model_dump(),
                    }
                )
                await self.put(schedule_id, definition, expected_digest)
            else:
                raise ValueError("Expected list, put, remove, or trigger")
            return {"schedule_id": schedule_id, "digest": self.digest, "action": action}
