from __future__ import annotations

from pathlib import Path

import pytest

from scripts import restore_database


def test_parse_database_url_preserves_source_database_identity() -> None:
    connection = restore_database.parse_database_url(
        "postgresql://app:secret@db:5433/source_db"
    )

    assert connection == {
        "host": "db",
        "port": "5433",
        "user": "app",
        "password": "secret",
        "database": "source_db",
    }


def test_parse_database_url_requires_database_name() -> None:
    with pytest.raises(
        RuntimeError,
        match="does not contain a database name",
    ):
        restore_database.parse_database_url(
            "postgresql://app:secret@db:5432"
        )


def test_restore_refuses_source_database_before_invoking_pg_restore(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backup_path = tmp_path / "backup.dump"
    backup_path.write_bytes(
        b"archive"
    )

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://app:secret@db:5432/source_db",
    )

    invoked = False

    def fake_run(
        *args: object,
        **kwargs: object,
    ) -> None:
        nonlocal invoked
        invoked = True

    monkeypatch.setattr(
        restore_database.subprocess,
        "run",
        fake_run,
    )

    with pytest.raises(
        RuntimeError,
        match="Refusing to restore into the source database",
    ):
        restore_database.restore_backup(
            backup_path,
            "source_db",
        )

    assert invoked is False


def test_restore_uses_explicit_target_without_exposing_password_in_argv(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backup_path = tmp_path / "backup.dump"
    backup_path.write_bytes(
        b"archive"
    )

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://app:secret@db:5432/source_db",
    )

    captured: dict[str, object] = {}

    def fake_run(
        command: list[str],
        *,
        env: dict[str, str],
        check: bool,
    ) -> None:
        captured["command"] = command
        captured["env"] = env
        captured["check"] = check

    monkeypatch.setattr(
        restore_database.subprocess,
        "run",
        fake_run,
    )

    restore_database.restore_backup(
        backup_path,
        "scratch_restore",
    )

    command = captured["command"]
    environment = captured["env"]

    assert isinstance(
        command,
        list,
    )
    assert isinstance(
        environment,
        dict,
    )

    assert command == [
        "pg_restore",
        "--no-owner",
        "--no-privileges",
        "--exit-on-error",
        "--host",
        "db",
        "--port",
        "5432",
        "--username",
        "app",
        "--dbname",
        "scratch_restore",
        str(backup_path),
    ]
    assert captured["check"] is True
    assert environment["PGPASSWORD"] == "secret"
    assert "secret" not in " ".join(command)
