"""Canonical metadata for the embedded product database; no Host execution tables."""

import sqlalchemy as sa

metadata = sa.MetaData()

instance = sa.Table(
    "instance",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("mode", sa.Text, nullable=False),
    sa.Column("main_thread_id", sa.Text),
    sa.Column("main_project_id", sa.Text, nullable=False),
    sa.CheckConstraint("id = 1"),
)

targets = sa.Table(
    "targets",
    metadata,
    sa.Column("key", sa.Text, primary_key=True),
    sa.Column("thread_id", sa.Text, nullable=False, unique=True),
    sa.Column("project_id", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("detail", sa.Text, nullable=False),
)

inbox = sa.Table(
    "inbox",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("connection", sa.Text, nullable=False),
    sa.Column("event_id", sa.Text, nullable=False),
    sa.Column("fingerprint", sa.Text, nullable=False),
    sa.Column("channel_id", sa.Text),
    sa.Column("mode", sa.Text, nullable=False),
    sa.Column("project_id", sa.Text),
    sa.Column("thread_id", sa.Text),
    sa.Column("body", sa.Text),
    sa.Column("disposition", sa.Text, nullable=False),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("reason", sa.Text, nullable=False),
    sa.Column("blocked", sa.Text),
    sa.Column("next_check", sa.Float, nullable=False),
    sa.Column("dependency", sa.Text),
    sa.Column("created", sa.Float, nullable=False),
    sa.UniqueConstraint("connection", "event_id"),
)

attempts = sa.Table(
    "attempts",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("thread_id", sa.Text, nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("receipt_id", sa.Text),
    sa.Column("evidence", sa.Text, nullable=False),
    sa.Column("created", sa.Float, nullable=False),
)
sa.Table(
    "attempt_items",
    metadata,
    sa.Column("attempt_id", sa.Text, sa.ForeignKey("attempts.id"), primary_key=True),
    sa.Column("item_id", sa.Text, sa.ForeignKey("inbox.id"), primary_key=True),
)

sa.Table(
    "deliveries",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("request_key", sa.Text, nullable=False, unique=True),
    sa.Column("channel_id", sa.Text, nullable=False),
    sa.Column("destination", sa.Text, nullable=False),
    sa.Column("thread_id", sa.Text),
    sa.Column("item_id", sa.Text, sa.ForeignKey("inbox.id")),
    sa.Column("body", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("evidence", sa.Text, nullable=False),
    sa.Column("created", sa.Float, nullable=False),
)

sa.Table(
    "resolutions",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("subject", sa.Text, nullable=False),
    sa.Column("action", sa.Text, nullable=False),
    sa.Column("evidence", sa.Text, nullable=False),
    sa.Column("created", sa.Float, nullable=False),
)

# Schedule definitions are file-owned; these rows retain clocks and captured occurrences.
sa.Table(
    "schedule_state",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("revision", sa.Text, nullable=False),
    sa.Column("next_due", sa.Float),
)
sa.Table(
    "occurrences",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("schedule_id", sa.Text, nullable=False),
    sa.Column("request_key", sa.Text, nullable=False, unique=True),
    sa.Column("definition", sa.Text, nullable=False),
    sa.Column("destination", sa.Text),
    sa.Column("due", sa.Float, nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("thread_id", sa.Text),
    sa.Column("receipt_id", sa.Text),
    sa.Column("detail", sa.Text, nullable=False),
    sa.Column("result", sa.Text),
    sa.Column("next_check", sa.Float, nullable=False),
    sa.Column("created", sa.Float, nullable=False),
)
sa.Table(
    "job_deliveries",
    metadata,
    sa.Column("delivery_id", sa.Text, sa.ForeignKey("deliveries.id"), primary_key=True),
    sa.Column("occurrence_id", sa.Text, sa.ForeignKey("occurrences.id"), nullable=False),
)

# Polling is durable product intake, independent of Host execution and settlement.
sa.Table(
    "github_poll_state",
    metadata,
    sa.Column("connection", sa.Text, primary_key=True),
    sa.Column("account", sa.Text, nullable=False),
    sa.Column("cursor", sa.Float, nullable=False),
    sa.Column("next_poll", sa.Float, nullable=False),
    sa.Column("error", sa.Text, nullable=False),
)
sa.Table(
    "github_events",
    metadata,
    sa.Column("connection", sa.Text, primary_key=True),
    sa.Column("event_key", sa.Text, primary_key=True),
    sa.Column("notification_id", sa.Text, nullable=False),
    sa.Column("updated", sa.Float, nullable=False),
    sa.Column("next_check", sa.Float, nullable=False),
    sa.Column("notification", sa.Text, nullable=False),
    sa.Column("message", sa.Text),
    sa.Column("state", sa.Text, nullable=False),
    sa.Column("read_status", sa.Text, nullable=False),
    sa.Column("detail", sa.Text, nullable=False),
)

# Service feedback does not settle or block business execution when transport is unknown.
sa.Table(
    "feedback_deliveries",
    metadata,
    sa.Column("delivery_id", sa.Text, sa.ForeignKey("deliveries.id"), primary_key=True),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("continuation_id", sa.Text),
)
