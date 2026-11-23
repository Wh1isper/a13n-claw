"""Short, tenant-scoped transactions for Inbox and delivery history."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Literal
from uuid import uuid4

from a13n_service.infra.db import Storage, after_commit, now, short_session, transaction
from a13n_service.infra.errors import ServiceError
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Claw, Delivery, InboxItem, Occurrence, ScheduleRequest

HISTORY_AGE = timedelta(days=7)
DELIVERY_LIMIT = 3000


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


@dataclass(frozen=True, slots=True)
class Offer:
    kind: Literal["user", "worker", "worker_terminal", "schedule"]
    origin: str
    source_key: str
    source: dict[str, Any]
    content: str
    preview: str
    volatile_source_fields: tuple[str, ...] = ()
    legacy_optional_source_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Admission:
    status: Literal["accepted", "duplicate", "full", "dropped"]
    item_id: str | None = None
    delivery_id: str | None = None


class ClawNotFound(LookupError):
    """The selected Claw is outside the scoped workspace or does not exist."""


async def lock_claw(session: AsyncSession, workspace_id: str, claw_id: str) -> Claw:
    # One short row lock orders count + insert, open + prune, and configuration changes.
    claw = await session.scalar(
        select(Claw)
        .where(Claw.workspace_id == workspace_id, Claw.id == claw_id, Claw.archived_at.is_(None))
        .with_for_update()
    )
    if claw is None:
        raise ClawNotFound(claw_id)
    return claw


async def admit(session: AsyncSession, claw: Claw, offer: Offer) -> Admission:
    """Admit within the caller's Claw lock; schedule facts can join the same commit."""
    existing = await session.scalar(
        select(InboxItem).where(
            InboxItem.claw_id == claw.id, InboxItem.source_key == offer.source_key
        )
    )
    if existing is not None:
        ignored = set(offer.volatile_source_fields)
        ignored.update(
            key
            for key in offer.legacy_optional_source_fields
            if key not in existing.source or key not in offer.source
        )
        existing_source = {k: v for k, v in existing.source.items() if k not in ignored}
        offered_source = {k: v for k, v in offer.source.items() if k not in ignored}
        if existing.content != offer.content or existing_source != offered_source:
            raise ServiceError("conflict", "The delivery key identifies a different message")
        delivery_id = await session.scalar(
            select(Delivery.id).where(Delivery.claw_id == claw.id, Delivery.item_id == existing.id)
        )
        return Admission("duplicate", existing.id, delivery_id)
    unread = await session.scalar(
        select(func.count())
        .select_from(InboxItem)
        .where(InboxItem.claw_id == claw.id, InboxItem.read_at.is_(None))
    )
    if (unread or 0) >= claw.unread_limit:
        status = "dropped" if offer.kind == "worker_terminal" else "full"
        return Admission(
            status, delivery_id=await _record(session, claw, offer.origin, status, None)
        )
    item_id = new_id("inb")
    session.add(
        InboxItem(
            id=item_id,
            claw_id=claw.id,
            kind=offer.kind,
            source_key=offer.source_key,
            source=offer.source,
            preview=offer.preview[:500],
            content=offer.content,
        )
    )
    delivery_id = await _record(session, claw, offer.origin, "accepted", item_id)
    return Admission("accepted", item_id, delivery_id)


async def _record(
    session: AsyncSession, claw: Claw, origin: str, status: str, item_id: str | None
) -> str:
    delivery_id = new_id("dlv")
    session.add(
        Delivery(
            id=delivery_id,
            claw_id=claw.id,
            item_id=item_id,
            origin=origin,
            status=status,
            thread_id=claw.main_thread_id,
            session_id=claw.main_session_id,
        )
    )
    await session.flush()
    await _prune_deliveries(session, claw.id, await now(session))
    return delivery_id


async def _prune_deliveries(session: AsyncSession, claw_id: str, current: Any) -> None:
    # Activity retains metadata, not an additional copy of the accepted message.
    latest = select(Delivery.id).where(Delivery.claw_id == claw_id)
    overflow = latest.order_by(Delivery.created_at.desc(), Delivery.id.desc()).offset(
        DELIVERY_LIMIT
    )
    await session.execute(delete(Delivery).where(Delivery.id.in_(overflow)))
    await session.execute(
        delete(Delivery).where(
            Delivery.claw_id == claw_id, Delivery.created_at < current - HISTORY_AGE
        )
    )


