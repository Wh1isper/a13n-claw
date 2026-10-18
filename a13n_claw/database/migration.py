"""Run package-owned Alembic migrations under the caller's Instance lease."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import URL, Connection, create_engine, inspect
from sqlalchemy.pool import NullPool

from a13n_claw.errors import ClawError

MIGRATIONS_PATH = Path(__file__).parent.parent / "business_migrations"


class DatabaseMigrator:
    """A single linear history, applied atomically before application state is opened."""

    def __init__(self, path: Path):
        self.path = path.resolve()

    @staticmethod
    def config() -> Config:
        config = Config()
        config.set_main_option("script_location", str(MIGRATIONS_PATH).replace("%", "%%"))
        config.set_main_option(
            "file_template", "%%(year)d%%(month).2d%%(day).2d_%%(rev)s_%%(slug)s"
        )
        config.set_main_option("timezone", "UTC")
        return config

    def history(self, *, allow_empty: bool = False) -> ScriptDirectory:
        scripts = ScriptDirectory.from_config(self.config())
        heads = scripts.get_heads()
        if (len(heads) != 1 and not (allow_empty and not heads)) or any(
            revision.is_branch_point or revision.is_merge_point
            for revision in scripts.walk_revisions()
        ):
            raise ClawError("migration_history", "Claw migrations must form one linear history")
        return scripts

    @contextmanager
    def connection(self) -> Iterator[Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        engine = create_engine(
            URL.create("sqlite", database=str(self.path)),
            poolclass=NullPool,
            connect_args={"timeout": 10},
        )
        try:
            with engine.connect() as connection:
                connection.exec_driver_sql("PRAGMA journal_mode=WAL")
                connection.exec_driver_sql("PRAGMA synchronous=FULL")
                # SQLite batch rebuilds may drop referenced tables. Validate all
                # foreign keys before commit, while rollback is still possible.
                connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
                connection.commit()
                connection.exec_driver_sql("BEGIN IMMEDIATE")
                try:
                    yield connection
                    if connection.exec_driver_sql("PRAGMA foreign_key_check").fetchone():
                        raise ClawError(
                            "migration_integrity", "Migration left invalid foreign keys"
                        )
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
        finally:
            engine.dispose()

    def upgrade(self) -> None:
        scripts = self.history()
        known = {revision.revision for revision in scripts.walk_revisions()}
        with self.connection() as connection:
            current = MigrationContext.configure(connection).get_current_heads()
            if len(current) > 1 or any(revision not in known for revision in current):
                raise ClawError(
                    "schema_incompatible",
                    "Database revision is unknown to this Claw version; use a matching or newer "
                    "package, or restore a pre-upgrade backup",
                )
            if not current and (
                connection.exec_driver_sql("PRAGMA user_version").scalar_one() != 0
                or set(inspect(connection).get_table_names()) - {"alembic_version"}
            ):
                raise ClawError(
                    "schema_incompatible",
                    "This database predates the migration baseline; use a fresh development "
                    "data root. Existing data has not been reset",
                )
            config = self.config()
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

    def revision(self, message: str) -> None:
        """Called by the development generator with a disposable database only."""
        if not message.strip():
            raise ValueError("Migration message must not be empty")
        scripts = self.history(allow_empty=True)
        if scripts.get_heads():
            self.upgrade()
        with self.connection() as connection:
            config = self.config()
            config.attributes["connection"] = connection
            command.revision(config, message=message, autogenerate=True)
