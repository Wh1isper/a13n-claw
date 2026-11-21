"""record claw lifecycle and connector reauthorization

Revision ID: 5e4bb42a1938
Revises: 794be6141730
"""

import sqlalchemy as sa
from a13n_service.app_database import migration_name
from alembic import op

revision = "5e4bb42a1938"
down_revision = "794be6141730"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        migration_name("claws"),
        sa.Column("source_agent_revision_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        migration_name("claws"), sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        migration_name("connectors"),
        sa.Column(
            "reply_reauthorization_required",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        migration_name("schedules"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column(migration_name("schedules"), "deleted_at")
    op.drop_column(migration_name("connectors"), "reply_reauthorization_required")
    op.drop_column(migration_name("claws"), "archived_at")
    op.drop_column(migration_name("claws"), "source_agent_revision_id")
