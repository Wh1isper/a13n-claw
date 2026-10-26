"""Messaging policy and durable business obligations over one concrete embedded App."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import time
from contextlib import suppress
from typing import Any

from a13n_harness_ui.app import HarnessUiApp
from a13n_harness_ui.errors import HarnessUiError
from a13n_harness_ui.surfaces import NewThreadDefaults
from a13n_harness_ui.thread_files import AttachmentUpload

from a13n_claw.async_utils import database
from a13n_claw.configuration.models import (
    ClawSettings,
)
from a13n_claw.configuration.product import ProductConfiguration
from a13n_claw.database.store import BusinessStore
from a13n_claw.encoding import encode, identifier
from a13n_claw.messaging import controls
from a13n_claw.messaging.delivery import Delivery
from a13n_claw.messaging.feedback import Feedback
from a13n_claw.messaging.inbox import Inbox
from a13n_claw.messaging.ingress import Ingress
from a13n_claw.messaging.transport import (
    MessagingAdapter,
)
from a13n_claw.runtime.observations import REJECTED, TERMINAL, exact_operation_status
from a13n_claw.schedules.service import Scheduler

logger = logging.getLogger("a13n_claw.runtime.service")


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
        self.ingress = Ingress(self)
        self.inbox = Inbox(self)
        self.delivery = Delivery(self)
        self.feedback = Feedback(self)
        self.configuration = ProductConfiguration(self)

    @property
    def host(self) -> HarnessUiApp:
        if self._host is None:
            raise RuntimeError("Embedded Host is not open")
        return self._host

    def bind(self, host: HarnessUiApp) -> None:
        self._host = host

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
            message, body = self.ingress.source(items[0])
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
                "rejected" if exc.code in REJECTED else "unknown",
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
                    body = await self.inbox.authorized_body(item)
                    await self.feedback.status(item, "retained")
                    if "control" in body:
                        await controls.execute(self, item)
                        continue
                    if "operator_attention" not in body:
                        await self.ingress.collect_attachments(item)
                    if item["mode"] != self.settings.mode:
                        raise ValueError("Retained work belongs to another mode")
                    key = (
                        "main"
                        if item["mode"] == "one_thread"
                        else f"channel:{item['channel_id']}"
                        if item["mode"] == "per_channel"
                        else f"event:{item['id']}"
                    )
                    if (
                        item["mode"] == "per_channel"
                        and body.get("message", {}).get("platform") == "github"
                    ):
                        key += f":github:{body['message']['message_id']}"
                    if item["thread_id"] is not None:
                        # Routing changes apply to new work, never to an admitted task.
                        targets = await database(
                            self.store.rows,
                            "SELECT * FROM targets WHERE thread_id=? AND status='created'",
                            (item["thread_id"],),
                        )
                        if not targets:
                            raise ValueError("Retained Thread has no established target")
                        key = targets[0]["key"]
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
        await self.delivery.reconcile()

    async def _observe_attempts(self) -> None:
        attempts = await database(
            self.store.rows,
            "SELECT * FROM attempts WHERE status IN ('accepted','unknown') "
            "AND receipt_id IS NOT NULL",
        )
        for attempt in attempts:
            try:
                status = await exact_operation_status(
                    self.host, attempt["thread_id"], attempt["receipt_id"]
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
            if status not in TERMINAL:
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
        rows = await database(
            self.store.rows,
            "SELECT * FROM inbox WHERE disposition='deferred' AND mode=?",
            (self.settings.mode,),
        )
        for item in rows:
            dependency = json.loads(item["dependency"]) if item["dependency"] else {}
            ready = False
            if dependency.get("kind") == "time":
                ready = time.time() >= dependency["at"]
            elif dependency.get("kind") == "operation":
                try:
                    status = await exact_operation_status(
                        self.host, dependency["thread_id"], dependency["receipt_id"]
                    )
                except (HarnessUiError, ValueError):
                    status = None
                ready = status in TERMINAL
                if status is None or status == "unknown":
                    await database(self.store.dependency_unavailable, item["id"], item["version"])
            if ready:
                await database(
                    self.store.reactivate,
                    item["id"],
                    item["version"],
                    "Recorded dependency completed",
                )

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
