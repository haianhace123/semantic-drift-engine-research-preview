"""Safety contracts for combined PostgreSQL and raw snapshot backups."""
from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts import backup_knowledge_state as kb


ROOT = Path(__file__).resolve().parents[2]


def test_maintenance_job_is_opt_in_and_snapshot_mount_read_only():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    job = compose["services"]["knowledge_backup_maintenance"]
    assert job["profiles"] == ["maintenance"]
    assert job["command"] == ["python", "-m", "scripts.backup_knowledge_state"]
    assert job["volumes"] == ["backup_data:/app/backups", "snapshot_data:/app/data:ro"]
    assert "env_file" not in job
    assert "build" not in job
    assert "depends_on" not in job
    assert job["pull_policy"] == "never"
    assert "ports" not in job
    assert set(job["environment"]) == {"DATABASE_URL", "BACKUP_DIR", "SNAPSHOT_ROOT"}
    prod = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    section = prod.split("  knowledge_backup_maintenance:\n", 1)[1].split(
        "\n  db:", 1
    )[0]
    assert "    networks:\n      - backend\n" in section


def test_no_backup_without_explicit_quiescence_ack(tmp_path: Path, capsys):
    with pytest.raises(SystemExit) as error:
        kb.main(["--execute", "--snapshot-root", str(tmp_path)])
    assert error.value.code == 2
    assert kb.main([]) == 0
    assert "plan_only" in capsys.readouterr().out


def test_referenced_snapshot_hash_and_inline_inventory(tmp_path: Path):
    (tmp_path / "s.txt").write_text("audit evidence", encoding="utf-8")
    digest = hashlib.sha256(b"audit evidence").hexdigest()
    files = dict(kb._volume_files(tmp_path))
    refs = [("s1", str(tmp_path / "s.txt"), digest, False), ("s2", None, digest, True)]
    hashes, count = kb._reference_map(refs, tmp_path, files)
    assert hashes == {"s.txt": digest}
    assert count == 1

    # Use an absolute path on the same filesystem on both Windows and POSIX.
    outside = tmp_path.parent / "outside-snapshot-volume.txt"
    with pytest.raises(ValueError, match="outside snapshot volume"):
        kb._reference_map([("x", str(outside), digest, False)], tmp_path, files)
    with pytest.raises(ValueError, match="missing"):
        kb._reference_map([("x", str(tmp_path / "missing"), digest, False)], tmp_path, files)


def test_volume_rejects_symlinks_and_incomplete_snapshots(tmp_path: Path, monkeypatch):
    (tmp_path / "x.tmp").write_text("in progress")
    with pytest.raises(ValueError, match="Incomplete"):
        kb._volume_files(tmp_path)
    (tmp_path / "x.tmp").unlink()
    link = tmp_path / "link"
    try:
        link.symlink_to(tmp_path, target_is_directory=True)
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
        kb._volume_files(tmp_path)


def test_bundle_verifier_rejects_corruption_unlisted_files_and_traversal(tmp_path: Path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "database.dump").write_bytes(b"dump")
    snapshot_dir = bundle / "snapshots"
    snapshot_dir.mkdir()
    (snapshot_dir / "e.txt").write_bytes(b"evidence")

    entries = []
    for name in ("database.dump", "snapshots/e.txt"):
        sha, size = kb.file_hash(bundle / name)
        entries.append({"path": name, "sha256": sha, "size_bytes": size})
    manifest = {"format": kb.FORMAT, "artifacts": entries}
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert kb.verify_bundle(bundle, validate_pg_dump=False)["snapshot_files"] == 1

    (snapshot_dir / "e.txt").write_bytes(b"modified")
    with pytest.raises(ValueError, match="hash/size mismatch"):
        kb.verify_bundle(bundle, validate_pg_dump=False)
    (snapshot_dir / "e.txt").write_bytes(b"evidence")
    (snapshot_dir / "extra.txt").write_bytes(b"orphan")
    with pytest.raises(ValueError, match="unrecorded"):
        kb.verify_bundle(bundle, validate_pg_dump=False)
    (snapshot_dir / "extra.txt").unlink()

    manifest["artifacts"][1]["path"] = "snapshots/../escape"
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="Unsafe relative path"):
        kb.verify_bundle(bundle, validate_pg_dump=False)


def test_bundle_removes_staging_and_preserves_existing_on_failure(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(kb, "require_quiescent_database_connections", lambda: None)
    snapshot_root = tmp_path / "raw"
    snapshot_root.mkdir()
    (snapshot_root / "a.txt").write_bytes(b"expected")
    correct = hashlib.sha256(b"expected").hexdigest()
    monkeypatch.setattr(kb, "snapshot_references", lambda: [
        ("id", str(snapshot_root / "a.txt"), "0" * 64, False)
    ])
    from scripts import backup_database

    def fake_backup(*, backup_directory):
        dump = backup_directory / "x.dump"
        dump.write_bytes(b"fake-dump")
        return dump

    monkeypatch.setattr(backup_database, "run_backup", fake_backup)
    archive_root = tmp_path / "backup"
    with pytest.raises(ValueError, match="digest does not match"):
        kb.create_bundle(snapshot_root, archive_root)
    assert list((archive_root / kb.BUNDLE_DIRNAME).iterdir()) == []
    assert correct != "0" * 64


def test_bundle_publish_and_verify_valid_snapshot_set(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(kb, "require_quiescent_database_connections", lambda: None)
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "tenant policy.txt").write_bytes(b"policy")
    (raw / "unreferenced.txt").write_bytes(b"historic orphan")
    digest = hashlib.sha256(b"policy").hexdigest()
    monkeypatch.setattr(
        kb, "snapshot_references",
        lambda: [("snapshot1", str(raw / "tenant policy.txt"), digest, False)],
    )
    from scripts import backup_database

    def fake_backup(*, backup_directory):
        dump = backup_directory / "date.dump"
        dump.write_bytes(b"sample-pg-dump")
        return dump

    monkeypatch.setattr(backup_database, "run_backup", fake_backup)
    monkeypatch.setattr(kb.subprocess, "run", lambda *args, **kwargs: None)

    saved = kb.create_bundle(raw, tmp_path / "backups")
    assert saved.is_dir()
    assert not saved.name.startswith(".incomplete-")
    manifest = json.loads((saved / "manifest.json").read_text())
    assert manifest["format"] == kb.FORMAT
    assert manifest["snapshot_rows"] == 1
    assert manifest["file_references"] == 1
    assert manifest["orphan_volume_files"] == 1
    assert kb.verify_bundle(saved, validate_pg_dump=False) == {
        "verified_artifacts": 3, "snapshot_files": 2,
    }
    assert (saved / "snapshots" / "tenant policy.txt").read_bytes() == b"policy"
    assert (saved / "snapshots" / "unreferenced.txt").read_bytes() == b"historic orphan"
