"""Allow project setup before environment binding

Revision ID: 7d73bb4ff773
Revises: bc17a4144a8e
"""

import sqlalchemy as sa
from a13n_service.app_database import migration_name
from alembic import op

revision = "7d73bb4ff773"
down_revision = "bc17a4144a8e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        migration_name("claws"),
        "environment_id",
        existing_type=sa.VARCHAR(length=64),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        migration_name("claws"),
        "environment_id",
        existing_type=sa.VARCHAR(length=64),
        nullable=False,
    )
