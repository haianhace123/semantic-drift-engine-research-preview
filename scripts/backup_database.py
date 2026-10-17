"""
PostgreSQL backup utility for Semantic Drift Engine.

Creates compressed custom-format backups using pg_dump.

Usage:
    python scripts/backup_database.py

Environment:
    DATABASE_URL
    BACKUP_DIR
    BACKUP_RETENTION_DAYS
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

from datetime import datetime, timedelta, timezone
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


PROJECT_ROOT = Path(__file__).resolve().parents[1]

BACKUP_DIR = Path(
    os.getenv(
        "BACKUP_DIR",
        str(PROJECT_ROOT / "backups"),
    )
)

BACKUP_RETENTION_DAYS = int(
    os.getenv(
        "BACKUP_RETENTION_DAYS",
        "14",
    )
)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(
    "semantic_drift.backup"
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


def ensure_backup_directory() -> None:
    BACKUP_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )


def build_backup_path(
    database: str,
) -> Path:
    timestamp = datetime.now(
        timezone.utc
    ).strftime(
        "%Y%m%dT%H%M%SZ"
    )

    return (
        BACKUP_DIR
        / f"{database}-{timestamp}.dump"
    )


def run_backup(*, backup_directory: Path | None = None) -> Path:
    database_url = get_database_url()

    connection = parse_database_url(
        database_url
    )

    if backup_directory is None:
        ensure_backup_directory()
        backup_path = build_backup_path(connection["database"])
    else:
        backup_directory.mkdir(parents=True, exist_ok=True)
        backup_path = backup_directory / build_backup_path(connection["database"]).name

    partial_path = backup_path.with_suffix(
        backup_path.suffix + ".partial"
    )

    environment = postgres_cli_environment(
        database_url,
        os.environ,
    )

    command = [
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--host",
        connection["host"],
        "--port",
        connection["port"],
        "--username",
        connection["user"],
        "--file",
        str(partial_path),
        connection["database"],
    ]

    logger.info(
        "Creating database backup: %s",
        backup_path,
    )

    try:
        subprocess.run(
            command,
            env=environment,
            check=True,
        )

        if (
            not partial_path.exists()
            or partial_path.stat().st_size == 0
        ):
            raise RuntimeError(
                "Backup command completed but produced "
                "an empty backup file."
            )

        subprocess.run(
            [
                "pg_restore",
                "--list",
                str(partial_path),
            ],
            env=environment,
            check=True,
            stdout=subprocess.DEVNULL,
        )

        partial_path.replace(
            backup_path
        )
    except Exception:
        partial_path.unlink(
            missing_ok=True
        )
        raise

    logger.info(
        "Backup completed: %s (%d bytes)",
        backup_path,
        backup_path.stat().st_size,
    )

    return backup_path


def remove_expired_backups() -> int:
    if BACKUP_RETENTION_DAYS <= 0:
        return 0

    cutoff = (
        datetime.now(
            timezone.utc
        )
        - timedelta(
            days=BACKUP_RETENTION_DAYS
        )
    )

    removed = 0

    for path in BACKUP_DIR.glob(
        "*.dump"
    ):
        modified_at = datetime.fromtimestamp(
            path.stat().st_mtime,
            tz=timezone.utc,
        )

        if modified_at >= cutoff:
            continue

        path.unlink()
        removed += 1

        logger.info(
            "Removed expired backup: %s",
            path,
        )

    return removed


def main() -> int:
    try:
        backup_path = run_backup()

        removed = remove_expired_backups()

        logger.info(
            "Backup lifecycle complete. "
            "backup=%s expired_removed=%d",
            backup_path,
            removed,
        )

        return 0
    except FileNotFoundError:
        logger.exception(
            "pg_dump is not available in PATH."
        )

        return 1
    except subprocess.CalledProcessError:
        logger.exception(
            "pg_dump failed."
        )

        return 1
    except Exception:
        logger.exception(
            "Database backup failed."
        )

        return 1


if __name__ == "__main__":
    raise SystemExit(
        main()
    )