"""initial claw schema

Revision ID: fd68a54911de
Revises:
"""

import sqlalchemy as sa
from a13n_service.app_database import migration_name
from alembic import op

revision = "fd68a54911de"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        migration_name("connector_egress"),
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("workspace_id", sa.String(length=64), nullable=False),
        sa.Column("claw_id", sa.String(length=64), nullable=False),
        sa.Column("connector_id", sa.String(length=72), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("domain", sa.String(length=200), nullable=True),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("credential_id", sa.String(length=64), nullable=True),
        sa.Column("actor_id", sa.String(length=64), nullable=False),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refresh_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(migration_name("connector_egress__pk"))),
    )
    op.create_index(
        migration_name("egress_claw_state_idx"),
        migration_name("connector_egress"),
        ["claw_id", "state", "created_at"],
        unique=False,
    )
    op.create_index(
        migration_name("egress_connector_idx"),
        migration_name("connector_egress"),
        ["connector_id", "created_at"],
        unique=False,
    )
    op.create_table(
        migration_name("claws"),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("agent_id", sa.String(length=64), nullable=False),
        sa.Column("agent_revision_id", sa.String(length=64), nullable=True),
        sa.Column("source_agent_revision_id", sa.String(length=64), nullable=True),
        sa.Column("environment_id", sa.String(length=64), nullable=True),
        sa.Column("memory_space_id", sa.String(length=64), nullable=False),
        sa.Column("browser_id", sa.String(length=64), nullable=True),
        sa.Column("service_account_id", sa.String(length=64), nullable=False),
        sa.Column("main_thread_id", sa.String(length=64), nullable=True),
        sa.Column("main_session_id", sa.String(length=64), nullable=True),
        sa.Column("schedule_session_id", sa.String(length=64), nullable=True),
        sa.Column("worker_models", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False),
        sa.Column("worker_agents", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False),
        sa.Column(
            "worker_agent_revisions",
            sa.JSON(),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
        sa.Column("shell_review", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False),
        sa.Column("cli_versions", sa.JSON(), nullable=True),
        sa.Column("cli_update_id", sa.String(length=64), nullable=True),
        sa.Column("cli_update_state", sa.String(length=16), nullable=True),
        sa.Column("cli_update_error", sa.String(length=512), nullable=True),
        sa.Column("heartbeat_seconds", sa.Integer(), server_default=sa.text("300"), nullable=False),
        sa.Column("unread_limit", sa.Integer(), server_default=sa.text("500"), nullable=False),
        sa.Column("read_limit", sa.Integer(), server_default=sa.text("3000"), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "read_limit BETWEEN 1 AND 10000",
            name=op.f(migration_name("claws__ck_") + migration_name("read_limit_ck")),
        ),
        sa.CheckConstraint(
            "unread_limit BETWEEN 1 AND 10000",
            name=op.f(migration_name("claws__ck_") + migration_name("unread_limit_ck")),
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["workspaces.id"], name=op.f(migration_name("claws__fk_workspace_id"))
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(migration_name("claws__pk"))),
        sa.UniqueConstraint(
            "main_thread_id", name=op.f(migration_name("claws__uq_main_thread_id"))
        ),
    )
    op.create_index(
        migration_name("claw_feed_idx"),
        migration_name("claws"),
        ["workspace_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        op.f(migration_name("claws__ix_workspace_id")),
        migration_name("claws"),
        ["workspace_id"],
        unique=False,
    )
    op.create_table(
        migration_name("connectors"),
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("claw_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("credential", sa.JSON(), nullable=True),
        sa.Column("egress_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["claw_id"],
            [migration_name("claws") + ".id"],
            name=op.f(migration_name("connectors__fk_claw_id")),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(migration_name("connectors__pk"))),
        sa.UniqueConstraint("claw_id", "name", name=migration_name("connector_name_uq")),
    )
    op.create_table(
        migration_name("deliveries"),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("claw_id", sa.String(length=64), nullable=False),
        sa.Column("item_id", sa.String(length=64), nullable=True),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=True),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("run_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["claw_id"],
            [migration_name("claws") + ".id"],
            name=op.f(migration_name("deliveries__fk_claw_id")),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(migration_name("deliveries__pk"))),
    )
    op.create_index(
        migration_name("delivery_feed_idx"),
        migration_name("deliveries"),
        ["claw_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        migration_name("delivery_item_idx"),
        migration_name("deliveries"),
        ["claw_id", "item_id"],
        unique=False,
    )
    op.create_table(
        migration_name("inbox_items"),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("claw_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("source_key", sa.String(length=200), nullable=False),
        sa.Column("source", sa.JSON(), nullable=False),
        sa.Column("preview", sa.String(length=500), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["claw_id"],
            [migration_name("claws") + ".id"],
            name=op.f(migration_name("inbox_items__fk_claw_id")),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(migration_name("inbox_items__pk"))),
        sa.UniqueConstraint("claw_id", "source_key", name=migration_name("inbox_source_uq")),
    )
    op.create_index(
        migration_name("inbox_read_idx"),
        migration_name("inbox_items"),
        ["claw_id", "read_at", "id"],
        unique=False,
        postgresql_where=sa.text("read_at IS NOT NULL"),
    )
    op.create_index(
        migration_name("inbox_unread_idx"),
        migration_name("inbox_items"),
        ["claw_id", "created_at", "id"],
        unique=False,
        postgresql_where=sa.text("read_at IS NULL"),
    )
    op.create_table(
        migration_name("lark_retry_cards"),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("claw_id", sa.String(length=64), nullable=False),
        sa.Column("connector_id", sa.String(length=72), nullable=False),
        sa.Column("run_id", sa.String(length=72), nullable=False),
        sa.Column("chat_id", sa.String(length=200), nullable=False),
        sa.Column("source_message_id", sa.String(length=200), nullable=False),
        sa.Column("is_group", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("reply_in_thread", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("token", sa.JSON(), nullable=False),
        sa.Column(
            "state", sa.String(length=24), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column("card_message_id", sa.String(length=200), nullable=True),
        sa.Column("send_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("admitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("patch_pending", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("delivery_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["claw_id"],
            [migration_name("claws") + ".id"],
            name=op.f(migration_name("lark_retry_cards__fk_claw_id")),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(migration_name("lark_retry_cards__pk"))),
        sa.UniqueConstraint(
            "claw_id",
            "run_id",
            "connector_id",
            "chat_id",
            name=migration_name("lark_retry_card_uq"),
        ),
    )
    op.create_index(
        migration_name("lark_retry_card_pending_idx"),
        migration_name("lark_retry_cards"),
        ["claw_id", "state"],
        unique=False,
    )
    op.create_table(
        migration_name("schedules"),
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("claw_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("cron", sa.String(length=128), nullable=True),
        sa.Column("timezone", sa.String(length=100), nullable=False),
        sa.Column("lateness_seconds", sa.Integer(), nullable=False),
        sa.Column("next_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["claw_id"],
            [migration_name("claws") + ".id"],
            name=op.f(migration_name("schedules__fk_claw_id")),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(migration_name("schedules__pk"))),
    )
    op.create_index(
        migration_name("schedule_due_idx"),
        migration_name("schedules"),
        ["claw_id", "next_at", "id"],
        unique=False,
    )
    op.create_table(
        migration_name("github_poll_state"),
        sa.Column("connector_id", sa.String(length=72), nullable=False),
        sa.Column("cursor", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_modified", sa.String(length=200), nullable=True),
        sa.Column(
            "server_poll_interval", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("next_poll_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["connector_id"],
            [migration_name("connectors") + ".id"],
            name=op.f(migration_name("github_poll_state__fk_connector_id")),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("connector_id", name=op.f(migration_name("github_poll_state__pk"))),
    )
    op.create_table(
        migration_name("github_receipts"),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("connector_id", sa.String(length=72), nullable=False),
        sa.Column("source_key", sa.String(length=200), nullable=False),
        sa.Column("reaction_url", sa.Text(), nullable=False),
        sa.Column(
            "reaction_state",
            sa.String(length=16),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("thread_id", sa.String(length=200), nullable=False),
        sa.Column(
            "mark_read_state",
            sa.String(length=16),
            server_default=sa.text("'disabled'"),
            nullable=False,
        ),
        sa.Column("feedback_attempted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["connector_id"],
            [migration_name("connectors") + ".id"],
            name=op.f(migration_name("github_receipts__fk_connector_id")),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(migration_name("github_receipts__pk"))),
    )
    op.create_index(
        migration_name("github_receipt_pending_idx"),
        migration_name("github_receipts"),
        ["connector_id", "reaction_state"],
        unique=False,
    )
    op.create_index(
        op.f(migration_name("github_receipts__ix_connector_id")),
        migration_name("github_receipts"),
        ["connector_id"],
        unique=False,
    )
    op.create_table(
        migration_name("occurrences"),
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("schedule_id", sa.String(length=72), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("item_id", sa.String(length=72), nullable=True),
        sa.Column("run_id", sa.String(length=72), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["schedule_id"],
            [migration_name("schedules") + ".id"],
            name=op.f(migration_name("occurrences__fk_schedule_id")),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(migration_name("occurrences__pk"))),
        sa.UniqueConstraint("item_id", name=op.f(migration_name("occurrences__uq_item_id"))),
        sa.UniqueConstraint(
            "schedule_id", "scheduled_at", name=migration_name("occurrence_time_uq")
        ),
    )
    op.create_index(
        migration_name("occurrence_open_idx"),
        migration_name("occurrences"),
        ["schedule_id"],
        unique=False,
        postgresql_where=sa.text("status IN ('unread', 'processing')"),
    )
    op.create_index(
        op.f(migration_name("occurrences__ix_run_id")),
        migration_name("occurrences"),
        ["run_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f(migration_name("occurrences__ix_run_id")), table_name=migration_name("occurrences")
    )
    op.drop_index(
        migration_name("occurrence_open_idx"),
        table_name=migration_name("occurrences"),
        postgresql_where=sa.text("status IN ('unread', 'processing')"),
    )
    op.drop_table(migration_name("occurrences"))
    op.drop_index(
        op.f(migration_name("github_receipts__ix_connector_id")),
        table_name=migration_name("github_receipts"),
    )
    op.drop_index(
        migration_name("github_receipt_pending_idx"), table_name=migration_name("github_receipts")
    )
    op.drop_table(migration_name("github_receipts"))
    op.drop_table(migration_name("github_poll_state"))
    op.drop_index(migration_name("schedule_due_idx"), table_name=migration_name("schedules"))
    op.drop_table(migration_name("schedules"))
    op.drop_index(
        migration_name("lark_retry_card_pending_idx"), table_name=migration_name("lark_retry_cards")
    )
    op.drop_table(migration_name("lark_retry_cards"))
    op.drop_index(
        migration_name("inbox_unread_idx"),
        table_name=migration_name("inbox_items"),
        postgresql_where=sa.text("read_at IS NULL"),
    )
    op.drop_index(
        migration_name("inbox_read_idx"),
        table_name=migration_name("inbox_items"),
        postgresql_where=sa.text("read_at IS NOT NULL"),
    )
    op.drop_table(migration_name("inbox_items"))
    op.drop_index(migration_name("delivery_item_idx"), table_name=migration_name("deliveries"))
    op.drop_index(migration_name("delivery_feed_idx"), table_name=migration_name("deliveries"))
    op.drop_table(migration_name("deliveries"))
    op.drop_table(migration_name("connectors"))
    op.drop_index(
        op.f(migration_name("claws__ix_workspace_id")), table_name=migration_name("claws")
    )
    op.drop_index(migration_name("claw_feed_idx"), table_name=migration_name("claws"))
    op.drop_table(migration_name("claws"))
    op.drop_index(
        migration_name("egress_connector_idx"), table_name=migration_name("connector_egress")
    )
    op.drop_index(
        migration_name("egress_claw_state_idx"), table_name=migration_name("connector_egress")
    )
    op.drop_table(migration_name("connector_egress"))
