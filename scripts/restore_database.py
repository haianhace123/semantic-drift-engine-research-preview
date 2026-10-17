"""
PostgreSQL restore utility for Semantic Drift Engine.

Restores a custom-format pg_dump backup into a target PostgreSQL database.

Usage:
    python scripts/restore_database.py /app/backups/example.dump target_database

Environment:
    DATABASE_URL
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

from pathlib import Path


if __package__:
    from .postgres_cli_connection import (
        parse_database_url,
        postgres_cli_environment,
    )
else:
    from postgres_cli_connection import (
        parse_database_url,
        postgres_cli_environment,
    )


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(
    "semantic_drift.restore"
)


def get_database_url() -> str:
    database_url = os.getenv(
        "DATABASE_URL",
        "",
    ).strip()

    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is required."
        )

    return database_url


def restore_backup(
    backup_path: Path,
    target_database: str,
) -> None:
    if not backup_path.exists():
        raise FileNotFoundError(
            f"Backup file does not exist: {backup_path}"
        )

    if not backup_path.is_file():
        raise RuntimeError(
            f"Backup path is not a file: {backup_path}"
        )

    database_url = get_database_url()
    connection = parse_database_url(
        database_url
    )

    if target_database == connection["database"]:
        raise RuntimeError(
            "Refusing to restore into the source database "
            "configured by DATABASE_URL."
        )

    environment = postgres_cli_environment(
        database_url,
        os.environ,
    )

    command = [
        "pg_restore",
        "--no-owner",
        "--no-privileges",
        "--exit-on-error",
        "--host",
        connection["host"],
        "--port",
        connection["port"],
        "--username",
        connection["user"],
        "--dbname",
        target_database,
        str(backup_path),
    ]

    logger.info(
        "Restoring backup %s into database %s.",
        backup_path,
        target_database,
    )

    subprocess.run(
        command,
        env=environment,
        check=True,
    )

    logger.info(
        "Restore completed successfully."
    )


def main() -> int:
    if len(sys.argv) != 3:
        print(
            "Usage: python scripts/restore_database.py "
            "<backup.dump> <target_database>",
            file=sys.stderr,
        )

        return 2

    backup_path = Path(
        sys.argv[1]
    )

    target_database = (
        sys.argv[2].strip()
    )

    if not target_database:
        logger.error(
            "Target database name cannot be empty."
        )

        return 2

    try:
        restore_backup(
            backup_path,
            target_database,
        )

        return 0
    except FileNotFoundError:
        logger.exception(
            "Backup file was not found."
        )

        return 1
    except subprocess.CalledProcessError:
        logger.exception(
            "pg_restore failed."
        )

        return 1
    except Exception:
        logger.exception(
            "Database restore failed."
        )

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )