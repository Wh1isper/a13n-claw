"""Control-role background connector and credential supervisor."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from uuid import uuid4

import httpx
from a13n_logging import get_logger
from a13n_service.app_sdk import is_app_enabled
from a13n_service.infra.db import short_session
from a13n_service.infra.errors import ServiceError
from a13n_service.runs.runtime import Runtime
from a13n_service.tenancy.tables import WorkspaceRow
from redis.exceptions import LockError, RedisError
from sqlalchemy import func, or_, select

from . import egress, github_intake, lark_retry
from .connectors import (
    LARK_CHANGES,
    STATUS_TTL,
    WRITE_STATUS,
    Intake,
    allows_lark,
    allows_lark_event,
    credential,
    intake_enabled,
    listener_key,
    offer_lark,
    offer_lark_event,
)
from .feishu import InboundEvent, InboundMessage, LarkAdapter, LarkSettings
from .github_poll import GitHubSettings
from .models import APP_ID, Claw, Connector, ConnectorEgress

logger = get_logger(__name__)


class Listeners:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self.tasks: dict[str, tuple[Intake, asyncio.Task[None]]] = {}
        self.github_tasks: dict[str, tuple[github_intake.Intake, asyncio.Task[None]]] = {}
        self.card_tasks: dict[str, asyncio.Task[None]] = {}
        self.egress_tasks: dict[str, asyncio.Task[None]] = {}
        self._card_slots = asyncio.Semaphore(16)
        self._egress_slots = asyncio.Semaphore(8)
        self._reconcile_lock = asyncio.Lock()
        self._subscription = asyncio.create_task(self._subscribe())
        self._write_status = runtime.redis.register_script(WRITE_STATUS)
        self._closing = False

    async def _subscribe(self) -> None:
        while True:
            try:
                async with self.runtime.redis.pubsub() as pubsub:
                    await pubsub.subscribe(LARK_CHANGES)
                    async for message in pubsub.listen():
                        organization_id = message["data"]
                        if message["type"] != "message" or not isinstance(organization_id, str):
                            continue
                        try:
                            await self.reconcile(organization_id)
                        except Exception:
                            logger.warning("Lark configuration reconciliation failed")
            except RedisError:
                logger.warning("Lark reconcile subscription unavailable")
                await asyncio.sleep(1)

    async def close(self) -> None:
        self._closing = True
        self._subscription.cancel()
        await asyncio.gather(self._subscription, return_exceptions=True)
        async with self._reconcile_lock:
            owned = [task for _, task in self.tasks.values() if not task.cancelling()]
            for task in owned:
                task.cancel()
            github = [task for _, task in self.github_tasks.values()]
            cards = list(self.card_tasks.values())
            egress_tasks = list(self.egress_tasks.values())
            for task in (*github, *cards, *egress_tasks):
                task.cancel()
            await asyncio.gather(
                *(task for _, task in self.tasks.values()),
                *github,
                *cards,
                *egress_tasks,
                return_exceptions=True,
            )

    def queue_cards(self, workspace_id: str, claw_id: str) -> None:
        """Keep provider latency out of the bounded organization sweep."""
        if self._closing or claw_id in self.card_tasks:
            return

        async def run() -> None:
            try:
                async with self._card_slots:
                    await lark_retry.reconcile(self.runtime, workspace_id, claw_id)
            except Exception:
                logger.warning("Lark retry card reconciliation failed", extra={"claw_id": claw_id})
            finally:
                self.card_tasks.pop(claw_id, None)

        self.card_tasks[claw_id] = asyncio.create_task(run())

    def queue_egress(self, workspace_id: str, claw_id: str) -> None:
        if self._closing or claw_id in self.egress_tasks:
            return

        async def run() -> None:
            try:
                async with self._egress_slots:
                    async with short_session(self.runtime.storage) as session:
                        ids = list(
                            await session.scalars(
                                select(ConnectorEgress.id)
                                .where(
                                    ConnectorEgress.claw_id == claw_id,
                                    or_(
                                        ConnectorEgress.state.in_(
                                            ("pending", "preparing", "revoke_pending", "unbinding")
                                        ),
                                        (ConnectorEgress.state == "ready")
                                        & ConnectorEgress.refresh_at.is_not(None)
                                        & (ConnectorEgress.refresh_at <= func.now()),
                                    ),
                                )
                                .order_by(ConnectorEgress.created_at, ConnectorEgress.id)
                                .limit(50)
                            )
                        )
                    for generation_id in ids:
                        await egress.process_generation(
                            self.runtime, workspace_id, claw_id, generation_id
                        )
            except Exception:
                logger.warning("Connector egress reconciliation failed", extra={"claw_id": claw_id})
            finally:
                self.egress_tasks.pop(claw_id, None)

        self.egress_tasks[claw_id] = asyncio.create_task(run())

    async def reconcile(self, organization_id: str) -> None:
        async with self._reconcile_lock:
            if not self._closing:
                await self._reconcile(organization_id)

    async def _reconcile(self, organization_id: str) -> None:
        async with short_session(self.runtime.storage) as session:
            enabled = await is_app_enabled(
                session, self.runtime, APP_ID, organization_id=organization_id
            )
            rows = (
                (
                    await session.execute(
                        select(Connector, Claw.id, Claw.workspace_id)
                        .join(Claw, Claw.id == Connector.claw_id)
                        .join(WorkspaceRow, WorkspaceRow.id == Claw.workspace_id)
                        .where(
                            WorkspaceRow.organization_id == organization_id,
                            WorkspaceRow.archived_at.is_(None),
                            Claw.archived_at.is_(None),
                            Connector.kind.in_(("lark", "github")),
                            Connector.enabled,
                        )
                    )
                ).all()
                if enabled
                else ()
            )
        needed = {}
        github_needed = {}
        for row, claw, workspace in rows:
            try:
                token = credential(self.runtime, organization_id, row)
            except ServiceError:
                logger.warning("Connector credential unavailable", extra={"connector_id": row.id})
                continue
            if token and row.kind == "github":
                settings = GitHubSettings.model_validate(row.config)
                if settings.mode == "polling":
                    github_needed[row.id] = github_intake.Intake(
                        organization_id, workspace, claw, row.id, settings, token
                    )
            elif token:
                needed[row.id] = Intake(
                    organization_id,
                    workspace,
                    claw,
                    row.id,
                    LarkSettings.model_validate(row.config),
                    token,
                )
        for key, (current, task) in list(self.tasks.items()):
            if current.organization_id == organization_id and (
                task.done() or current != needed.get(key)
            ):
                if not task.done():
                    if not task.cancelling():
                        task.cancel()
                    await asyncio.wait((task,))
                if not task.cancelled() and task.exception() is not None:
                    logger.warning("Lark listener stopped", extra={"connector_id": key})
                del self.tasks[key]
        for key, intake in needed.items():
            if key not in self.tasks:
                self.tasks[key] = (intake, asyncio.create_task(self.listen(intake, intake.token)))
        for key, (current, task) in list(self.github_tasks.items()):
            if current.organization_id == organization_id and (
                task.done() or current != github_needed.get(key)
            ):
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                del self.github_tasks[key]
        for key, intake in github_needed.items():
            if key not in self.github_tasks:
                self.github_tasks[key] = (
                    intake,
                    asyncio.create_task(github_intake.listen(self.runtime, intake)),
                )

    async def listen(self, intake: Intake, token: str) -> None:
        redis = self.runtime.redis
        lease_key = f"claw:lark:{intake.settings.app_id}"
        status_key = listener_key(intake.connector_id)
        prefix = f"{intake.connector_id}:"
        owner = f"{prefix}{uuid4().hex}"
        lock = redis.lock(lease_key, timeout=45, blocking=False, thread_local=False)
        if not await lock.acquire(token=owner):
            # A replica of this connector is normal; another connector with this App ID is not.
            holder = await redis.get(lease_key)
            if isinstance(holder, str) and not holder.startswith(prefix):
                await self._write_status(
                    keys=[lease_key, status_key], args=[holder, "conflict", STATUS_TTL]
                )
            return

        async def status_changed(status: str) -> None:
            with suppress(RedisError):
                await self._write_status(
                    keys=[lease_key, status_key], args=[owner, status, STATUS_TTL]
                )

        async def on_card(
            card_id: str, secret: str, open_id: str, chat_id: str, message_id: str
        ) -> bool:
            if not await lock.owned():
                raise LockError("Feishu listener has moved")
            return await lark_retry.retry(
                self.runtime, intake, card_id, secret, open_id, chat_id, message_id
            )

        adapter = LarkAdapter(
            intake.connector_id, intake.settings, token, on_status=status_changed, on_card=on_card
        )

        async def receive(event: InboundMessage | InboundEvent) -> None:
            if not await lock.owned():
                raise LockError("Feishu listener has moved")
            if isinstance(event, InboundEvent):
                if allows_lark_event(intake.settings, event):
                    await offer_lark_event(self.runtime, intake, event)
                return
            if not allows_lark(intake.settings, event):
                return
            try:
                snapshot = await asyncio.wait_for(adapter.snapshot(event), timeout=1.5)
                event = event.model_copy(update={"previous_messages_snapshot": snapshot})
            except Exception:
                # Provider content and credentials must never be logged; intake is independent.
                logger.warning(
                    "Lark context snapshot unavailable", extra={"connector_id": intake.connector_id}
                )
            if not await lock.owned():
                raise LockError("Feishu listener has moved")
            result = await offer_lark(self.runtime, intake, event)
            if result is not None and result.status == "full":
                try:
                    await adapter.react(event.message_id, "ERROR")
                except (httpx.HTTPError, ValueError):
                    logger.warning(
                        "Feishu Inbox-full feedback failed",
                        extra={"connector_id": intake.connector_id},
                    )

        run = asyncio.create_task(adapter.run(receive))
        try:
            await status_changed(adapter.status)
            while not run.done():
                done, _ = await asyncio.wait((run,), timeout=15)
                if not done:
                    async with short_session(self.runtime.storage) as session:
                        if not await intake_enabled(session, intake.workspace_id):
                            return
                    await lock.extend(45, replace_ttl=True)
                    await status_changed(adapter.status)
            await run
        except LockError:
            logger.warning(
                "Feishu listener lease ended", extra={"connector_id": intake.connector_id}
            )
        finally:
            run.cancel()
            await asyncio.gather(run, return_exceptions=True)
            try:
                await adapter.close()
            finally:
                await status_changed("")
                with suppress(LockError, RedisError):
                    await lock.release()
