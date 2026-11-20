"""Fence native schedule creation requests

Revision ID: bc17a4144a8e
Revises: 45c62098f46f
"""

import sqlalchemy as sa
from a13n_service.app_database import migration_name
from alembic import op

revision = "bc17a4144a8e"
down_revision = "45c62098f46f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        migration_name("schedule_requests"),
        sa.Column("id", sa.String(length=72), nullable=False),
        sa.Column("claw_id", sa.String(length=64), nullable=False),
        sa.Column("request", sa.JSON(), nullable=True),
        sa.Column("schedule_id", sa.String(length=72), nullable=True),
        sa.Column("failure", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["claw_id"],
            [migration_name("claws") + ".id"],
            name=op.f(migration_name("schedule_requests__fk_claw_id")),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(migration_name("schedule_requests__pk"))),
    )
    op.create_index(
        migration_name("schedule_request_pending_idx"),
        migration_name("schedule_requests"),
        ["claw_id", "created_at", "id"],
        unique=False,
        postgresql_where=sa.text("completed_at IS NULL"),
    )
    op.create_index(
        migration_name("schedule_request_receipt_idx"),
        migration_name("schedule_requests"),
        ["claw_id", "completed_at"],
        unique=False,
    )
    op.add_column(
        migration_name("claws"),
        sa.Column("schedule_session_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column(migration_name("claws"), "schedule_session_id")
    op.drop_index(
        migration_name("schedule_request_receipt_idx"),
        table_name=migration_name("schedule_requests"),
    )
    op.drop_index(
        migration_name("schedule_request_pending_idx"),
        table_name=migration_name("schedule_requests"),
        postgresql_where=sa.text("completed_at IS NULL"),
    )
    op.drop_table(migration_name("schedule_requests"))
