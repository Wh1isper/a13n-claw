"""add worker scheduling and connector state

Revision ID: f0ec035b2916
Revises: c6bad727f3eb
"""

import sqlalchemy as sa
from a13n_service.app_database import migration_name
from alembic import op

revision = "f0ec035b2916"
down_revision = "c6bad727f3eb"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        migration_name("connectors"),
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("claw_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
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
        migration_name("schedules"),
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("claw_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("cron", sa.String(length=128), nullable=True),
        sa.Column("timezone", sa.String(length=100), nullable=False),
        sa.Column("lateness_seconds", sa.Integer(), nullable=False),
        sa.Column("next_at", sa.DateTime(timezone=True), nullable=True),
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
        migration_name("workers"),
        sa.Column("thread_id", sa.String(length=72), nullable=False),
        sa.Column("claw_id", sa.String(length=64), nullable=False),
        sa.Column("session_id", sa.String(length=72), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("model", sa.String(length=200), nullable=True),
        sa.Column("last_notice_run_id", sa.String(length=72), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["claw_id"],
            [migration_name("claws") + ".id"],
            name=op.f(migration_name("workers__fk_claw_id")),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("thread_id", name=op.f(migration_name("workers__pk"))),
    )
    op.create_index(
        migration_name("worker_feed_idx"),
        migration_name("workers"),
        ["claw_id", "created_at", "thread_id"],
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
    op.create_table(
        migration_name("steers"),
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("thread_id", sa.String(length=72), nullable=False),
        sa.Column("run_id", sa.String(length=72), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            [migration_name("workers") + ".thread_id"],
            name=op.f(migration_name("steers__fk_thread_id")),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(migration_name("steers__pk"))),
    )
    op.create_index(
        migration_name("steer_run_idx"),
        migration_name("steers"),
        ["run_id", "created_at", "id"],
        unique=False,
    )
    op.create_index(
        op.f(migration_name("steers__ix_thread_id")),
        migration_name("steers"),
        ["thread_id"],
        unique=False,
    )
    op.create_index(
        migration_name("claw_feed_idx"),
        migration_name("claws"),
        ["workspace_id", "created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(migration_name("claw_feed_idx"), table_name=migration_name("claws"))
    op.drop_index(op.f(migration_name("steers__ix_thread_id")), table_name=migration_name("steers"))
    op.drop_index(migration_name("steer_run_idx"), table_name=migration_name("steers"))
    op.drop_table(migration_name("steers"))
    op.drop_index(
        op.f(migration_name("occurrences__ix_run_id")), table_name=migration_name("occurrences")
    )
    op.drop_index(
        migration_name("occurrence_open_idx"),
        table_name=migration_name("occurrences"),
        postgresql_where=sa.text("status IN ('unread', 'processing')"),
    )
    op.drop_table(migration_name("occurrences"))
    op.drop_index(migration_name("worker_feed_idx"), table_name=migration_name("workers"))
    op.drop_table(migration_name("workers"))
    op.drop_index(migration_name("schedule_due_idx"), table_name=migration_name("schedules"))
    op.drop_table(migration_name("schedules"))
    op.drop_table(migration_name("connectors"))
