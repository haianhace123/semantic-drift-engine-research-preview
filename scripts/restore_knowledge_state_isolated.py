"""Restore and verify one SDE knowledge bundle in a disposable isolated stack.

Only invoke via docker-compose.restore-isolated.yml. Never run this module
against the production database or snapshot volume. No network access to
external providers is required or allowed by the isolated Compose stack.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from scripts import backup_knowledge_state as kb
from scripts.postgres_cli_connection import parse_database_url, postgres_cli_environment

EXPECTED_TARGET = {
    "host": "restore_db",
    "port": "5432",
    "user": "sde_restore",
    "database": "sde_restore_drill",
}
BUNDLE_ID_PATTERN = re.compile(r"[0-9]{8}T[0-9]{12}Z-[a-f0-9]{32}\Z")


def validate_isolation(project: str, database_url: str, bundle_id: str) -> None:
    """Refuse an unsafe Compose identity or any non-isolated DB destination."""
    if not re.fullmatch(r"sde-restore-[a-z0-9-]{8,60}", project):
        raise ValueError("Restore requires a dedicated sde-restore-* project")
    connection = parse_database_url(database_url)
    if any(connection[key] != expected for key, expected in EXPECTED_TARGET.items()):
        raise ValueError("Restore target is not the dedicated isolated database")
    if not BUNDLE_ID_PATTERN.fullmatch(bundle_id):
        raise ValueError("Invalid knowledge bundle identifier")


def require_empty_target(raw_root: Path) -> None:
    """Check both target stores are clean before ANY restore operation."""
    from sqlalchemy import text
    from sqlalchemy.orm import Session
    from src.core.database import engine

    if raw_root.is_symlink():
        raise ValueError("Isolated snapshot root cannot be a symlink")
    if raw_root.exists() and (not raw_root.is_dir() or any(raw_root.iterdir())):
        raise ValueError("Isolated snapshot volume must be empty")
    with Session(engine) as session:
        # Fresh database has no tables in public; extension objects do not count.
        count = session.execute(text(
            "SELECT count(*) FROM pg_catalog.pg_tables WHERE schemaname = 'public'"
        )).scalar_one()
        if count:
            raise ValueError("Isolated PostgreSQL database must be empty")


def restore_bundle(bundle: Path, raw_root: Path, database_url: str) -> dict[str, int | str]:
    """Copy hashed evidence, restore PG atomically, validate recovered references."""
    kb.verify_bundle(bundle)
    require_empty_target(raw_root)
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))

    # The bundle is read-only; the target path is a private, fresh volume.
    raw_root.mkdir(parents=True, exist_ok=True)
    copied = 0
    for entry in manifest["artifacts"]:
        relative = kb._safe_relative(entry["path"])
        if relative.parts[0] != "snapshots":
            continue
        target_relative = Path(*relative.parts[1:])
        source = bundle / relative
        destination = raw_root / target_relative
        checksum, size = kb._copy_verified(source, destination)
        if checksum != entry["sha256"] or size != entry["size_bytes"]:
            raise ValueError("Restored snapshot differs from manifest")
        copied += 1

    connection = parse_database_url(database_url)
    env = postgres_cli_environment(database_url, os.environ)
    subprocess.run([
        "pg_restore", "--no-owner", "--no-privileges", "--exit-on-error",
        "--single-transaction", "--host", connection["host"],
        "--port", connection["port"], "--username", connection["user"],
        "--dbname", connection["database"], str(bundle / "database.dump"),
    ], env=env, check=True, stdout=subprocess.DEVNULL)

    # A parseable pg_dump alone does not prove that raw evidence is usable.
    records = kb.snapshot_references()
    files = dict(kb._volume_files(raw_root))
    if manifest["format"] == kb.PRESERVED_FORMAT:
        policy = manifest["preservation"]
        allowance = kb.PreservationAllowance(
            policy["organization_id"], policy["verified_legacy_inline_rows"],
            policy["missing_raw_rows"],
        )
        references, inline_count, deficits = kb.assess_preserved_references(
            records, raw_root, files, allowance=allowance,
            details=kb.unresolved_snapshot_details(kb.unresolved_ids(records, raw_root, files)),
            seed_hashes=kb.historical_seed_hashes(),
        )
        if (kb.snapshot_reference_fingerprint(records) != policy["reference_sha256"]
                or deficits["missing_raw_rows"] != policy["missing_raw_rows"]):
            raise ValueError("Restored preserved evidence inventory changed")
    else:
        references, inline_count = kb._reference_map(records, raw_root, files)
    for relative, expected_hash in references.items():
        digest, _ = kb.file_hash(files[relative])
        if digest != expected_hash:
            raise ValueError("Restored DB snapshot reference has wrong content hash")
    if (len(records) != manifest["snapshot_rows"]
            or len(references) != manifest["file_references"]
            or inline_count != manifest["inline_snapshot_rows"]
            or len(files) != copied
            or len(files) - len(references) != manifest["orphan_volume_files"]):
        raise ValueError("Restored database and snapshot manifest disagree")
    return {
        "result": "isolated_restore_preserved_with_integrity_deficits"
                  if manifest["format"] == kb.PRESERVED_FORMAT
                  else "isolated_restore_verified",
        "snapshot_rows": len(records),
        "referenced_snapshot_files": len(references),
        "restored_snapshot_files": copied,
        "orphan_volume_files": len(files) - len(references),
        **({"missing_raw_rows": manifest["preservation"]["missing_raw_rows"],
            "verified_legacy_inline_rows": manifest["preservation"]["verified_legacy_inline_rows"]}
           if manifest["format"] == kb.PRESERVED_FORMAT else {}),
    }


def main() -> int:
    try:
        project = os.environ.get("SDE_RESTORE_PROJECT", "")
        database_url = os.environ.get("DATABASE_URL", "")
        bundle_id = os.environ.get("SDE_RESTORE_BUNDLE_ID", "")
        validate_isolation(project, database_url, bundle_id)
        source_root = Path(os.environ.get("SDE_RESTORE_BACKUP_ROOT", "/app/source-backups/knowledge-bundles"))
        raw_root = Path(os.environ.get("SDE_RESTORE_SNAPSHOT_ROOT", "/app/data/raw"))
        # Pin the only two permitted container filesystem boundaries.
        if source_root != Path("/app/source-backups/knowledge-bundles") or raw_root != Path("/app/data/raw"):
            raise ValueError("Unexpected backup or snapshot mount path")
        bundle = source_root / bundle_id
        if bundle.is_symlink() or not bundle.is_dir():
            raise ValueError("Requested knowledge bundle does not exist")
        print(json.dumps(restore_bundle(bundle, raw_root, database_url), sort_keys=True))
        return 0
    except (ValueError, OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Isolated restore refused or failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
