"""Contracts for all-tenant pre-maintenance snapshot inventory."""
from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path
from types import SimpleNamespace

from scripts import audit_global_knowledge_backup_readiness_readonly as audit


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_valid_global_inventory_includes_orphans_and_inline_snapshots(tmp_path: Path):
    (tmp_path / "current.txt").write_bytes(b"customer source")
    (tmp_path / "legacy.txt").write_bytes(b"unreferenced evidence")
    rows = [
        ("secret-id", str(tmp_path / "current.txt"), sha(b"customer source"), False),
        ("inline", None, sha(b"inline"), True),
    ]
    report = audit.inspect_snapshot_integrity(rows, tmp_path, verify_hash=True)
    assert report["snapshot_integrity"] == "pass"
    assert report["database_snapshot_rows"] == 2
    assert report["inline_only_rows"] == 1
    assert report["hashes_verified_files"] == 1
    assert report["unreferenced_volume_files"] == 1
    assert report["ready_to_execute_backup"] is False
    assert "current.txt" not in str(report)
    assert "secret-id" not in str(report)


def test_detects_legacy_relative_missing_hash_and_no_content(tmp_path: Path):
    (tmp_path / "known.txt").write_bytes(b"present")
    records = [
        ("relative", "historic/old.txt", sha(b"x"), False),
        ("missing", str(tmp_path / "missing.txt"), sha(b"missing"), False),
        ("bad-hash", str(tmp_path / "known.txt"), "NOT_SHA256", False),
        ("no-content", None, sha(b"x"), False),
    ]
    report = audit.inspect_snapshot_integrity(records, tmp_path, verify_hash=True)
    assert report["snapshot_integrity"] == "blocked"
    assert report["affected_snapshot_rows"] == 4
    assert report["issues"] == {
        "invalid_sha256_metadata": 1,
        "missing_raw_and_inline_content": 1,
        "missing_snapshot_artifact": 1,
        "unsafe_or_relative_reference": 1,
    }


def test_detects_mismatched_content_and_conflicting_references(tmp_path: Path):
    file = tmp_path / "snapshot.txt"
    file.write_bytes(b"actual")
    rows = [
        ("s1", str(file), sha(b"old"), False),
        ("s2", str(file), sha(b"other"), False),
    ]
    report = audit.inspect_snapshot_integrity(rows, tmp_path, verify_hash=True)
    assert report["issues"]["sha256_mismatch"] == 2
    assert report["issues"]["conflicting_sha256_references"] == 1
    assert report["hashes_verified_files"] == 1


def test_nonhashing_inspection_is_not_marked_ready(tmp_path: Path):
    f = tmp_path / "valid"
    f.write_bytes(b"v1")
    report = audit.inspect_snapshot_integrity(
        [("x", str(f), sha(b"v1"), False)], tmp_path, verify_hash=False
    )
    assert report["snapshot_integrity"] == "hash_not_verified"
    assert report["ready_to_execute_backup"] is False


def test_symlinks_and_partial_files_fail_closed(tmp_path: Path, monkeypatch):
    import pytest
    (tmp_path / "unfinished.partial").write_bytes(b"incomplete")
    with pytest.raises(ValueError, match="Incomplete"):
        audit.inspect_snapshot_integrity([], tmp_path, verify_hash=True)
    (tmp_path / "unfinished.partial").unlink()
    link = tmp_path / "link"
    try:
        link.symlink_to(tmp_path / "elsewhere")
    except OSError as exc:
        if getattr(exc, "winerror", None) != 1314:
            raise
        # Windows without symlink privilege: exercise the same lstat() guard.
        link.write_bytes(b"symlink fixture")
        original_lstat = Path.lstat

        def simulated_link_lstat(path, *args, **kwargs):
            if path == link:
                return SimpleNamespace(st_mode=stat.S_IFLNK)
            return original_lstat(path, *args, **kwargs)

        monkeypatch.setattr(Path, "lstat", simulated_link_lstat)
    with pytest.raises(ValueError, match="link"):
        audit.inspect_snapshot_integrity([], tmp_path, verify_hash=True)


def test_cli_redacts_errors(tmp_path: Path, monkeypatch, capsys):
    def fail(*_args, **_kwargs):
        raise OSError("/private/customer/path and postgres://secret")
    monkeypatch.setattr(audit, "audit", fail)
    assert audit.main(["--snapshot-root", str(tmp_path)]) == 1
    report = json.loads(capsys.readouterr().err)
    assert report["snapshot_integrity"] == "inspection_failed"
    assert "private" not in str(report)
    assert "secret" not in str(report)
