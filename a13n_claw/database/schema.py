"""Canonical SQLite schema for migration generation; runtime SQL lives in Store."""

import sqlalchemy as sa

metadata = sa.MetaData(
    naming_convention={
        "ix": "ix_%(column_0_label)s",
        "uq": "uq_%(table_name)s_%(column_0_name)s",
        "ck": "ck_%(table_name)s_%(constraint_name)s",
        "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
        "pk": "pk_%(table_name)s",
    }
)

assets = sa.Table(
    "assets",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("thread_id", sa.Text, sa.ForeignKey("threads.id"), nullable=False),
    sa.Column("run_id", sa.Text, sa.ForeignKey("runs.id")),
    sa.Column("actor_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False),
    sa.Column("request_id", sa.Text, nullable=False),
    sa.Column("fingerprint", sa.Text, nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("name", sa.Text, nullable=False),
    sa.Column("media_type", sa.Text, nullable=False),
    sa.Column("size", sa.Integer, nullable=False),
    sa.Column("sha256", sa.Text, nullable=False),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("content", sa.LargeBinary, nullable=False),
    sa.UniqueConstraint("actor_id", "request_id"),
)

audit = sa.Table(
    "audit",
    metadata,
    sa.Column("sequence", sa.Integer, primary_key=True),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("subject_id", sa.Text, nullable=False),
    sa.Column("detail", sa.Text, nullable=False),
    sa.Column("created_at", sa.Text, nullable=False),
    sqlite_autoincrement=True,
)

automatic_runs = sa.Table(
    "automatic_runs",
    metadata,
    sa.Column("run_id", sa.Text, sa.ForeignKey("runs.id"), primary_key=True),
    sa.Column("progress", sa.Integer, nullable=False),
    sa.Column("settled", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("automatic_only", sa.Integer, nullable=False, server_default=sa.text("1")),
    sa.Column("notice_sequence", sa.Integer, nullable=False, server_default=sa.text("0")),
)

channels = sa.Table(
    "channels",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("policy", sa.Text, nullable=False),
    sa.Column("thread_id", sa.Text, sa.ForeignKey("threads.id")),
    sa.Column("active", sa.Integer, nullable=False, server_default=sa.text("1")),
    sa.Column("qualified_key", sa.Text, nullable=False),
)
sa.Index(
    "active_channel_key", channels.c.qualified_key, unique=True, sqlite_where=sa.text("active=1")
)

checkpoints = sa.Table(
    "checkpoints",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("thread_id", sa.Text, sa.ForeignKey("threads.id"), nullable=False),
    sa.Column("run_id", sa.Text, sa.ForeignKey("runs.id")),
    sa.Column("state", sa.Text, nullable=False),
    sa.Column("created_at", sa.Text, nullable=False),
)

configuration_state = sa.Table(
    "configuration_state",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("path", sa.Text, nullable=False),
    sa.Column("revision", sa.Text, nullable=False),
    sa.CheckConstraint("id=1", name="singleton"),
)

coordination = sa.Table(
    "coordination",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("mode", sa.Text, nullable=False, server_default=sa.text("'per_channel'")),
    sa.Column("main_id", sa.Text, sa.ForeignKey("threads.id")),
    sa.Column("actor_id", sa.Text, sa.ForeignKey("principals.id")),
    sa.Column("paused", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
    sa.Column("progress", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("failures", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("retry_at", sa.Text),
    sa.Column("blocked_reason", sa.Text),
    sa.CheckConstraint("id=1", name="singleton"),
)

credentials = sa.Table(
    "credentials",
    metadata,
    sa.Column("name", sa.Text, primary_key=True),
    sa.Column("value", sa.Text, nullable=False),
    sa.Column("updated_at", sa.Text, nullable=False),
)

decisions = sa.Table(
    "decisions",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("run_id", sa.Text, sa.ForeignKey("runs.id"), nullable=False),
    sa.Column("checkpoint_id", sa.Text, sa.ForeignKey("checkpoints.id"), nullable=False),
    sa.Column("requests", sa.Text, nullable=False),
    sa.Column("response", sa.Text),
    sa.Column("actor_id", sa.Text, sa.ForeignKey("principals.id")),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("answered_at", sa.Text),
)
sa.Index(
    "one_pending_decision",
    decisions.c.run_id,
    unique=True,
    sqlite_where=sa.text("response IS NULL"),
)

deferred_inputs = sa.Table(
    "deferred_inputs",
    metadata,
    sa.Column("run_id", sa.Text, sa.ForeignKey("runs.id"), primary_key=True),
    sa.Column("checkpoint_id", sa.Text, sa.ForeignKey("checkpoints.id"), nullable=False),
    sa.Column("requests", sa.Text, nullable=False),
    sa.Column("response", sa.Text, nullable=False),
    sa.Column("actor_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False),
)

delegations = sa.Table(
    "delegations",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("parent_run_id", sa.Text, sa.ForeignKey("runs.id"), nullable=False),
    sa.Column("child_run_id", sa.Text, sa.ForeignKey("runs.id"), nullable=False),
    sa.Column("request_id", sa.Text, nullable=False),
    sa.Column("fingerprint", sa.Text, nullable=False),
    sa.Column("cancel_policy", sa.Text, nullable=False),
    sa.Column("notify", sa.Integer, nullable=False),
    sa.Column("delivery_input_id", sa.Text, sa.ForeignKey("inputs.id")),
    sa.Column("delivery_error", sa.Text),
    sa.Column("delivery_attention_id", sa.Text, sa.ForeignKey("inbox.id")),
    sa.UniqueConstraint("parent_run_id", "request_id"),
    sa.UniqueConstraint("child_run_id"),
)

deliveries = sa.Table(
    "deliveries",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("thread_id", sa.Text, sa.ForeignKey("threads.id"), nullable=False),
    sa.Column("run_id", sa.Text, sa.ForeignKey("runs.id"), nullable=False),
    sa.Column("actor_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False),
    sa.Column("request_id", sa.Text, nullable=False),
    sa.Column("fingerprint", sa.Text, nullable=False),
    sa.Column("channel_id", sa.Text, sa.ForeignKey("channels.id"), nullable=False),
    sa.Column("channel_version", sa.Integer, nullable=False),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'pending'")),
    sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
    sa.Column("transport_receipt", sa.Text),
    sa.Column("error", sa.Text),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("updated_at", sa.Text, nullable=False),
    sa.UniqueConstraint("thread_id", "request_id"),
)
sa.Index("delivery_pending", deliveries.c.status)

external_assets = sa.Table(
    "external_assets",
    metadata,
    sa.Column("asset_id", sa.Text, sa.ForeignKey("assets.id"), primary_key=True),
    sa.Column("channel_id", sa.Text, sa.ForeignKey("channels.id"), primary_key=True),
    sa.Column("event_id", sa.Text, primary_key=True),
    sa.Column("sender", sa.Text, nullable=False),
    sa.Column("addressed", sa.Integer, nullable=False),
)

forks = sa.Table(
    "forks",
    metadata,
    sa.Column("actor_id", sa.Text, sa.ForeignKey("principals.id"), primary_key=True),
    sa.Column("request_id", sa.Text, primary_key=True),
    sa.Column("fingerprint", sa.Text, nullable=False),
    sa.Column("thread_id", sa.Text, sa.ForeignKey("threads.id"), nullable=False),
)

inbox = sa.Table(
    "inbox",
    metadata,
    sa.Column("sequence", sa.Integer, primary_key=True),
    sa.Column("id", sa.Text, nullable=False),
    sa.Column("main_id", sa.Text, sa.ForeignKey("threads.id"), nullable=False),
    sa.Column("origin", sa.Text, nullable=False),
    sa.Column("fingerprint", sa.Text, nullable=False),
    sa.Column("kind", sa.Text, nullable=False),
    sa.Column("payload", sa.Text, nullable=False),
    sa.Column("source_thread_id", sa.Text, sa.ForeignKey("threads.id")),
    sa.Column("source_run_id", sa.Text, sa.ForeignKey("runs.id")),
    sa.Column("channel_id", sa.Text, sa.ForeignKey("channels.id")),
    sa.Column("input_id", sa.Text, sa.ForeignKey("inputs.id")),
    sa.Column("disposition", sa.Text, nullable=False, server_default=sa.text("'pending'")),
    sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
    sa.Column("note", sa.Text, nullable=False, server_default=sa.text("''")),
    sa.Column("dependency_run_id", sa.Text, sa.ForeignKey("runs.id")),
    sa.Column("due_at", sa.Text),
    sa.Column("human_key", sa.Text),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("updated_at", sa.Text, nullable=False),
    sa.UniqueConstraint("main_id", "origin"),
    sa.UniqueConstraint("id"),
    sqlite_autoincrement=True,
)
sa.Index("inbox_actionable", inbox.c.main_id, inbox.c.disposition, inbox.c.sequence)

ingress = sa.Table(
    "ingress",
    metadata,
    sa.Column("channel_id", sa.Text, sa.ForeignKey("channels.id"), primary_key=True),
    sa.Column("event_id", sa.Text, primary_key=True),
    sa.Column("fingerprint", sa.Text, nullable=False),
    sa.Column("input_id", sa.Text, sa.ForeignKey("inputs.id")),
    sa.Column("inbox_id", sa.Text, sa.ForeignKey("inbox.id")),
    # Neither reference means the event was filtered before admission.
    sa.CheckConstraint("input_id IS NULL OR inbox_id IS NULL", name="one_destination"),
)

inputs = sa.Table(
    "inputs",
    metadata,
    sa.Column("sequence", sa.Integer, primary_key=True),
    sa.Column("id", sa.Text, nullable=False),
    sa.Column("thread_id", sa.Text, sa.ForeignKey("threads.id"), nullable=False),
    sa.Column("run_id", sa.Text, sa.ForeignKey("runs.id"), nullable=False),
    sa.Column("actor_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False),
    sa.Column("request_id", sa.Text, nullable=False),
    sa.Column("fingerprint", sa.Text, nullable=False),
    sa.Column("text", sa.Text, nullable=False),
    sa.Column("source", sa.Text, nullable=False),
    sa.Column("disposition", sa.Text, nullable=False),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("attachment_ids", sa.Text, nullable=False, server_default=sa.text("'[]'")),
    # Host-owned provenance, independent of the caller's source label.
    sa.Column("automatic", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.UniqueConstraint("actor_id", "request_id"),
    sa.UniqueConstraint("id"),
    sqlite_autoincrement=True,
)
sa.Index("inputs_run_order", inputs.c.run_id, inputs.c.sequence)

principals = sa.Table(
    "principals",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("token_hash", sa.Text, nullable=False),
    sa.Column("content", sa.Text, nullable=False),
    sa.UniqueConstraint("token_hash"),
)

recoveries = sa.Table(
    "recoveries",
    metadata,
    sa.Column("actor_id", sa.Text, sa.ForeignKey("principals.id"), primary_key=True),
    sa.Column("request_id", sa.Text, primary_key=True),
    sa.Column("fingerprint", sa.Text, nullable=False),
    sa.Column("run_id", sa.Text, sa.ForeignKey("runs.id"), nullable=False),
)

resources = sa.Table(
    "resources",
    metadata,
    sa.Column("kind", sa.Text, primary_key=True),
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("version", sa.Integer, nullable=False),
    sa.Column("content", sa.Text, nullable=False),
    sa.Column("retired", sa.Integer, nullable=False, server_default=sa.text("0")),
)

runs = sa.Table(
    "runs",
    metadata,
    sa.Column("sequence", sa.Integer, primary_key=True),
    sa.Column("id", sa.Text, nullable=False),
    sa.Column("thread_id", sa.Text, sa.ForeignKey("threads.id"), nullable=False),
    sa.Column("actor_id", sa.Text, sa.ForeignKey("principals.id"), nullable=False),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("composition", sa.Text, nullable=False),
    sa.Column("checkpoint_id", sa.Text, sa.ForeignKey("checkpoints.id", use_alter=True)),
    sa.Column("recovery_of", sa.Text, sa.ForeignKey("runs.id")),
    sa.Column("recovery_required", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("reconciled_at", sa.Text),
    sa.Column("cancel_requested", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("output", sa.Text),
    sa.Column("error", sa.Text),
    sa.Column("owner", sa.Text),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("finished_at", sa.Text),
    sa.UniqueConstraint("id"),
    sqlite_autoincrement=True,
)
sa.Index(
    "one_thread_owner",
    runs.c.thread_id,
    unique=True,
    sqlite_where=sa.text("status IN ('preparing', 'running', 'waiting')"),
)
sa.Index("runs_thread_order", runs.c.thread_id, runs.c.sequence)

targets = sa.Table(
    "targets",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("thread_id", sa.Text, sa.ForeignKey("threads.id"), nullable=False),
    sa.Column("generation", sa.Text, nullable=False),
    sa.Column("definition", sa.Text, nullable=False),
    sa.Column("provider_state", sa.Text),
    sa.Column("status", sa.Text, nullable=False),
    sa.Column("operation_id", sa.Text),
    sa.Column("error", sa.Text),
    sa.Column("updated_at", sa.Text, nullable=False),
    sa.UniqueConstraint("thread_id", "generation"),
)

thread_creations = sa.Table(
    "thread_creations",
    metadata,
    sa.Column("actor_id", sa.Text, sa.ForeignKey("principals.id"), primary_key=True),
    sa.Column("request_id", sa.Text, primary_key=True),
    sa.Column("fingerprint", sa.Text, nullable=False),
    sa.Column("thread_id", sa.Text, sa.ForeignKey("threads.id"), nullable=False),
)

threads = sa.Table(
    "threads",
    metadata,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("title", sa.Text, nullable=False),
    sa.Column("profile_id", sa.Text, nullable=False),
    sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
    sa.Column("checkpoint_id", sa.Text, sa.ForeignKey("checkpoints.id", use_alter=True)),
    sa.Column("parent_thread_id", sa.Text, sa.ForeignKey("threads.id")),
    sa.Column("parent_run_id", sa.Text, sa.ForeignKey("runs.id", use_alter=True)),
    sa.Column("fork_checkpoint_id", sa.Text, sa.ForeignKey("checkpoints.id", use_alter=True)),
    sa.Column("archived", sa.Integer, nullable=False, server_default=sa.text("0")),
    sa.Column("created_at", sa.Text, nullable=False),
    sa.Column("active", sa.Integer, nullable=False, server_default=sa.text("1")),
    sa.Column("owner_main_id", sa.Text, sa.ForeignKey("threads.id")),
)

worker_creations = sa.Table(
    "worker_creations",
    metadata,
    sa.Column("main_id", sa.Text, sa.ForeignKey("threads.id"), primary_key=True),
    sa.Column("request_id", sa.Text, primary_key=True),
    sa.Column("fingerprint", sa.Text, nullable=False),
    sa.Column("worker_id", sa.Text, sa.ForeignKey("threads.id"), nullable=False),
    sa.Column("input_id", sa.Text, sa.ForeignKey("inputs.id"), nullable=False),
)
