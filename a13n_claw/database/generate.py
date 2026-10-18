"""Generate a business revision against a disposable SQLite database."""

import argparse
import tempfile
from pathlib import Path

from a13n_claw.database.migration import DatabaseMigrator


def main() -> None:
    parser = argparse.ArgumentParser(prog="claw-db-migrate")
    parser.add_argument("message")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="claw-migration-") as directory:
        DatabaseMigrator(Path(directory) / "business.sqlite3").revision(args.message)


if __name__ == "__main__":
    main()
