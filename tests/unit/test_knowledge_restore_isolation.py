"""Contracts for disposable knowledge recovery drills (no production DB)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

from scripts import restore_knowledge_state_isolated as restore

ROOT = Path(__file__).resolve().parents[2]
VALID_ID = "20261011T024012834172Z-" + "a" * 32
DB_URL = "postgresql://sde_restore:local@restore_db:5432/sde_restore_drill"


def test_compose_has_no_live_databases_ports_or_networks():
    yaml_doc = yaml.safe_load((ROOT / "docker-compose.restore-isolated.yml").read_text())
    assert set(yaml_doc["services"]) == {"restore_db", "restore_runner"}
    assert yaml_doc["networks"]["restore_private"]["internal"] is True
    assert yaml_doc["volumes"]["source_backups"]["external"] is True
    assert ":ro" in yaml_doc["services"]["restore_runner"]["volumes"][0]
    assert "no-new-privileges:true" in yaml_doc["services"]["restore_runner"]["security_opt"]
    assert "build" not in yaml_doc["services"]["restore_runner"]
    assert yaml_doc["services"]["restore_runner"]["pull_policy"] == "never"
    assert "SDE_RESTORE_IMAGE" in yaml_doc["services"]["restore_runner"]["image"]
    for service in yaml_doc["services"].values():
        assert "ports" not in service
        assert "env_file" not in service
        assert service["networks"] == ["restore_private"]
        sources = [v.split(":", 1)[0] for v in service.get("volumes", [])]
        assert "backup_data" not in sources
        assert "snapshot_data" not in sources


def test_isolation_rejects_live_database_and_project():
    restore.validate_isolation("sde-restore-contract123", DB_URL, VALID_ID)
    for project, db, bundle in (
        ("semantic_drift_engine", DB_URL, VALID_ID),
        ("sde-restore-contract123", "postgresql://postgres:p@db:5432/semantic_drift", VALID_ID),
        ("sde-restore-contract123", "postgresql://sde_restore:p@localhost:5432/sde_restore_drill", VALID_ID),
        ("sde-restore-contract123", DB_URL, "../not-safe"),
        ("sde-restore-contract123", DB_URL, "no-bundle"),
    ):
        with pytest.raises((ValueError, RuntimeError)):
            restore.validate_isolation(project, db, bundle)


def _fixture_bundle(tmp_path: Path) -> tuple[Path, Path, str]:
    bundle = tmp_path / "source" / VALID_ID
    (bundle / "snapshots").mkdir(parents=True)
    raw = bundle / "snapshots" / "policy.txt"
    raw.write_bytes(b"seven day retention")
    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
    dump = bundle / "database.dump"
    dump.write_bytes(b"test pg archive placeholder")
    entries = []
    for path in ("database.dump", "snapshots/policy.txt"):
        target = bundle / path
        entries.append({"path": path, "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                        "size_bytes": target.stat().st_size})
    manifest = {
        "format": "sde-knowledge-bundle-v1", "snapshot_rows": 1,
        "file_references": 1, "inline_snapshot_rows": 0, "orphan_volume_files": 0,
        "artifacts": entries,
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return bundle, tmp_path / "restored", digest


def test_restore_verified_without_database_write_in_unit_environment(tmp_path, monkeypatch):
    bundle, raw_root, digest = _fixture_bundle(tmp_path)
    from scripts import backup_knowledge_state as kb

    def fake_verify(path):
        assert path == bundle
        return {"verified_artifacts": 2, "snapshot_files": 1}

    def fake_pg_restore(command, **kwargs):
        assert command[0] == "pg_restore"
        assert "--single-transaction" in command
        assert "--exit-on-error" in command
        assert "restore_db" in command
        assert command[-1] == str(bundle / "database.dump")

    monkeypatch.setattr(kb, "verify_bundle", fake_verify)
    monkeypatch.setattr(restore, "require_empty_target", lambda root: None)
    monkeypatch.setattr(restore.subprocess, "run", fake_pg_restore)
    monkeypatch.setattr(kb, "snapshot_references", lambda: [
        ("snapshot", str(raw_root / "policy.txt"), digest, False)
    ])
    output = restore.restore_bundle(bundle, raw_root, DB_URL)
    assert output == {
        "result": "isolated_restore_verified", "snapshot_rows": 1,
        "referenced_snapshot_files": 1, "restored_snapshot_files": 1,
        "orphan_volume_files": 0,
    }
    assert (raw_root / "policy.txt").read_bytes() == b"seven day retention"


def test_restored_database_hash_mismatch_fails_closed(tmp_path, monkeypatch):
    bundle, raw_root, _ = _fixture_bundle(tmp_path)
    from scripts import backup_knowledge_state as kb
    monkeypatch.setattr(kb, "verify_bundle", lambda path: {})
    monkeypatch.setattr(restore, "require_empty_target", lambda root: None)
    monkeypatch.setattr(restore.subprocess, "run", lambda *a, **kw: None)
    monkeypatch.setattr(kb, "snapshot_references", lambda: [
        ("snapshot", str(raw_root / "policy.txt"), "0" * 64, False)
    ])
    with pytest.raises(ValueError, match="wrong content hash"):
        restore.restore_bundle(bundle, raw_root, DB_URL)


def test_manifest_snapshot_row_mismatch_fails_closed(tmp_path, monkeypatch):
    bundle, raw_root, digest = _fixture_bundle(tmp_path)
    from scripts import backup_knowledge_state as kb
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["snapshot_rows"] = 2
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(kb, "verify_bundle", lambda path: {})
    monkeypatch.setattr(restore, "require_empty_target", lambda root: None)
    monkeypatch.setattr(restore.subprocess, "run", lambda *a, **kw: None)
    monkeypatch.setattr(kb, "snapshot_references", lambda: [
        ("snapshot", str(raw_root / "policy.txt"), digest, False)
    ])
    with pytest.raises(ValueError, match="manifest disagree"):
        restore.restore_bundle(bundle, raw_root, DB_URL)
