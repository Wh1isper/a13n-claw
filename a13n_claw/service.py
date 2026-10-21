"""Messaging policy and durable business obligations over one concrete embedded App."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import time
from contextlib import suppress
from typing import Any, Literal

from a13n_harness_ui.app import HarnessUiApp
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.surfaces import NewThreadDefaults
from a13n_harness_ui.thread_files import AttachmentUpload

from a13n_claw import controls
from a13n_claw.async_utils import database as database
from a13n_claw.business import BusinessStore, encode, identifier
from a13n_claw.feedback import Feedback
from a13n_claw.product_configuration import ProductConfiguration
from a13n_claw.scheduler import Scheduler
from a13n_claw.settings import ChannelBinding, ClawSettings, GitHubConnection, connection_identity
from a13n_claw.transport import Destination, InboundMessage, MessagingAdapter, OutboundMessage

logger = logging.getLogger("a13n_claw.service")
_TERMINAL = {"completed", "failed", "cancelled", "suspended"}
# Only explicit pre-admission conflicts are safe to retry. Other errors can follow admission.
_REJECTED = {"thread_run_active", "app_stopping", "thread_touch_failed"}


class ClawService:
    def __init__(
        self, settings: ClawSettings, store: BusinessStore, adapters: dict[str, MessagingAdapter]
    ):
        self.settings = settings
        self.store = store
        self.adapters = adapters
        self._host: HarnessUiApp | None = None
        self.lock = asyncio.Lock()
        self.intake_lock = asyncio.Lock()
        self.active_sends = 0
        self.sends_idle = asyncio.Event()
        self.sends_idle.set()
        self.wake = asyncio.Event()
        self.stopping = False
        self.last_error: str | None = None
        self.connection_errors: dict[str, str] = {}
        self.scheduler = Scheduler(self, store.path.parent / "schedules.yaml")
        self.feedback = Feedback(self)
        self.configuration = ProductConfiguration(self)

    @property
    def host(self) -> HarnessUiApp:
        if self._host is None:
            raise RuntimeError("Embedded Host is not open")
        return self._host

    def bind(self, host: HarnessUiApp) -> None:
        self._host = host

    def binding(self, message: InboundMessage) -> tuple[str, ChannelBinding] | None:
        root = None
        for key, binding in self.settings.channels.items():
            if (binding.connection, binding.chat_id) != (message.connection, message.chat_id):
                continue
            # An explicit topic binding, including a disabled one, always wins.
            if binding.topic_id == message.topic_id:
                return key, binding
            if message.platform == "lark" and binding.topic_id is None:
                root = key, binding
        return root

    def authorize_message(self, message: InboundMessage, channel_id: str) -> ChannelBinding:
        binding = self.settings.channels.get(channel_id)
        connection = self.settings.connections.get(message.connection)
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
        async with self.intake_lock:
            if self.stopping:
                raise ValueError("Claw is shutting down")
            fingerprint = hashlib.sha256(
                encode(message.model_dump(mode="json")).encode()
            ).hexdigest()
            previous = await database(
                self.store.rows,
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
                    self.authorize_message(message, channel_id)
                    mentions_only = binding.mentions_only
                    if mentions_only is None:
                        mentions_only = self.settings.connections[message.connection].mentions_only
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
                        self.settings.connections[message.connection]
                    ),
                    "files": [],
                    "unavailable": list(message.unavailable),
                    "attachments_pending": bool(message.attachments)
                    and not controls.is_control(message.text),
                }
                if controls.is_control(message.text):
                    body["control"] = message.text.removeprefix("/claw").strip()
            item = await database(
                self.store.accept,
                connection=message.connection,
                event_id=message.event_id,
                fingerprint=fingerprint,
                channel_id=channel_id,
                mode=self.settings.mode,
                project_id=(
                    self.settings.main_project_id
                    if self.settings.mode == "one_thread"
                    else binding.project_id
                    if binding
                    else None
                ),
                body=body,
                reason=reason,
            )
            if eligible:
                await self.feedback.status(item, "retained")
            self.wake.set()

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
                    data = await self.adapters[message.connection].download(
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
        await database(self.store.update_body, item["id"], body)
        item["body"] = encode(body)

    async def ensure_target(
        self, key: str, project_id: str, *, agent_id: str | None = None, model_id: str | None = None
    ) -> str:
        target = await database(self.store.target, key)
        if target is None or target["status"] == "retry":
            target = await database(self.store.begin_target, key, project_id)
            try:
                await self.host.create_thread(
                    thread_id=target["thread_id"],
                    title="Claw Main" if key == "main" else key,
                    defaults=NewThreadDefaults(
                        project_id=project_id, agent_id=agent_id, default_model_id=model_id
                    ),
                    coordinator=key == "main",
                )
            except BaseException:
                await database(
                    self.store.target_failure, key, "Creation outcome requires inspection"
                )
                raise
            await database(self.store.finish_target, key)
        elif target["status"] != "created":
            raise ValueError("Target creation requires operator reconciliation")
        if target["project_id"] != project_id:
            raise ValueError("Retained target Project cannot be rebound")
        detail = await self.host.get_thread(target["thread_id"])
        if detail.thread.archived or detail.thread.configuration.project_id != project_id:
            raise ValueError("Retained target is archived or its Project changed")
        if key == "main" and detail.thread.role != "coordinator":
            raise ValueError("Canonical Main is no longer a Coordinator")
        return target["thread_id"]

    async def resolve_target(self, key: str, action: str, evidence: str) -> dict[str, Any]:
        async with self.lock:
            target = await database(self.store.target, key)
            if target is None:
                raise ValueError("Unknown target")
            if action == "adopt":
                detail = await self.host.get_thread(target["thread_id"])
                if (
                    detail.thread.archived
                    or detail.thread.configuration.project_id != target["project_id"]
                    or (key == "main" and detail.thread.role != "coordinator")
                ):
                    raise ValueError("Existing Thread does not match the recorded target")
            await database(self.store.resolve_target, key, action, evidence)
            self.wake.set()
            return {"key": key, "thread_id": target["thread_id"], "action": action}

    async def operator_input(
        self,
        thread_id: str,
        text: str,
        attachment_ids: tuple[str, ...] = (),
        *,
        receipt_id: str | None = None,
    ):
        """Submit once using native semantics; retain Main attention, not a replay queue."""
        async with self.lock:
            if receipt_id is None:
                occurrence = await self.scheduler.waiting(thread_id)
                if occurrence is not None:
                    return await self.scheduler.continue_work(
                        thread_id, occurrence, prompt=text, attachment_ids=attachment_ids
                    )
            detail = await self.host.get_thread(thread_id)
            instance = await database(self.store.instance)
            attention = None
            body = {
                "operator_attention": {
                    "worker_thread_id": thread_id,
                    "kind": "steer" if receipt_id else "submit",
                    "text": text,
                    "attachment_ids": list(attachment_ids),
                    "receipt_id": receipt_id,
                    "outcome": "unknown",
                    "instruction": (
                        "Inspect already-requested worker work; never resubmit from this notice."
                    ),
                },
                "files": [],
                "unavailable": [],
            }
            if (
                self.settings.mode == "one_thread"
                and instance["main_thread_id"]
                and detail.thread.coordinator_thread_id == instance["main_thread_id"]
            ):
                attention = await database(
                    self.store.accept,
                    connection="operator",
                    event_id=identifier("input"),
                    fingerprint=hashlib.sha256(encode(body).encode()).hexdigest(),
                    channel_id=None,
                    mode="one_thread",
                    project_id=instance["main_project_id"],
                    body=body,
                )
            try:
                if receipt_id is None:
                    result = await self.host.submit_thread(
                        thread_id=thread_id, prompt=text, attachment_ids=attachment_ids
                    )
                    outcome = "accepted"
                else:
                    result = await self.host.steer_root_operation(
                        receipt_id=receipt_id, message=text, attachment_ids=attachment_ids
                    )
                    outcome = "accepted" if result.accepted else "rejected"
            except BaseException:
                # Initial durable 'unknown' survives interrupted calls and failed publication.
                self.wake.set()
                raise
            if attention is not None:
                body["operator_attention"].update(outcome=outcome, receipt_id=result.receipt_id)
                await database(self.store.update_body, attention["id"], body)
                self.wake.set()
            return result

    async def authorized_body(self, item: dict[str, Any]) -> dict[str, Any]:
        body = json.loads(item["body"]) if item["body"] else {}
        if "operator_attention" not in body:
            return self.source(item)[1]
        instance = await database(self.store.instance)
        worker = await self.host.get_thread(body["operator_attention"]["worker_thread_id"])
        if (
            self.settings.mode != "one_thread"
            or not instance["main_thread_id"]
            or worker.thread.coordinator_thread_id != instance["main_thread_id"]
            or worker.thread.configuration.project_id != item["project_id"]
        ):
            raise ValueError("Worker attention no longer belongs to canonical Main")
        return body

    def source(self, item: dict[str, Any]) -> tuple[InboundMessage, dict[str, Any]]:
        if item["body"] is None:
            raise ValueError("Inbox content was filtered or pruned; submit a new message")
        body = json.loads(item["body"])
        if "operator_attention" in body:
            raise ValueError("Operator attention has no external messaging destination")
        message = InboundMessage.model_validate(body["message"])
        self.authorize_message(message, item["channel_id"])
        if "connection_identity" in body and body["connection_identity"] != connection_identity(
            self.settings.connections[message.connection]
        ):
            raise ValueError("Retained connection identity changed")
        return message, body

    async def _dispatch(self, thread_id: str, items: list[dict[str, Any]]) -> None:
        detail = await self.host.get_thread(thread_id)
        if detail.deferred_requests:
            for item in items:
                await self.feedback.status(item, "suspended")
                await self.feedback.decisions(item)
                await database(
                    self.store.delay,
                    item["id"],
                    "Host is awaiting decisions",
                    self.settings.retry_seconds,
                )
            return
        active = await self.host.active_root_operation(thread_id)
        if active is not None and active.status == "preparing":
            return
        configuration = await self.host.inspect_thread_configuration(thread_id)
        capabilities = configuration.next_capability_ids
        if active is not None:
            if (
                configuration.receipt_id != active.receipt.receipt_id
                or configuration.captured is None
            ):
                return
            capabilities = configuration.captured.capability_ids
        if "ClawMessaging" not in capabilities:
            for item in items:
                await database(
                    self.store.delay,
                    item["id"],
                    "Selected Agent lacks required ClawMessaging capability",
                    self.settings.retry_seconds,
                )
            return
        kind = "steer" if active is not None else "submit"
        if self.settings.mode == "one_thread":
            prompt = (
                "Claw business attention (not a new user task). Inspect these retained Inbox IDs "
                "with claw_inbox_read, explicitly reply if needed, and record dispositions. "
                "Already requested worker work must be inspected, not recreated. IDs: "
                + ", ".join(item["id"] for item in items)
            )
            attachments: tuple[str, ...] = ()
        else:
            message, body = self.source(items[0])
            uploads = []
            for file in body["files"]:
                if file.get("host_unavailable"):
                    continue
                try:
                    uploads.append(
                        await self.host.stage_thread_attachment(
                            thread_id=thread_id,
                            upload=AttachmentUpload(
                                name=file["name"], data=base64.b64decode(file["data"])
                            ),
                        )
                    )
                except (ValueError, HarnessUiError):
                    file["host_unavailable"] = True
                    body["unavailable"].append(f"Host attachment unavailable: {file['name']}")
                    await database(self.store.update_body, items[0]["id"], body)
            prompt = (
                f"Human IM input from {message.platform}/{message.connection}, "
                f"sender {message.sender_id}. "
                f"Inbox ID: {items[0]['id']}. "
                "Use Claw tools for explicit replies and disposition.\n"
                f"Unavailable content: {body['unavailable']}\n\n{message.text}"
            )
            attachments = tuple(upload.attachment_id for upload in uploads)
        attempt = await database(
            self.store.begin_attempt, thread_id, kind, [item["id"] for item in items]
        )
        try:
            if active is None:
                result = await self.host.submit_thread(
                    thread_id=thread_id, prompt=prompt, attachment_ids=attachments
                )
                receipt_id = result.receipt_id
            else:
                control = await self.host.steer_root_operation(
                    receipt_id=active.receipt.receipt_id,
                    message=prompt,
                    attachment_ids=attachments,
                )
                if not control.accepted:
                    await database(
                        self.store.finish_attempt,
                        attempt,
                        "rejected",
                        evidence="Steering rejected",
                        retry_seconds=self.settings.retry_seconds,
                    )
                    return
                receipt_id = control.receipt_id
        except HarnessUiError as exc:
            await database(
                self.store.finish_attempt,
                attempt,
                "rejected" if exc.code in _REJECTED else "unknown",
                evidence=exc.code,
                retry_seconds=self.settings.retry_seconds,
            )
        except BaseException:
            await database(
                self.store.finish_attempt,
                attempt,
                "unknown",
                evidence="Host call returned no conclusive response",
            )
            raise
        else:
            await database(self.store.finish_attempt, attempt, "accepted", receipt_id=receipt_id)
            for item in items:
                await self.feedback.status(item, "admitted")

    async def reconcile(self) -> None:
        """Serialize product calls only. Native collaboration keeps its own Host admission."""
        async with self.lock:
            if self.stopping:
                return
            await self._observe_attempts()
            await self._dependencies()
            try:
                await self.scheduler.reconcile()
            except Exception as exc:
                self.scheduler.error = type(exc).__name__
                logger.error("Schedule reconciliation failed (%s)", type(exc).__name__)
            rows = await database(self.store.actionable)
            grouped: dict[str, list[dict[str, Any]]] = {}
            for item in rows:
                try:
                    body = await self.authorized_body(item)
                    await self.feedback.status(item, "retained")
                    if "control" in body:
                        await controls.execute(self, item)
                        continue
                    if "operator_attention" not in body:
                        await self.collect_attachments(item)
                    if item["mode"] != self.settings.mode:
                        raise ValueError("Retained work belongs to another mode")
                    key = (
                        "main"
                        if item["mode"] == "one_thread"
                        else f"channel:{item['channel_id']}"
                        if item["mode"] == "per_channel"
                        else f"event:{item['id']}"
                    )
                    thread_id = await self.ensure_target(key, item["project_id"])
                    await database(self.store.assign_thread, item["id"], thread_id)
                    item["thread_id"] = thread_id
                    grouped.setdefault(thread_id, []).append(item)
                except (ValueError, HarnessUiError) as exc:
                    await database(
                        self.store.delay, item["id"], str(exc), self.settings.retry_seconds
                    )
                    await self.feedback.status(item, "blocked")
            for thread_id, items in grouped.items():
                selected = items if self.settings.mode == "one_thread" else items[:1]
                try:
                    await self._dispatch(thread_id, selected)
                except (ValueError, HarnessUiError) as exc:
                    for item in selected:
                        await database(
                            self.store.delay, item["id"], str(exc), self.settings.retry_seconds
                        )
        for row in await database(
            self.store.rows,
            "SELECT * FROM deliveries WHERE status='pending' ORDER BY created LIMIT 50",
        ):
            await self.deliver(row["id"])

    async def exact_operation_status(self, thread_id: str, receipt_id: str) -> str | None:
        """Use exact public live or saved evidence, never the latest unrelated outcome."""
        try:
            operation = await self.host.get_root_operation(receipt_id)
        except HarnessUiError:
            detail = await self.host.get_thread(thread_id)
            saved = detail.thread.last_execution
            if saved is not None and saved.execution_id == receipt_id:
                return saved.status
            return None
        if operation.receipt.thread_id != thread_id:
            raise ValueError("Operation does not belong to the selected Thread")
        return operation.status

    async def _observe_attempts(self) -> None:
        attempts = await database(
            self.store.rows,
            "SELECT * FROM attempts WHERE status IN ('accepted','unknown') "
            "AND receipt_id IS NOT NULL",
        )
        for attempt in attempts:
            try:
                status = await self.exact_operation_status(
                    attempt["thread_id"], attempt["receipt_id"]
                )
            except (HarnessUiError, ValueError):
                status = None
            if status is None or status == "unknown":
                await self.feedback.observe(attempt, "unknown")
                await database(
                    self.store.finish_attempt,
                    attempt["id"],
                    "unknown",
                    evidence="Exact receipt unavailable",
                )
                continue
            if status not in _TERMINAL:
                if status == "running":
                    await self.feedback.observe(attempt, "running")
                continue
            await self.feedback.observe(attempt, status)
            # The exact operation is observable. Unsettled One Thread work remains an obligation.
            # Routed replies are explicit tools too: no arbitrary final output is broadcast.
            await database(
                self.store.finish_attempt,
                attempt["id"],
                "observed",
                evidence=encode({"receipt_id": attempt["receipt_id"], "status": status}),
                retry_seconds=self.settings.retry_seconds,
            )

    async def _dependencies(self) -> None:
        rows = await database(self.store.rows, "SELECT * FROM inbox WHERE disposition='deferred'")
        for item in rows:
            dependency = json.loads(item["dependency"]) if item["dependency"] else {}
            ready = False
            if dependency.get("kind") == "time":
                ready = time.time() >= dependency["at"]
            elif dependency.get("kind") == "operation":
                try:
                    status = await self.exact_operation_status(
                        dependency["thread_id"], dependency["receipt_id"]
                    )
                except (HarnessUiError, ValueError):
                    status = None
                ready = status in _TERMINAL
                if status is None or status == "unknown":
                    await database(self.store.dependency_unavailable, item["id"], item["version"])
            if ready:
                await database(
                    self.store.reactivate,
                    item["id"],
                    item["version"],
                    "Recorded dependency completed",
                )

    async def _caller(self, thread_id: str, run_id: str) -> str:
        detail = await self.host.get_thread(thread_id)
        if detail.thread.parent_thread_id is not None:
            raise ValueError("Subagents have no Claw messaging authority")
        if self.settings.mode == "one_thread":
            instance = await database(self.store.instance)
            if thread_id != instance["main_thread_id"] or detail.thread.role != "coordinator":
                raise ValueError("Only canonical Main has Claw messaging authority")
        operation = await self.host.active_root_operation(thread_id)
        if operation is None or operation.run_id != run_id:
            raise ValueError("Caller no longer owns the active Host execution")
        return operation.receipt.receipt_id

    async def _read(self, thread_id: str, receipt_id: str, item_id: str) -> dict[str, Any]:
        row = await database(self.store.readable, item_id, receipt_id)
        if row["thread_id"] != thread_id or row["mode"] != self.settings.mode:
            raise ValueError("Inbox item is outside the caller's routed context")
        detail = await self.host.get_thread(thread_id)
        if detail.thread.configuration.project_id != row["project_id"]:
            raise ValueError("Thread Project no longer matches the retained Inbox context")
        body = await self.authorized_body(row)
        if "control" in body:
            raise ValueError("IM controls are handled by the Host boundary, not Agent tools")
        return row

    async def inbox_read(self, thread_id: str, run_id: str, item_id: str) -> dict[str, Any]:
        async with self.lock:
            receipt = await self._caller(thread_id, run_id)
            row = await self._read(thread_id, receipt, item_id)
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

    async def inbox_file(
        self, thread_id: str, run_id: str, item_id: str, index: int
    ) -> tuple[str, bytes]:
        async with self.lock:
            receipt = await self._caller(thread_id, run_id)
            row = await self._read(thread_id, receipt, item_id)
            _, body = self.source(row)
            if not 0 <= index < len(body["files"]):
                raise ValueError("Attachment index is unavailable")
            file = body["files"][index]
            return file["name"], base64.b64decode(file["data"], validate=True)

    async def inbox_list(self, thread_id: str, run_id: str) -> list[dict[str, Any]]:
        async with self.lock:
            receipt = await self._caller(thread_id, run_id)
            rows = await database(
                self.store.rows,
                "SELECT * FROM inbox WHERE thread_id=? AND disposition='pending' "
                "ORDER BY created LIMIT 50",
                (thread_id,),
            )
            result = []
            for row in rows:
                try:
                    await self._read(thread_id, receipt, row["id"])
                    unknown = await database(
                        self.store.rows,
                        "SELECT id FROM deliveries WHERE item_id=? "
                        "AND status IN ('sending','unknown') AND NOT EXISTS "
                        "(SELECT 1 FROM feedback_deliveries f WHERE f.delivery_id=deliveries.id)",
                        (row["id"],),
                    )
                    if not unknown:
                        result.append(
                            {
                                key: row[key]
                                for key in ("id", "channel_id", "version", "disposition")
                            }
                        )
                except ValueError:
                    continue
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
        async with self.lock:
            receipt = await self._caller(thread_id, run_id)
            await self._read(thread_id, receipt, item_id)
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
                    status = await self.exact_operation_status(
                        dependency["thread_id"], dependency["receipt_id"]
                    )
                    target = await self.host.get_thread(dependency["thread_id"])
                    if status is None or (
                        target.thread.thread_id != thread_id
                        and target.thread.coordinator_thread_id != thread_id
                    ):
                        raise ValueError(
                            "Dependency is not an exact operation of this Thread or its worker"
                        )
            row = await database(
                self.store.settle,
                item_id,
                expected_version,
                disposition,
                reason,
                receipt_id=receipt,
                dependency=dependency,
            )
            await self._dependencies()
            self.wake.set()
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
        async with self.lock:
            receipt = await self._caller(thread_id, run_id)
            item = await self._read(thread_id, receipt, item_id)
            message, _ = self.source(item)
            unknown = await database(
                self.store.rows,
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
                self.store.request_delivery,
                request_key=request_key,
                channel_id=item["channel_id"],
                destination=Destination(
                    chat_id=message.chat_id, reply_to=message.message_id
                ).model_dump(),
                thread_id=thread_id,
                item_id=item_id,
                body=body.model_dump(),
            )
        return await self.deliver(row["id"])

    async def deliver(self, delivery_id: str) -> dict[str, Any]:
        async with self.lock:
            row = await database(self.store.delivery, delivery_id)
            if row["status"] != "pending":
                return row
            try:
                if row["item_id"] is None:
                    connection, destination = await self.scheduler.delivery_destination(row)
                else:
                    item = await database(self.store.item, row["item_id"])
                    message, source = self.source(item)
                    await self.feedback.authorize_delivery(row, item)
                    if (
                        "control" in source
                        and message.sender_id
                        not in self.settings.channels[item["channel_id"]].operators
                    ):
                        raise ValueError("Control disclosure grant was revoked")
                    destination = Destination.model_validate_json(row["destination"])
                    if (
                        destination.chat_id != message.chat_id
                        or destination.reply_to != message.message_id
                    ):
                        raise ValueError("Retained destination differs from original source")
                    connection = message.connection
                adapter = self.adapters[connection]
            except (ValueError, KeyError, HarnessUiError):
                return {**row, "blocked": "Current destination authority is unavailable"}
            if not await database(self.store.claim_delivery, delivery_id):
                return await database(self.store.delivery, delivery_id)
            self.active_sends += 1
            self.sends_idle.clear()
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
                    self.store.finish_delivery,
                    delivery_id,
                    "unknown",
                    "Send returned no conclusive response",
                )
                raise
            await database(self.store.finish_delivery, delivery_id, result.status, result.evidence)
            return await database(self.store.delivery, delivery_id)
        finally:
            self.active_sends -= 1
            if self.active_sends == 0:
                self.sends_idle.set()

    async def run(self) -> None:
        while not self.stopping:
            self.wake.clear()
            try:
                await self.reconcile()
                self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = type(exc).__name__
                logger.error("Claw reconciliation failed (%s)", type(exc).__name__)
            with suppress(TimeoutError):
                await asyncio.wait_for(self.wake.wait(), self.settings.reconcile_seconds)