async def prune_read(session: AsyncSession, claw: Claw, current: Any) -> None:
    read = select(InboxItem.id).where(InboxItem.claw_id == claw.id, InboxItem.read_at.is_not(None))
    overflow = read.order_by(InboxItem.read_at.desc(), InboxItem.id.desc()).offset(claw.read_limit)
    await session.execute(delete(InboxItem).where(InboxItem.id.in_(overflow)))
    await session.execute(
        delete(InboxItem).where(
            InboxItem.claw_id == claw.id, InboxItem.read_at < current - HISTORY_AGE
        )
    )


async def deliver(storage: Storage, workspace_id: str, claw_id: str, offer: Offer) -> Admission:
    """Store first; the caller may wake Main after this commits."""
    async with transaction(storage) as session:
        return await admit(session, await lock_claw(session, workspace_id, claw_id), offer)


async def pop(
    storage: Storage,
    workspace_id: str,
    claw_id: str,
    item_id: str,
    *,
    run_id: str | None = None,
    on_open: Callable[[InboxItem], Awaitable[None]] | None = None,
    guard: Callable[[AsyncSession, Claw], Awaitable[None]] | None = None,
) -> InboxItem | None:
    """Open a retained item once, or return the existing read item unchanged."""
    async with transaction(storage) as session:
        claw = await lock_claw(session, workspace_id, claw_id)
        if guard is not None:
            await guard(session, claw)
        current = await now(session)
        item = await session.scalar(
            select(InboxItem).where(InboxItem.claw_id == claw.id, InboxItem.id == item_id)
        )
        if item is None or (item.read_at is not None and item.read_at < current - HISTORY_AGE):
            return None
        if item.read_at is None:
            item.read_at = current
            if on_open is not None:
                after_commit(session, lambda: on_open(item))
            if run_id is not None:
                await session.execute(
                    update(Delivery)
                    .where(Delivery.claw_id == claw.id, Delivery.item_id == item.id)
                    .values(
                        thread_id=claw.main_thread_id,
                        session_id=claw.main_session_id,
                        run_id=run_id,
                    )
                )
                if item.kind == "schedule":
                    await session.execute(
                        update(Occurrence)
                        .where(Occurrence.item_id == item.id)
                        .values(status="processing", run_id=run_id)
                    )
            await session.flush()
            await prune_read(session, claw, current)
        return item


async def cleanup(storage: Storage, workspace_id: str, claw_id: str) -> None:
    async with transaction(storage) as session:
        # Retention continues after archiving; this lock grants no admission authority.
        claw = await session.scalar(
            select(Claw)
            .where(Claw.workspace_id == workspace_id, Claw.id == claw_id)
            .with_for_update()
        )
        if claw is None:
            return
        current = await now(session)
        await prune_read(session, claw, current)
        await _prune_deliveries(session, claw.id, current)
        await session.execute(
            delete(ScheduleRequest).where(
                ScheduleRequest.claw_id == claw_id,
                ScheduleRequest.completed_at < current - HISTORY_AGE,
            )
        )


async def unread_previews(
    storage: Storage,
    workspace_id: str,
    claw_id: str,
    *,
    limit: int = 100,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    query = (
        select(InboxItem.id, InboxItem.kind, InboxItem.preview, InboxItem.created_at)
        .join(Claw, Claw.id == InboxItem.claw_id)
        .where(
            Claw.id == claw_id,
            Claw.workspace_id == workspace_id,
            Claw.archived_at.is_(None),
            InboxItem.read_at.is_(None),
        )
        .order_by(InboxItem.created_at, InboxItem.id)
        .limit(limit)
    )
    if run_id is not None:
        query = query.join(Delivery, Delivery.item_id == InboxItem.id).where(
            Delivery.claw_id == claw_id, Delivery.run_id == run_id
        )
    async with short_session(storage) as session:
        return [dict(row) for row in (await session.execute(query)).mappings()]
