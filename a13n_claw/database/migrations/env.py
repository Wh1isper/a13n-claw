"""Product-only Alembic environment."""

from alembic import context
from sqlalchemy import Connection

from a13n_claw.database.schema import metadata

connection = context.config.attributes.get("connection")
if not isinstance(connection, Connection):
    raise RuntimeError("Product migrations require a runner-owned connection")
context.configure(
    connection=connection,
    target_metadata=metadata,
    compare_type=True,
    compare_server_default=True,
    render_as_batch=True,
)
with context.begin_transaction():
    context.run_migrations()
