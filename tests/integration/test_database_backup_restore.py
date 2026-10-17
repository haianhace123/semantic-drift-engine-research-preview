from __future__ import annotations

import os
import subprocess
import uuid

from pathlib import Path
from urllib.parse import urlparse, urlunparse

import pytest
from disposable_postgres_admin_guard import require_disposable_postgres_admin

from sqlalchemy import create_engine, text

from scripts import backup_database
from scripts import restore_database


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="database integration tests are disabled",
)


def _database_url_for(
    database_url: str,
    database: str,
) -> str:
    parsed = urlparse(
        database_url
    )

    return urlunparse(
        parsed._replace(
            path=f"/{database}"
        )
    )


def _create_database(
    admin_url: str,
    database: str,
) -> None:
    engine = create_engine(
        admin_url,
        isolation_level="AUTOCOMMIT",
    )

    try:
        with engine.connect() as connection:
            connection.execute(
                text(
                    f'CREATE DATABASE "{database}" TEMPLATE template0'
                )
            )
    finally:
        engine.dispose()


def _drop_database(
    admin_url: str,
    database: str,
) -> None:
    engine = create_engine(
        admin_url,
        isolation_level="AUTOCOMMIT",
    )

    try:
        with engine.connect() as connection:
            connection.execute(
                text(
                    """
                    SELECT pg_terminate_backend(pid)
                    FROM pg_stat_activity
                    WHERE datname = :database
                      AND pid <> pg_backend_pid()
                    """
                ),
                {
                    "database": database,
                },
            )

            connection.execute(
                text(
                    f'DROP DATABASE IF EXISTS "{database}"'
                )
            )
    finally:
        engine.dispose()


def test_backup_restore_round_trip_recovers_database_content(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    require_disposable_postgres_admin()
    configured_url = os.environ["DATABASE_URL"]

    parsed = urlparse(
        configured_url
    )

    admin_url = _database_url_for(
        configured_url,
        parsed.path.lstrip("/"),
    )

    suffix = uuid.uuid4().hex[:12]
    source_database = f"sde_backup_source_{suffix}"
    target_database = f"sde_backup_target_{suffix}"

    source_url = _database_url_for(
        configured_url,
        source_database,
    )

    created_databases: list[str] = []

    try:
        _create_database(
            admin_url,
            source_database,
        )
        created_databases.append(source_database)

        _create_database(
            admin_url,
            target_database,
        )
        created_databases.append(target_database)

        source_engine = create_engine(
            source_url
        )

        try:
            with source_engine.begin() as connection:
                connection.execute(
                    text(
                        """
                        CREATE TABLE recovery_probe (
                            id INTEGER PRIMARY KEY,
                            payload TEXT NOT NULL
                        )
                        """
                    )
                )
                connection.execute(
                    text(
                        """
                        INSERT INTO recovery_probe (
                            id,
                            payload
                        )
                        VALUES (
                            1,
                            'semantic-drift-recovery-proof'
                        )
                        """
                    )
                )
        finally:
            source_engine.dispose()

        monkeypatch.setenv(
            "DATABASE_URL",
            source_url,
        )
        monkeypatch.setattr(
            backup_database,
            "BACKUP_DIR",
            tmp_path,
        )

        backup_path = backup_database.run_backup()

        assert backup_path.exists()
        assert backup_path.stat().st_size > 0
        assert backup_path.suffix == ".dump"
        assert not list(
            tmp_path.glob("*.partial")
        )

        restore_database.restore_backup(
            backup_path,
            target_database,
        )

        target_url = _database_url_for(
            configured_url,
            target_database,
        )

        target_engine = create_engine(
            target_url
        )

        try:
            with target_engine.connect() as connection:
                row = connection.execute(
                    text(
                        """
                        SELECT id, payload
                        FROM recovery_probe
                        """
                    )
                ).fetchone()
        finally:
            target_engine.dispose()

        assert row == (
            1,
            "semantic-drift-recovery-proof",
        )
    finally:
        for name in reversed(created_databases):
            _drop_database(admin_url, name)
