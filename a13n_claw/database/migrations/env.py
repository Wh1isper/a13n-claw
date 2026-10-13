"""Alembic environment; the runner owns the SQLite connection and transaction."""

from alembic import context
from sqlalchemy import Connection

from a13n_claw.database.schema import metadata

connection = context.config.attributes.get("connection")
if not isinstance(connection, Connection):
    raise RuntimeError("Claw migrations require a runner-owned SQLite connection")

context.configure(
    connection=connection,
    target_metadata=metadata,
    compare_type=True,
    compare_server_default=True,
    render_as_batch=True,
)
with context.begin_transaction():
    context.run_migrations()
