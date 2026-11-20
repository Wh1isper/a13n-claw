"""worker model guidance and delivery lookup

Revision ID: a401f3a3227d
Revises: f0ec035b2916
"""

import sqlalchemy as sa
from a13n_service.app_database import migration_name
from alembic import op

revision = "a401f3a3227d"
down_revision = "f0ec035b2916"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        migration_name("claws"),
        sa.Column("worker_models", sa.JSON(), server_default=sa.text("'[]'::json"), nullable=False),
    )
    op.create_index(
        migration_name("delivery_item_idx"),
        migration_name("deliveries"),
        ["claw_id", "item_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(migration_name("delivery_item_idx"), table_name=migration_name("deliveries"))
    op.drop_column(migration_name("claws"), "worker_models")
