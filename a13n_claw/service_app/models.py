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
    worker_agents: Mapped[list[dict[str, str | None]]] = mapped_column(
        JSON, server_default=text("'[]'::json")
    )
    worker_agent_revisions: Mapped[dict[str, str]] = mapped_column(
        JSON, server_default=text("'{}'::json")
    )
    shell_review: Mapped[dict[str, Any]] = mapped_column(JSON, server_default=text("'{}'::json"))
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


class Schedule(Base):
    __tablename__ = database.name("schedules")
    __table_args__ = (
        Index(database.name("schedule_due_idx"), "claw_id", "next_at", "id"),
        UniqueConstraint("claw_id", "request_key", name=database.name("schedule_request_uq")),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    claw_id: Mapped[str] = mapped_column(
        ForeignKey(f"{database.name('claws')}.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(200))
    prompt: Mapped[str] = mapped_column(Text)
    target: Mapped[str] = mapped_column(String(16), server_default=text("'main'"))
    request_key: Mapped[str | None] = mapped_column(String(128))
    request_digest: Mapped[str | None] = mapped_column(String(64))
    cron: Mapped[str | None] = mapped_column(String(128))
    timezone: Mapped[str] = mapped_column(String(100), default="UTC")
    lateness_seconds: Mapped[int] = mapped_column(default=300)
    next_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
    target: Mapped[str] = mapped_column(String(16), server_default=text("'main'"))
    error: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32))
    item_id: Mapped[str | None] = mapped_column(String(72), unique=True)
    session_id: Mapped[str | None] = mapped_column(String(64))
    thread_id: Mapped[str | None] = mapped_column(String(64))
    run_id: Mapped[str | None] = mapped_column(String(72), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GitHubPollState(Base):
    """A completed provider scan checkpoint, independent of listener ownership."""

    __tablename__ = database.name("github_poll_state")

    connector_id: Mapped[str] = mapped_column(
        ForeignKey(f"{database.name('connectors')}.id", ondelete="CASCADE"), primary_key=True
    )
    cursor: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_modified: Mapped[str | None] = mapped_column(String(200))
    server_poll_interval: Mapped[int] = mapped_column(server_default=text("0"))
    next_poll_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class GitHubReceipt(Base):
    """Retained admission identity and retryable provider feedback for a source version."""

    __tablename__ = database.name("github_receipts")
    __table_args__ = (
        Index(database.name("github_receipt_pending_idx"), "connector_id", "reaction_state"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    connector_id: Mapped[str] = mapped_column(
        ForeignKey(f"{database.name('connectors')}.id", ondelete="CASCADE"), index=True
    )
    source_key: Mapped[str] = mapped_column(String(200))
    reaction_url: Mapped[str] = mapped_column(Text)
    reaction_state: Mapped[str] = mapped_column(String(16), server_default=text("'pending'"))
    thread_id: Mapped[str] = mapped_column(String(200))
    mark_read_state: Mapped[str] = mapped_column(String(16), server_default=text("'disabled'"))
    feedback_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LarkRetryCard(Base):
    """A durable card delivery and one retry admission for a failed Main Run."""

    __tablename__ = database.name("lark_retry_cards")
    __table_args__ = (
        UniqueConstraint(
            "claw_id", "run_id", "connector_id", "chat_id", name=database.name("lark_retry_card_uq")
        ),
        Index(database.name("lark_retry_card_pending_idx"), "claw_id", "state"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    claw_id: Mapped[str] = mapped_column(
        ForeignKey(f"{database.name('claws')}.id", ondelete="CASCADE")
    )
    connector_id: Mapped[str] = mapped_column(String(72))
    run_id: Mapped[str] = mapped_column(String(72))
    chat_id: Mapped[str] = mapped_column(String(200))
    source_message_id: Mapped[str] = mapped_column(String(200))
    is_group: Mapped[bool] = mapped_column(server_default=text("false"))
    reply_in_thread: Mapped[bool] = mapped_column(server_default=text("false"))
    token: Mapped[dict[str, str]] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(24), server_default=text("'pending'"))
    card_message_id: Mapped[str | None] = mapped_column(String(200))
    send_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    admitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    patch_pending: Mapped[bool] = mapped_column(server_default=text("false"))
    delivery_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConnectorEgress(Base):
    """Durable credential generation, including revocation after connector deletion."""

    __tablename__ = database.name("connector_egress")
    __table_args__ = (
        Index(database.name("egress_claw_state_idx"), "claw_id", "state", "created_at"),
        Index(database.name("egress_connector_idx"), "connector_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(72), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64))
    claw_id: Mapped[str] = mapped_column(String(64))
    connector_id: Mapped[str] = mapped_column(String(72))
    kind: Mapped[str] = mapped_column(String(16))
    domain: Mapped[str | None] = mapped_column(String(200))
    state: Mapped[str] = mapped_column(String(24))
    credential_id: Mapped[str | None] = mapped_column(String(64))
    actor_id: Mapped[str] = mapped_column(String(64))
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure: Mapped[str | None] = mapped_column(String(200))
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
    egress_primary: Mapped[bool] = mapped_column(server_default=text("false"))
    enabled: Mapped[bool] = mapped_column(default=True)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
