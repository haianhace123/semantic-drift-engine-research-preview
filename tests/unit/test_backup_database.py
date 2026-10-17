from __future__ import annotations

import subprocess

from pathlib import Path

import pytest

from scripts import backup_database


def _configure_backup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        backup_database,
        "BACKUP_DIR",
        tmp_path,
    )
    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql://app:secret@db:5432/source_db",
    )


def test_run_backup_publishes_only_validated_archive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_backup(
        monkeypatch,
        tmp_path,
    )

    final_path = tmp_path / "source_db-test.dump"
    partial_path = tmp_path / "source_db-test.dump.partial"

    monkeypatch.setattr(
        backup_database,
        "build_backup_path",
        lambda database: final_path,
    )

    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(
        command: list[str],
        *,
        env: dict[str, str],
        check: bool,
        stdout: object = None,
    ) -> None:
        calls.append(
            (
                command,
                env,
            )
        )

        if command[0] == "pg_dump":
            partial_path.write_bytes(
                b"valid-custom-archive"
            )
            return

        assert command == [
            "pg_restore",
            "--list",
            str(partial_path),
        ]
        assert stdout is subprocess.DEVNULL

    monkeypatch.setattr(
        backup_database.subprocess,
        "run",
        fake_run,
    )

    result = backup_database.run_backup()

    assert result == final_path
    assert final_path.read_bytes() == b"valid-custom-archive"
    assert not partial_path.exists()

    assert len(calls) == 2

    dump_command, dump_env = calls[0]

    assert dump_command == [
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--host",
        "db",
        "--port",
        "5432",
        "--username",
        "app",
        "--file",
        str(partial_path),
        "source_db",
    ]
    assert dump_env["PGPASSWORD"] == "secret"
    assert "secret" not in " ".join(dump_command)

    restore_command, restore_env = calls[1]

    assert restore_command == [
        "pg_restore",
        "--list",
        str(partial_path),
    ]
    assert restore_env["PGPASSWORD"] == "secret"


def test_run_backup_removes_partial_artifact_when_pg_dump_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_backup(
        monkeypatch,
        tmp_path,
    )

    final_path = tmp_path / "source_db-test.dump"
    partial_path = tmp_path / "source_db-test.dump.partial"

    monkeypatch.setattr(
        backup_database,
        "build_backup_path",
        lambda database: final_path,
    )

    def fake_run(
        command: list[str],
        **kwargs: object,
    ) -> None:
        partial_path.write_bytes(
            b"incomplete"
        )
        raise subprocess.CalledProcessError(
            1,
            command,
        )

    monkeypatch.setattr(
        backup_database.subprocess,
        "run",
        fake_run,
    )

    with pytest.raises(
        subprocess.CalledProcessError
    ):
        backup_database.run_backup()

    assert not partial_path.exists()
    assert not final_path.exists()


def test_run_backup_removes_partial_artifact_when_archive_validation_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_backup(
        monkeypatch,
        tmp_path,
    )

    final_path = tmp_path / "source_db-test.dump"
    partial_path = tmp_path / "source_db-test.dump.partial"

    monkeypatch.setattr(
        backup_database,
        "build_backup_path",
        lambda database: final_path,
    )

    def fake_run(
        command: list[str],
        **kwargs: object,
    ) -> None:
        if command[0] == "pg_dump":
            partial_path.write_bytes(
                b"invalid-archive"
            )
            return

        raise subprocess.CalledProcessError(
            1,
            command,
        )

    monkeypatch.setattr(
        backup_database.subprocess,
        "run",
        fake_run,
    )

    with pytest.raises(
        subprocess.CalledProcessError
    ):
        backup_database.run_backup()

    assert not partial_path.exists()
    assert not final_path.exists()


def test_run_backup_rejects_empty_artifact_without_publishing_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _configure_backup(
        monkeypatch,
        tmp_path,
    )

    final_path = tmp_path / "source_db-test.dump"
    partial_path = tmp_path / "source_db-test.dump.partial"

    monkeypatch.setattr(
        backup_database,
        "build_backup_path",
        lambda database: final_path,
    )

    def fake_run(
        command: list[str],
        **kwargs: object,
    ) -> None:
        assert command[0] == "pg_dump"
        partial_path.touch()

    monkeypatch.setattr(
        backup_database.subprocess,
        "run",
        fake_run,
    )

    with pytest.raises(
        RuntimeError,
        match="empty backup file",
    ):
        backup_database.run_backup()

    assert not partial_path.exists()
    assert not final_path.exists()
