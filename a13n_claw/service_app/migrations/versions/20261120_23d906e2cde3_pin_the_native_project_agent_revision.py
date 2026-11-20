"""pin the native project agent revision

Revision ID: 23d906e2cde3
Revises: a401f3a3227d
"""

import sqlalchemy as sa
from a13n_service.app_database import migration_name
from alembic import op

revision = "23d906e2cde3"
down_revision = "a401f3a3227d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        migration_name("claws"), sa.Column("agent_revision_id", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column(migration_name("claws"), "agent_revision_id")
