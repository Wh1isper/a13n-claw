"""Create Claw Inbox and delivery history

Revision ID: c6bad727f3eb
Revises:
"""

import sqlalchemy as sa
from a13n_service.app_database import migration_name
from alembic import op

revision = "c6bad727f3eb"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        migration_name("claws"),
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("workspace_id", sa.String(length=72), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("agent_id", sa.String(length=64), nullable=False),
        sa.Column("environment_id", sa.String(length=64), nullable=False),
        sa.Column("memory_space_id", sa.String(length=64), nullable=False),
        sa.Column("browser_id", sa.String(length=64), nullable=True),
        sa.Column("service_account_id", sa.String(length=64), nullable=False),
        sa.Column("main_thread_id", sa.String(length=64), nullable=True),
        sa.Column("main_session_id", sa.String(length=64), nullable=True),
        sa.Column("heartbeat_seconds", sa.Integer(), server_default=sa.text("300"), nullable=False),
        sa.Column("unread_limit", sa.Integer(), server_default=sa.text("500"), nullable=False),
        sa.Column("read_limit", sa.Integer(), server_default=sa.text("3000"), nullable=False),
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
        op.f(migration_name("claws__ix_workspace_id")),
        migration_name("claws"),
        ["workspace_id"],
        unique=False,
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


def downgrade() -> None:
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
    op.drop_index(migration_name("delivery_feed_idx"), table_name=migration_name("deliveries"))
    op.drop_table(migration_name("deliveries"))
    op.drop_index(
        op.f(migration_name("claws__ix_workspace_id")), table_name=migration_name("claws")
    )
    op.drop_table(migration_name("claws"))
