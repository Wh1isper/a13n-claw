"""Observe Project reply readiness

Revision ID: 45c62098f46f
Revises: 23d906e2cde3
"""

import sqlalchemy as sa
from a13n_service.app_database import migration_name
from alembic import op

revision = "45c62098f46f"
down_revision = "23d906e2cde3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(migration_name("claws"), sa.Column("cli_observation", sa.JSON(), nullable=True))
    op.add_column(
        migration_name("claws"),
        sa.Column("cli_checked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column(migration_name("claws"), "cli_checked_at")
    op.drop_column(migration_name("claws"), "cli_observation")
