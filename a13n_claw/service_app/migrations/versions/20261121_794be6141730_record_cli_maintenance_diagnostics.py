"""Record CLI maintenance diagnostics

Revision ID: 794be6141730
Revises: 83827049b481
"""

import sqlalchemy as sa
from a13n_service.app_database import migration_name
from alembic import op

revision = "794be6141730"
down_revision = "83827049b481"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        migration_name("claws"), sa.Column("cli_update_error", sa.String(length=512), nullable=True)
    )


def downgrade() -> None:
    op.drop_column(migration_name("claws"), "cli_update_error")
