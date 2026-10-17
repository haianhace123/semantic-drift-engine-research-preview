"""Execution must refuse non-quiescent PostgreSQL even with operator ack."""
from __future__ import annotations

import hashlib
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from scripts import backup_knowledge_state as kb
from scripts import check_knowledge_backup_connections_readonly as connection_gate


def _report(passed: bool) -> dict[str, object]:
    return {"connection_gate_pass": passed}


def test_active_sessions_refuse_backup_before_any_artifact_write(tmp_path, monkeypatch):
    snapshot_root = tmp_path / "raw"
    snapshot_root.mkdir()
    backup_root = tmp_path / "backups"
    monkeypatch.setenv("DATABASE_URL", "postgresql://operator:secret@db/db")
    monkeypatch.setattr(connection_gate, "inspect", lambda _: _report(False))

    with pytest.raises(ValueError, match="client sessions"):
        kb.create_bundle(snapshot_root, backup_root)
    assert not backup_root.exists()


def test_operator_ack_does_not_bypass_failed_connection_gate(tmp_path, monkeypatch, capsys):
    snapshot_root = tmp_path / "raw"
    snapshot_root.mkdir()
    monkeypatch.setenv("DATABASE_URL", "postgresql://operator:secret@db/db")
    monkeypatch.setattr(connection_gate, "inspect", lambda _: _report(False))
    assert kb.main(["--execute", "--acknowledge-writers-quiesced",
                    "--snapshot-root", str(snapshot_root),
                    "--backup-root", str(tmp_path / "backups")]) == 1
    output = capsys.readouterr()
    assert "secret" not in (output.out + output.err)
    assert not (tmp_path / "backups").exists()


def test_failed_session_inspection_does_not_log_provider_exception(tmp_path, monkeypatch, capsys):
    snapshot_root = tmp_path / "raw"
    snapshot_root.mkdir()
    monkeypatch.setenv("DATABASE_URL", "postgresql://operator:secret@db/db")
    def broken(_):
        raise RuntimeError("postgresql://operator:secret@db/db")
    monkeypatch.setattr(connection_gate, "inspect", broken)
    assert kb.main(["--execute", "--acknowledge-writers-quiesced",
                    "--snapshot-root", str(snapshot_root)]) == 1
    assert "secret" not in (capsys.readouterr().err)


def test_gate_called_before_and_after_verified_staging(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "document.txt").write_bytes(b"evidence")
    digest = hashlib.sha256(b"evidence").hexdigest()
    monkeypatch.setattr(kb, "snapshot_references", lambda: [
        ("snapshot1", str(raw / "document.txt"), digest, False),
    ])
    from scripts import backup_database
    def fake_dump(*, backup_directory):
        path = backup_directory / "test.dump"
        path.write_bytes(b"dump")
        return path
    monkeypatch.setattr(backup_database, "run_backup", fake_dump)
    monkeypatch.setattr(kb.subprocess, "run", lambda *a, **kw: None)

    checks = []
    monkeypatch.setattr(kb, "require_quiescent_database_connections",
                        lambda: checks.append("gate"))
    result = kb.create_bundle(raw, tmp_path / "backups")
    assert result.exists()
    assert checks == ["gate", "gate"]


def test_gate_rejects_reconnected_session_before_publish(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "document.txt").write_bytes(b"evidence")
    digest = hashlib.sha256(b"evidence").hexdigest()
    monkeypatch.setattr(kb, "snapshot_references", lambda: [
        ("snapshot1", str(raw / "document.txt"), digest, False),
    ])
    from scripts import backup_database
    def fake_dump(*, backup_directory):
        path = backup_directory / "test.dump"
        path.write_bytes(b"dump")
        return path
    monkeypatch.setattr(backup_database, "run_backup", fake_dump)
    monkeypatch.setattr(kb.subprocess, "run", lambda *a, **kw: None)

    count = 0
    def gate():
        nonlocal count
        count += 1
        if count == 2:
            raise ValueError("Knowledge backup refused: PostgreSQL still has client sessions")
    monkeypatch.setattr(kb, "require_quiescent_database_connections", gate)
    backup_root = tmp_path / "backups"
    with pytest.raises(ValueError, match="client sessions"):
        kb.create_bundle(raw, backup_root)
    assert count == 2
    assert list((backup_root / kb.BUNDLE_DIRNAME).iterdir()) == []


@pytest.mark.parametrize("pool_dispose_fails", [False, True])
def test_final_connection_gate_releases_own_idle_reader_pool_first(tmp_path, monkeypatch, pool_dispose_fails):
    """A completed snapshot Session may leave a real PostgreSQL backend idle."""
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "document.txt").write_bytes(b"evidence")
    digest = hashlib.sha256(b"evidence").hexdigest()
    monkeypatch.setattr(kb, "snapshot_references", lambda: [
        ("snapshot1", str(raw / "document.txt"), digest, False),
    ])
    from scripts import backup_database
    def fake_dump(*, backup_directory):
        path = backup_directory / "test.dump"
        path.write_bytes(b"dump")
        return path
    monkeypatch.setattr(backup_database, "run_backup", fake_dump)
    monkeypatch.setattr(kb.subprocess, "run", lambda *args, **kw: None)

    order = []
    pooled_engine = MagicMock()
    def dispose():
        order.append("release own reader pool")
        if pool_dispose_fails:
            raise OSError("pooled connection cleanup failed")
    pooled_engine.dispose.side_effect = dispose
    monkeypatch.setitem(sys.modules, "src.core.database", SimpleNamespace(engine=pooled_engine))
    monkeypatch.setattr(kb, "require_quiescent_database_connections",
                        lambda: order.append("gate"))

    backup_root = tmp_path / "backups"
    if pool_dispose_fails:
        with pytest.raises(OSError, match="cleanup failed"):
            kb.create_bundle(raw, backup_root)
        assert order == ["gate", "release own reader pool"]
        assert list((backup_root / kb.BUNDLE_DIRNAME).iterdir()) == []
    else:
        result = kb.create_bundle(raw, backup_root)
        assert result.exists()
        assert order == ["gate", "release own reader pool", "gate"]
        pooled_engine.dispose.assert_called_once()


def test_reader_pool_release_noop_when_snapshot_module_not_loaded(monkeypatch):
    """Mocked snapshot audit creates no pool and should need no DB import."""
    monkeypatch.delitem(sys.modules, "src.core.database", raising=False)
    kb.release_snapshot_reader_pool()
