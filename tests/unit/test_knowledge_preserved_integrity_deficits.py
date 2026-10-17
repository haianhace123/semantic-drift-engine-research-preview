"""Explicitly degraded knowledge preservation never attests full evidence recovery."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts import backup_knowledge_state as kb
from scripts import restore_knowledge_state_isolated as restore

OWNER = "08f96bf7-6adb-4b6a-a39e-2379da0de169"
OTHER = "bf48bb5d-4a37-4e5e-bfac-43e6e7e34f3b"


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def fixture_records(root: Path, *, seed_content="Data retained for 30 days"):
    missing = str(root / "lost.txt")
    records = [
        ("00000000-0000-0000-0000-000000000001", "data/raw/old_before.txt", digest(seed_content), True),
        ("00000000-0000-0000-0000-000000000002", missing, "e" * 64, False),
        ("00000000-0000-0000-0000-000000000003", None, digest("no path"), True),
    ]
    details = {records[0][0]: (OWNER, seed_content), records[1][0]: (OWNER, None)}
    return records, details


def assess(root, records, details, *, owner=OWNER, legacy=1, missing=1, seeds=None):
    return kb.assess_preserved_references(
        records, root, dict(kb._volume_files(root)),
        allowance=kb.PreservationAllowance(owner, legacy, missing),
        details=details, seed_hashes=frozenset(seeds or {digest("Data retained for 30 days")}),
    )


def test_known_verified_legacy_inline_and_real_loss_are_distinct(tmp_path):
    records, details = fixture_records(tmp_path)
    refs, inline, deficits = assess(tmp_path, records, details)
    assert refs == {}
    assert inline == 1
    assert deficits == {"verified_legacy_inline_rows": 1, "missing_raw_rows": 1}
    assert kb.snapshot_reference_fingerprint(records) != kb.snapshot_reference_fingerprint(records[:-1])


@pytest.mark.parametrize("mutation, expected", [
    ("wrong_owner", "approved organization"),
    ("wrong_counts", "explicitly approved counts"),
    ("unknown_seed_hash", "Historical seed bytes"),
    ("mismatched_inline_bytes", "Historical inline bytes"),
    ("missing_inline_reappears", "unexpectedly has inline"),
    ("unattributed_detail", "detail set"),
    ("path_traversal", "Unrecognized historical"),
    ("other_org", "approved organization"),
 ])
def test_preservation_fails_closed_on_changes(tmp_path, mutation, expected):
    records, details = fixture_records(tmp_path)
    seeds = {records[0][2]}
    owner = OWNER
    legacy = 1
    if mutation == "wrong_owner":
        owner = OTHER
    elif mutation == "wrong_counts":
        legacy = 2
    elif mutation == "unknown_seed_hash":
        seeds = {"f" * 64}
    elif mutation == "mismatched_inline_bytes":
        details[records[0][0]] = (OWNER, "changed")
    elif mutation == "missing_inline_reappears":
        details[records[1][0]] = (OWNER, "recovered")
    elif mutation == "unattributed_detail":
        details["phantom"] = (OWNER, None)
    elif mutation == "path_traversal":
        r = records[0]
        records[0] = (r[0], "../outside_before.txt", r[2], r[3])
    elif mutation == "other_org":
        details[records[1][0]] = (OTHER, None)
    with pytest.raises(ValueError, match=expected):
        assess(tmp_path, records, details, owner=owner, legacy=legacy, seeds=seeds)


def test_default_strict_mode_still_rejects_historical_deficits(tmp_path):
    records, _ = fixture_records(tmp_path)
    with pytest.raises(ValueError, match="unsafe or relative"):
        kb._reference_map(records, tmp_path, dict(kb._volume_files(tmp_path)))


def test_preserved_manifest_validate_shape_and_inventory(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "database.dump").write_bytes(b"database")
    sha, size = kb.file_hash(bundle / "database.dump")
    manifest = {
        "format": kb.PRESERVED_FORMAT,
        "artifacts": [{"path": "database.dump", "sha256": sha, "size_bytes": size}],
        "preservation": {
            "status": "preserved_with_integrity_deficits", "organization_id": OWNER,
            "verified_legacy_inline_rows": 1, "missing_raw_rows": 1,
            "reference_sha256": "a" * 64,
        },
    }
    path = bundle / "manifest.json"
    path.write_text(json.dumps(manifest))
    assert kb.verify_bundle(bundle, validate_pg_dump=False)["verified_artifacts"] == 1
    manifest["preservation"]["missing_raw_rows"] = 0
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="deficit count"):
        kb.verify_bundle(bundle, validate_pg_dump=False)
    manifest["preservation"]["missing_raw_rows"] = 1
    manifest["preservation"]["status"] = "isolated_restore_verified"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="preservation state"):
        kb.verify_bundle(bundle, validate_pg_dump=False)


def test_preserved_bundle_publishes_honest_status_and_retains_files(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "valid.txt").write_text("real", encoding="utf-8")
    valid_hash = digest("real")
    records, details = fixture_records(raw)
    records.append(("00000000-0000-0000-0000-000000000004", str(raw / "valid.txt"), valid_hash, False))
    monkeypatch.setattr(kb, "require_quiescent_database_connections", lambda: None)
    monkeypatch.setattr(kb, "snapshot_references", lambda: records)
    monkeypatch.setattr(kb, "unresolved_snapshot_details", lambda ids: {key: details[key] for key in ids})
    monkeypatch.setattr(kb, "historical_seed_hashes", lambda: frozenset({records[0][2]}))
    from scripts import backup_database
    def fake_backup(*, backup_directory):
        fake = backup_directory / "demo.dump"
        fake.write_bytes(b"dump")
        return fake
    monkeypatch.setattr(backup_database, "run_backup", fake_backup)
    monkeypatch.setattr(kb.subprocess, "run", lambda *a, **kw: None)
    archive = kb.create_bundle(raw, tmp_path / "backups", preserve=kb.PreservationAllowance(OWNER, 1, 1))
    manifest = json.loads((archive / "manifest.json").read_text())
    assert manifest["format"] == kb.PRESERVED_FORMAT
    assert manifest["preservation"]["status"] == "preserved_with_integrity_deficits"
    assert manifest["preservation"]["reference_sha256"] == kb.snapshot_reference_fingerprint(records)
    assert (archive / "snapshots" / "valid.txt").read_bytes() == b"real"
    assert kb.verify_bundle(archive, validate_pg_dump=False)["snapshot_files"] == 1


def test_isolated_restore_rechecks_and_reports_missing_evidence(tmp_path, monkeypatch):
    raw = tmp_path / "restored"
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "database.dump").write_bytes(b"dump")
    records, details = fixture_records(raw)
    entries = []
    for file in [bundle / "database.dump"]:
        digest_file, size = kb.file_hash(file)
        entries.append({"path": file.name, "sha256": digest_file, "size_bytes": size})
    manifest = {
        "format": kb.PRESERVED_FORMAT,
        "artifacts": entries,
        "snapshot_rows": len(records), "file_references": 0,
        "inline_snapshot_rows": 1, "orphan_volume_files": 0,
        "preservation": {
            "status": "preserved_with_integrity_deficits", "organization_id": OWNER,
            "verified_legacy_inline_rows": 1, "missing_raw_rows": 1,
            "reference_sha256": kb.snapshot_reference_fingerprint(records),
        }
    }
    (bundle / "manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(kb, "verify_bundle", lambda *args, **kw: {})
    monkeypatch.setattr(restore, "require_empty_target", lambda root: None)
    monkeypatch.setattr(restore.subprocess, "run", lambda *args, **kw: None)
    monkeypatch.setattr(kb, "snapshot_references", lambda: records)
    monkeypatch.setattr(kb, "unresolved_snapshot_details", lambda ids: {k: details[k] for k in ids})
    monkeypatch.setattr(kb, "historical_seed_hashes", lambda: frozenset({records[0][2]}))
    output = restore.restore_bundle(bundle, raw, "postgresql://sde_restore:p@restore_db:5432/sde_restore_drill")
    assert output["result"] == "isolated_restore_preserved_with_integrity_deficits"
    assert output["missing_raw_rows"] == 1
    assert output["verified_legacy_inline_rows"] == 1
    altered = records[:-1]
    monkeypatch.setattr(kb, "snapshot_references", lambda: altered)
    with pytest.raises(ValueError):
        restore.restore_bundle(bundle, raw, "postgresql://sde_restore:p@restore_db:5432/sde_restore_drill")
