"""Shared connection contract for PostgreSQL backup and restore CLI tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import backup_database, restore_database
from scripts.postgres_cli_connection import (
    parse_database_url,
    postgres_cli_environment,
)


ESCAPED_URL = (
    "postgresql+psycopg2://us%40er:p%40ss%3Aword%2Fdb@db:5433/"
    "knowledge_db?sslmode=verify-full&sslrootcert=%2Fetc%2Fssl%2Fca.pem"
    "&connect_timeout=5&channel_binding=require"
)


def test_decodes_credentials_and_preserves_libpq_tls_contract() -> None:
    connection = parse_database_url(ESCAPED_URL)
    assert connection == {
        "host": "db",
        "port": "5433",
        "user": "us@er",
        "password": "p@ss:word/db",
        "database": "knowledge_db",
    }

    environment = postgres_cli_environment(
        ESCAPED_URL,
        {"PGPASSWORD": "wrong-secret", "OTHER_VAR": "keep"},
    )
    assert environment["PGPASSWORD"] == "p@ss:word/db"
    assert environment["PGSSLMODE"] == "verify-full"
    assert environment["PGSSLROOTCERT"] == "/etc/ssl/ca.pem"
    assert environment["PGCONNECT_TIMEOUT"] == "5"
    assert environment["PGCHANNELBINDING"] == "require"
    assert environment["OTHER_VAR"] == "keep"


def test_url_without_password_does_not_inherit_unrelated_password() -> None:
    environment = postgres_cli_environment(
        "postgresql://db:5432/source_db",
        {"PGPASSWORD": "stale"},
    )
    assert "PGPASSWORD" not in environment


@pytest.mark.parametrize(
    "url, message",
    [
        ("sqlite:///db.sqlite", "must use PostgreSQL"),
        ("postgresql://app:secret@db:5432", "database name"),
        (
            "postgresql://app:secret@db/app?sslmode=require&sslmode=disable",
            "sslmode must have one nonempty value",
        ),
        (
            "postgresql://app:secret@db/app?sslmode=",
            "sslmode must have one nonempty value",
        ),
        (
            "postgresql://app:secret@db/app?sslmode=require&unknown_param=1",
            "unsupported PostgreSQL CLI parameters: unknown_param",
        ),
    ],
)
def test_malformed_or_unsupported_connection_policy_fails_closed(
    url: str, message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        postgres_cli_environment(url, {})


def test_backup_pg_dump_uses_decoded_credentials_and_tls_without_argv_leak(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("DATABASE_URL", ESCAPED_URL)
    monkeypatch.setattr(backup_database, "BACKUP_DIR", tmp_path)
    monkeypatch.setattr(
        backup_database,
        "build_backup_path",
        lambda database: tmp_path / "archive.dump",
    )
    seen = []

    def fake_run(command, *, env, check, stdout=None):
        seen.append((command, env, check))
        if command[0] == "pg_dump":
            (tmp_path / "archive.dump.partial").write_bytes(b"archive")

    monkeypatch.setattr(backup_database.subprocess, "run", fake_run)
    path = backup_database.run_backup()
    assert path == tmp_path / "archive.dump"
    assert len(seen) == 2
    command, env, check = seen[0]
    assert check is True
    assert command[command.index("--username") + 1] == "us@er"
    assert command[-1] == "knowledge_db"
    assert env["PGPASSWORD"] == "p@ss:word/db"
    assert env["PGSSLMODE"] == "verify-full"
    assert env["PGSSLROOTCERT"] == "/etc/ssl/ca.pem"
    assert "p@ss" not in " ".join(command)


def test_restore_uses_same_connection_contract_and_explicit_target(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    archive = tmp_path / "archive.dump"
    archive.write_bytes(b"archive")
    monkeypatch.setenv("DATABASE_URL", ESCAPED_URL)
    seen = []

    def fake_run(command, *, env, check):
        seen.append((command, env, check))

    monkeypatch.setattr(restore_database.subprocess, "run", fake_run)
    restore_database.restore_backup(archive, "restore_target")
    assert len(seen) == 1
    command, env, check = seen[0]
    assert check is True
    assert command[command.index("--dbname") + 1] == "restore_target"
    assert command[command.index("--username") + 1] == "us@er"
    assert env["PGPASSWORD"] == "p@ss:word/db"
    assert env["PGSSLMODE"] == "verify-full"
    assert "p@ss" not in " ".join(command)
