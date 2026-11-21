"""Claw-owned state in the Service database; native resources remain Service-owned."""

import os
from datetime import datetime
from typing import Any, cast

from a13n_service.app_database import AppDatabase
from a13n_service.tenancy.tables import WorkspaceRow
from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, query_expression

from .project_cli import UpdateState

APP_ID = "a13n.claw"
database = AppDatabase(
    APP_ID,
    migrations="a13n_claw.service_app:migrations",
    prefix=os.environ.get("CLAW_TABLE_PREFIX") or None,
)
database.reference(cast(Table, WorkspaceRow.__table__))


class Base(DeclarativeBase):
    metadata = database.metadata


class Claw(Base):
    __tablename__ = database.name("claws")
    __table_args__ = (
        Index(database.name("claw_feed_idx"), "workspace_id", "created_at", "id"),
        CheckConstraint("unread_limit BETWEEN 1 AND 10000", name=database.name("unread_limit_ck")),
        CheckConstraint("read_limit BETWEEN 1 AND 10000", name=database.name("read_limit_ck")),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    agent_id: Mapped[str] = mapped_column(String(64))
    agent_revision_id: Mapped[str | None] = mapped_column(String(64))
    source_agent_revision_id: Mapped[str | None] = mapped_column(String(64))
    environment_id: Mapped[str | None] = mapped_column(String(64))
    memory_space_id: Mapped[str] = mapped_column(String(64))
    browser_id: Mapped[str | None] = mapped_column(String(64))
    service_account_id: Mapped[str] = mapped_column(String(64))
    main_thread_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    main_session_id: Mapped[str | None] = mapped_column(String(64))
    schedule_session_id: Mapped[str | None] = mapped_column(String(64))
    worker_models: Mapped[list[dict[str, str]]] = mapped_column(
        JSON, server_default=text("'[]'::json")
    )
    cli_versions: Mapped[dict[str, str] | None] = mapped_column(JSON)
    cli_update_id: Mapped[str | None] = mapped_column(String(64))
    cli_update_state: Mapped[UpdateState | None] = mapped_column(String(16))
    cli_update_error: Mapped[str | None] = mapped_column(String(512))
    heartbeat_seconds: Mapped[int] = mapped_column(server_default=text("300"))
    unread_limit: Mapped[int] = mapped_column(server_default=text("500"))
    read_limit: Mapped[int] = mapped_column(server_default=text("3000"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InboxItem(Base):
    __tablename__ = database.name("inbox_items")
    __table_args__ = (
        UniqueConstraint("claw_id", "source_key", name=database.name("inbox_source_uq")),
        Index(
            database.name("inbox_unread_idx"),
            "claw_id",
            "created_at",
            "id",
            postgresql_where=text("read_at IS NULL"),
        ),
        Index(
            database.name("inbox_read_idx"),
            "claw_id",
            "read_at",
            "id",
            postgresql_where=text("read_at IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    claw_id: Mapped[str] = mapped_column(
        ForeignKey(f"{database.name('claws')}.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column(String(32))
    source_key: Mapped[str] = mapped_column(String(200))
    source: Mapped[dict[str, Any]] = mapped_column(JSON)
    preview: Mapped[str] = mapped_column(String(500))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Delivery(Base):
    __tablename__ = database.name("deliveries")
    __table_args__ = (
        Index(database.name("delivery_feed_idx"), "claw_id", "created_at", "id"),
        Index(database.name("delivery_item_idx"), "claw_id", "item_id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    claw_id: Mapped[str] = mapped_column(
        ForeignKey(f"{database.name('claws')}.id", ondelete="CASCADE")
    )
    item_id: Mapped[str | None] = mapped_column(String(64))
    origin: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))
    thread_id: Mapped[str | None] = mapped_column(String(64))
    session_id: Mapped[str | None] = mapped_column(String(64))
    run_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Project retained Inbox fields into the same snapshot as delivery filters.
    kind: Mapped[str | None] = query_expression()
    preview: Mapped[str | None] = query_expression()
    read_at: Mapped[datetime | None] = query_expression()


class Worker(Base):
    __tablename__ = database.name("workers")
    __table_args__ = (
        Index(database.name("worker_feed_idx"), "claw_id", "created_at", "thread_id"),
    )

    thread_id: Mapped[str] = mapped_column(String(72), primary_key=True)
    claw_id: Mapped[str] = mapped_column(
        ForeignKey(f"{database.name('claws')}.id", ondelete="CASCADE")
    )
    session_id: Mapped[str] = mapped_column(String(72))
    name: Mapped[str] = mapped_column(String(200))
    model: Mapped[str | None] = mapped_column(String(200))
    last_notice_run_id: Mapped[str | None] = mapped_column(String(72))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Steer(Base):
    __tablename__ = database.name("steers")
    __table_args__ = (Index(database.name("steer_run_idx"), "run_id", "created_at", "id"),)

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    thread_id: Mapped[str] = mapped_column(
        ForeignKey(f"{database.name('workers')}.thread_id", ondelete="CASCADE"), index=True
    )
    run_id: Mapped[str] = mapped_column(String(72))
    content: Mapped[str] = mapped_column(Text)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Schedule(Base):
    __tablename__ = database.name("schedules")
    __table_args__ = (Index(database.name("schedule_due_idx"), "claw_id", "next_at", "id"),)

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    claw_id: Mapped[str] = mapped_column(
        ForeignKey(f"{database.name('claws')}.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(200))
    prompt: Mapped[str] = mapped_column(Text)
    cron: Mapped[str | None] = mapped_column(String(128))
    timezone: Mapped[str] = mapped_column(String(100), default="UTC")
    lateness_seconds: Mapped[int] = mapped_column(default=300)
    next_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ScheduleRequest(Base):
    """A fenced request to create a Service Schedule, not a copy of its definition."""

    __tablename__ = database.name("schedule_requests")
    __table_args__ = (
        Index(
            database.name("schedule_request_pending_idx"),
            "claw_id",
            "created_at",
            "id",
            postgresql_where=text("completed_at IS NULL"),
        ),
        Index(database.name("schedule_request_receipt_idx"), "claw_id", "completed_at"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    claw_id: Mapped[str] = mapped_column(
        ForeignKey(f"{database.name('claws')}.id", ondelete="CASCADE")
    )
    request: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    schedule_id: Mapped[str | None] = mapped_column(String(72))
    failure: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Occurrence(Base):
    __tablename__ = database.name("occurrences")
    __table_args__ = (
        UniqueConstraint("schedule_id", "scheduled_at", name=database.name("occurrence_time_uq")),
        Index(
            database.name("occurrence_open_idx"),
            "schedule_id",
            postgresql_where=text("status IN ('unread', 'processing')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    schedule_id: Mapped[str] = mapped_column(
        ForeignKey(f"{database.name('schedules')}.id", ondelete="CASCADE")
    )
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32))
    item_id: Mapped[str | None] = mapped_column(String(72), unique=True)
    run_id: Mapped[str | None] = mapped_column(String(72), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Connector(Base):
    __tablename__ = database.name("connectors")
    __table_args__ = (UniqueConstraint("claw_id", "name", name=database.name("connector_name_uq")),)

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    claw_id: Mapped[str] = mapped_column(
        ForeignKey(f"{database.name('claws')}.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(32))
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    credential: Mapped[dict[str, str] | None] = mapped_column(JSON)
    reply_environment_id: Mapped[str | None] = mapped_column(String(64))
    reply_reauthorization_required: Mapped[bool] = mapped_column(server_default=text("false"))
    enabled: Mapped[bool] = mapped_column(default=True)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
