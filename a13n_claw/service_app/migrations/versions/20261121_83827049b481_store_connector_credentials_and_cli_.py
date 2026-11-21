"""Store connector credentials and CLI maintenance

Revision ID: 83827049b481
Revises: 7d73bb4ff773
"""

import sqlalchemy as sa
from a13n_service.app_database import migration_name
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "83827049b481"
down_revision = "7d73bb4ff773"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(migration_name("claws"), sa.Column("cli_versions", sa.JSON(), nullable=True))
    op.add_column(
        migration_name("claws"), sa.Column("cli_update_id", sa.String(length=64), nullable=True)
    )
    op.add_column(
        migration_name("claws"), sa.Column("cli_update_state", sa.String(length=16), nullable=True)
    )
    op.drop_column(migration_name("claws"), "cli_observation")
    op.drop_column(migration_name("claws"), "cli_checked_at")
    op.add_column(migration_name("connectors"), sa.Column("credential", sa.JSON(), nullable=True))
    op.add_column(
        migration_name("connectors"),
        sa.Column("reply_environment_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column(migration_name("connectors"), "reply_environment_id")
    op.drop_column(migration_name("connectors"), "credential")
    op.add_column(
        migration_name("claws"),
        sa.Column(
            "cli_checked_at",
            postgresql.TIMESTAMP(timezone=True),
            autoincrement=False,
            nullable=True,
        ),
    )
    op.add_column(
        migration_name("claws"),
        sa.Column(
            "cli_observation",
            postgresql.JSON(astext_type=sa.Text()),
            autoincrement=False,
            nullable=True,
        ),
    )
    op.drop_column(migration_name("claws"), "cli_update_state")
    op.drop_column(migration_name("claws"), "cli_update_id")
    op.drop_column(migration_name("claws"), "cli_versions")
