"""Operator-only PostgreSQL + snapshot-volume backup boundary.

A knowledge bundle is NOT a complete DR image: Redis, third-party provider data,
external delivery effects, and off-host durability need separate recovery plans.
Writers must be stopped externally before --execute is permitted. No live
application process is paused or resumed by this script.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


FORMAT = "sde-knowledge-bundle-v1"
PRESERVED_FORMAT = "sde-knowledge-bundle-preserved-v1"
SEED_RELATIVE_PATH = re.compile(r"data/raw/[^/]+_(?:before|after)\.txt\Z")
SHA256_PATTERN = re.compile(r"[a-f0-9]{64}\Z")
BUFFER_SIZE = 1024 * 1024
BUNDLE_DIRNAME = "knowledge-bundles"


def file_hash(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        while block := stream.read(BUFFER_SIZE):
            digest.update(block)
            total += len(block)
    return digest.hexdigest(), total


def _safe_relative(value: str) -> Path:
    """Validate paths supplied by an untrusted manifest before using them."""
    p = PurePosixPath(value)
    if (
        not value or value.startswith("/") or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or any(part in ("", ".", "..") for part in value.split("/"))
        or p.as_posix() != value
    ):
        raise ValueError("Unsafe relative path in knowledge bundle")
    return Path(*p.parts)


def _snapshot_root(root: Path) -> Path:
    if root.is_symlink() or not root.is_dir():
        raise ValueError("Snapshot root must be an existing, non-symlink directory")
    return root.resolve(strict=True)


def _volume_files(root: Path) -> list[tuple[str, Path]]:
    """Inventory all files, including unreferenced files, without following links."""
    canonical = _snapshot_root(root)
    result = []
    for file in sorted(canonical.rglob("*")):
        mode = file.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise ValueError("Snapshot volume contains link or unsupported file type")
        if stat.S_ISDIR(mode):
            continue
        if file.name.endswith(".tmp") or file.name.endswith(".partial"):
            raise ValueError("Incomplete snapshot artifact; writers are not quiescent")
        rel = file.relative_to(canonical).as_posix()
        _safe_relative(rel)
        result.append((rel, file))
    return result


def snapshot_references() -> list[tuple[str, str | None, str, bool]]:
    """Read only the identifying fields of all tenants, never document bodies."""
    from sqlalchemy import func, select, text
    from sqlalchemy.orm import Session
    from src.core.database import engine
    from src.models import PageSnapshot

    with Session(engine) as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        rows = session.execute(
            select(
                PageSnapshot.id,
                PageSnapshot.raw_content_path,
                PageSnapshot.content_hash,
                (func.length(func.trim(PageSnapshot.raw_content)) > 0),
            ).order_by(PageSnapshot.id)
        )
        return [(str(id_), path, str(digest), bool(inline)) for id_, path, digest, inline in rows]


def _reference_map(
    records: list[tuple[str, str | None, str, bool]],
    root: Path,
    files: dict[str, Path],
) -> tuple[dict[str, str], int]:
    """Fail closed if any PostgreSQL reference cannot be recovered."""
    canonical = _snapshot_root(root)
    mapping = {}
    inline_count = 0
    for _, path, digest, inline in records:
        if not path:
            if not inline:
                raise ValueError("Snapshot has neither raw file nor inline content")
            inline_count += 1
            continue
        candidate = Path(path)
        if not candidate.is_absolute() or candidate.is_symlink():
            raise ValueError("Snapshot has unsafe or relative raw_content_path")
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(canonical) or resolved == canonical:
            raise ValueError("Snapshot points outside snapshot volume")
        rel = resolved.relative_to(canonical).as_posix()
        if rel not in files:
            raise ValueError("Referenced snapshot file is missing")
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Referenced snapshot has invalid SHA-256 metadata")
        if rel in mapping and mapping[rel] != digest:
            raise ValueError("Conflicting content hashes for the same snapshot file")
        mapping[rel] = digest
    return mapping, inline_count


@dataclass(frozen=True)
class PreservationAllowance:
    """One explicitly scoped exception for already-attributed historical losses."""
    organization_id: str
    verified_legacy_inline_rows: int
    missing_raw_rows: int


def snapshot_reference_fingerprint(records: list[tuple[str, str | None, str, bool]]) -> str:
    """Pin exact source/restore reference identities without publishing source paths."""
    stable = json.dumps(sorted(records, key=lambda item: item[0]), separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()


def historical_seed_hashes() -> frozenset[str]:
    from scripts.audit_seed_provenance_readonly import SEED_TRANSITIONS
    return frozenset(
        hashlib.sha256(value.encode("utf-8")).hexdigest()
        for before, after, _ in SEED_TRANSITIONS for value in (before, after)
    )


def unresolved_snapshot_details(snapshot_ids: list[str]) -> dict[str, tuple[str, str | None]]:
    """Load bytes only for unresolved references; never serialize content to output."""
    if not snapshot_ids:
        return {}
    from sqlalchemy import select, text
    from sqlalchemy.orm import Session
    from src.core.database import engine
    from src.models import PageSnapshot
    from uuid import UUID

    result = {}
    with Session(engine) as session:
        session.execute(text("SET TRANSACTION READ ONLY"))
        for offset in range(0, len(snapshot_ids), 100):
            ids = [UUID(key) for key in snapshot_ids[offset:offset + 100]]
            rows = session.execute(select(
                PageSnapshot.id, PageSnapshot.organization_id, PageSnapshot.raw_content
            ).where(PageSnapshot.id.in_(ids)))
            for snapshot_id, organization_id, content in rows:
                result[str(snapshot_id)] = (str(organization_id), content)
    if len(result) != len(snapshot_ids):
        raise ValueError("Preservation inspection could not load all snapshot rows")
    return result


def assess_preserved_references(
    records: list[tuple[str, str | None, str, bool]],
    root: Path,
    files: dict[str, Path],
    *,
    allowance: PreservationAllowance,
    details: dict[str, tuple[str, str | None]],
    seed_hashes: frozenset[str],
) -> tuple[dict[str, str], int, dict[str, int]]:
    """Permit only exact historical seed bytes and explicitly missing raw files.

    This is *not* a repair: raw_content_path and the original hash are preserved.
    Missing evidence remains missing following restoration.
    """
    from uuid import UUID

    if str(UUID(allowance.organization_id)) != allowance.organization_id:
        raise ValueError("Preservation requires canonical organization UUID")
    if allowance.verified_legacy_inline_rows < 1 or allowance.missing_raw_rows < 1:
        raise ValueError("Explicit positive expected historical deficit counts required")
    canonical = _snapshot_root(root)
    valid_refs: dict[str, str] = {}
    inline_count = 0
    counters = {"verified_legacy_inline_rows": 0, "missing_raw_rows": 0}
    needing_details: set[str] = set()
    for snapshot_id, path, digest, inline in records:
        if not path:
            if not inline:
                raise ValueError("Snapshot has neither raw file nor inline content")
            inline_count += 1
            continue
        candidate = Path(path)
        if candidate.is_absolute():
            if candidate.is_symlink():
                raise ValueError("Unsafe referenced raw artifact")
            resolved = candidate.resolve(strict=False)
            if not resolved.is_relative_to(canonical) or resolved == canonical:
                raise ValueError("Snapshot points outside snapshot volume")
            relative = resolved.relative_to(canonical).as_posix()
            if relative in files:
                if not SHA256_PATTERN.fullmatch(digest):
                    raise ValueError("Referenced snapshot has invalid SHA-256 metadata")
                if relative in valid_refs and valid_refs[relative] != digest:
                    raise ValueError("Conflicting hashes for the same raw artifact")
                valid_refs[relative] = digest
                continue
            bucket = "missing_raw_rows"
        else:
            if not SEED_RELATIVE_PATH.fullmatch(path):
                raise ValueError("Unrecognized historical relative raw path")
            bucket = "verified_legacy_inline_rows"
        needing_details.add(snapshot_id)
        if snapshot_id not in details:
            raise ValueError("Unresolved snapshot missing provenance inspection")
        owner_id, raw_content = details[snapshot_id]
        if owner_id != allowance.organization_id or not SHA256_PATTERN.fullmatch(digest):
            raise ValueError("Preservation exception is not limited to the approved organization")
        if bucket == "verified_legacy_inline_rows":
            if digest not in seed_hashes or not isinstance(raw_content, str):
                raise ValueError("Historical seed bytes or fingerprint not confirmed")
            if hashlib.sha256(raw_content.encode("utf-8")).hexdigest() != digest:
                raise ValueError("Historical inline bytes do not match persisted SHA-256")
        else:
            if raw_content is not None and raw_content.strip():
                raise ValueError("Missing artifact unexpectedly has inline content")
        counters[bucket] += 1
    if set(details) != needing_details:
        raise ValueError("Preservation detail set does not match unresolved references")
    if (counters["verified_legacy_inline_rows"] != allowance.verified_legacy_inline_rows
            or counters["missing_raw_rows"] != allowance.missing_raw_rows):
        raise ValueError("Historical evidence deficits differ from explicitly approved counts")
    return valid_refs, inline_count, counters


def unresolved_ids(
    records: list[tuple[str, str | None, str, bool]], root: Path, files: dict[str, Path]
) -> list[str]:
    canonical = _snapshot_root(root)
    result = []
    for snapshot_id, path, _digest, _inline in records:
        if not path:
            continue
        candidate = Path(path)
        if not candidate.is_absolute():
            result.append(snapshot_id)
        elif candidate.resolve(strict=False).is_relative_to(canonical) and (
            candidate.resolve(strict=False).relative_to(canonical).as_posix() not in files
        ):
            result.append(snapshot_id)
    return result


def _copy_verified(source: Path, target: Path) -> tuple[str, int]:
    before = source.stat()
    if not stat.S_ISREG(before.st_mode) or source.is_symlink():
        raise ValueError("Snapshot artifact is not a regular file")
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    count = 0
    with source.open("rb") as inp, target.open("xb") as out:
        while block := inp.read(BUFFER_SIZE):
            out.write(block)
            digest.update(block)
            count += len(block)
        out.flush()
        os.fsync(out.fileno())
    after = source.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (
        after.st_size, after.st_mtime_ns, after.st_ino
    ) or count != before.st_size:
        raise ValueError("Snapshot file changed during backup")
    return digest.hexdigest(), count


def verify_bundle(bundle: Path, *, validate_pg_dump: bool = True) -> dict[str, int]:
    """Verify the bundle's whole on-disk inventory, not merely a manifest row."""
    if bundle.is_symlink() or not bundle.is_dir():
        raise ValueError("Knowledge bundle directory is missing or unsafe")
    manifest_path = bundle / "manifest.json"
    if manifest_path.is_symlink():
        raise ValueError("Manifest symlink is not permitted")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") not in (FORMAT, PRESERVED_FORMAT):
        raise ValueError("Unsupported knowledge bundle manifest format")
    if manifest["format"] == PRESERVED_FORMAT:
        integrity = manifest.get("preservation")
        if not isinstance(integrity, dict) or set(integrity) != {
            "organization_id", "verified_legacy_inline_rows", "missing_raw_rows",
            "reference_sha256", "status",
        }:
            raise ValueError("Incomplete preservation integrity manifest")
        from uuid import UUID
        try:
            if str(UUID(integrity["organization_id"])) != integrity["organization_id"]:
                raise ValueError("Noncanonical preserved organization UUID")
        except (TypeError, AttributeError, ValueError) as exc:
            raise ValueError("Invalid preservation organization UUID") from None
        if integrity["status"] != "preserved_with_integrity_deficits" or not SHA256_PATTERN.fullmatch(str(integrity["reference_sha256"])):
            raise ValueError("Invalid preservation state or reference fingerprint")
        for key in ("verified_legacy_inline_rows", "missing_raw_rows"):
            if type(integrity[key]) is not int or integrity[key] <= 0:
                raise ValueError("Invalid preserved evidence deficit count")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("Missing knowledge bundle artifact inventory")
    expected = {"manifest.json"}
    files = 0
    for entry in artifacts:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ValueError("Malformed artifact record")
        relative = _safe_relative(entry["path"])
        name = relative.as_posix()
        if name in expected or (name != "database.dump" and not name.startswith("snapshots/")):
            raise ValueError("Duplicate or unexpected artifact path")
        expected.add(name)
        # Do not follow a symlink in any component of an artifact path.
        current = bundle
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("Symlinks inside knowledge bundles are forbidden")
        target = bundle / relative
        if not target.is_file():
            raise ValueError("Knowledge bundle artifact is missing or unsafe")
        sha, count = file_hash(target)
        if sha != entry.get("sha256") or count != entry.get("size_bytes"):
            raise ValueError("Knowledge bundle artifact hash/size mismatch")
        files += 1
    if "database.dump" not in expected:
        raise ValueError("Bundle has no PostgreSQL dump")
    actual = set()
    for candidate in bundle.rglob("*"):
        mode = candidate.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise ValueError("Knowledge bundle contains unsafe objects")
        if stat.S_ISREG(mode):
            actual.add(candidate.relative_to(bundle).as_posix())
    if actual != expected:
        raise ValueError("Bundle contains unrecorded or missing files")
    if validate_pg_dump:
        subprocess.run(
            ["pg_restore", "--list", str(bundle / "database.dump")],
            check=True, stdout=subprocess.DEVNULL,
        )
    return {"verified_artifacts": files, "snapshot_files": files - 1}



def release_snapshot_reader_pool() -> None:
    """Close idle reader connections owned by this maintenance process.

    snapshot_references() and unresolved_snapshot_details() use the global
    SQLAlchemy QueuePool. Closing a Session returns its connection to the pool,
    leaving a PostgreSQL *client backend* visible to the final connection gate.
    Dispose the pool only after all snapshot reads have finished. A checked-out
    connection is not forcibly closed and remains visible to the fail-closed
    gate, while a missing module means no snapshot reader pool was opened.
    """
    module = sys.modules.get("src.core.database")
    if module is not None:
        module.engine.dispose()


def require_quiescent_database_connections() -> None:
    """Enforce an independent PostgreSQL connection gate inside execution.

    Operator acknowledgement and an earlier preflight are not sufficient:
    a backup must fail closed when other clients remain or the observer cannot
    verify full session visibility. This check does not fence new connections,
    so a controlled maintenance window remains mandatory.
    """
    from scripts.check_knowledge_backup_connections_readonly import inspect

    try:
        report = inspect(os.environ.get("DATABASE_URL", ""))
    except Exception:
        # Provider/library errors can include DATABASE_URL; never echo them in
        # operator logs or error records.
        raise ValueError("Knowledge backup refused: PostgreSQL session inspection failed") from None
    if report.get("connection_gate_pass") is not True:
        raise ValueError(
            "Knowledge backup refused: PostgreSQL still has client sessions "
            "or session visibility is insufficient"
        )


def create_bundle(root: Path, backup_root: Path, *, preserve: PreservationAllowance | None = None) -> Path:
    """Create an atomic, verified knowledge bundle; preserve prior archives."""
    from scripts import backup_database

    # This must be checked within --execute, even if the operator previously
    # ran a separate read-only preflight and acknowledged stopped writers.
    require_quiescent_database_connections()
    canonical = _snapshot_root(root)
    snapshots_before = snapshot_references()
    raw_files = dict(_volume_files(canonical))
    if preserve is None:
        expected_hashes, inline_count = _reference_map(snapshots_before, canonical, raw_files)
        preserved_counts = None
    else:
        ids = unresolved_ids(snapshots_before, canonical, raw_files)
        expected_hashes, inline_count, preserved_counts = assess_preserved_references(
            snapshots_before, canonical, raw_files, allowance=preserve,
            details=unresolved_snapshot_details(ids), seed_hashes=historical_seed_hashes(),
        )

    bundles = backup_root / BUNDLE_DIRNAME
    bundles.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(prefix=".incomplete-", dir=bundles))
    final = bundles / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "-" + uuid.uuid4().hex
    )
    try:
        original_dump = backup_database.run_backup(backup_directory=staged)
        dump_path = staged / "database.dump"
        original_dump.replace(dump_path)
        dump_hash, dump_size = file_hash(dump_path)
        entries: list[dict[str, Any]] = [{
            "path": "database.dump", "sha256": dump_hash, "size_bytes": dump_size,
        }]
        for rel, src in raw_files.items():
            hash_, size = _copy_verified(src, staged / "snapshots" / _safe_relative(rel))
            if rel in expected_hashes and expected_hashes[rel] != hash_:
                raise ValueError("Database snapshot digest does not match raw artifact")
            entries.append({
                "path": "snapshots/" + rel, "sha256": hash_, "size_bytes": size,
            })

        # Detect new/deleted/changed snapshot references or volume files. Writers
        # must still be stopped: only a stopped-writer window gives cross-store
        # recovery semantics. Unrelated DB mutations are not intercepted here.
        snapshots_after = snapshot_references()
        if snapshots_before != snapshots_after or raw_files.keys() != dict(_volume_files(canonical)).keys():
            raise ValueError("Snapshot inventory changed during knowledge backup")
        if preserve is not None:
            # Reference metadata can remain unchanged while the historical
            # inline bytes change. Verify them again after pg_dump/copy.
            _, _, counts_after = assess_preserved_references(
                snapshots_after, canonical, raw_files, allowance=preserve,
                details=unresolved_snapshot_details(unresolved_ids(snapshots_after, canonical, raw_files)),
                seed_hashes=historical_seed_hashes(),
            )
            if counts_after != preserved_counts:
                raise ValueError("Historical evidence changed during knowledge backup")
        # Catch a same-name mutation that happened after the artifact was copied.
        # This is still not a substitute for stopping every writer.
        verified_sources = {entry["path"][len("snapshots/"):]: entry["sha256"]
                            for entry in entries if entry["path"].startswith("snapshots/")}
        for rel, source in raw_files.items():
            current_digest, _ = file_hash(source)
            if current_digest != verified_sources[rel]:
                raise ValueError("Snapshot content changed during knowledge backup")
        manifest = {
            "format": PRESERVED_FORMAT if preserve else FORMAT,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "snapshot_rows": len(snapshots_before),
            "file_references": len(expected_hashes),
            "inline_snapshot_rows": inline_count,
            "orphan_volume_files": len(raw_files) - len(expected_hashes),
            "artifacts": entries,
            "limitations": "Does not include Redis, provider-side content, external delivery state or offsite storage.",
        }
        if preserve is not None:
            manifest["preservation"] = {
                "status": "preserved_with_integrity_deficits",
                "organization_id": preserve.organization_id,
                "verified_legacy_inline_rows": preserved_counts["verified_legacy_inline_rows"],
                "missing_raw_rows": preserved_counts["missing_raw_rows"],
                "reference_sha256": snapshot_reference_fingerprint(snapshots_before),
            }
        (staged / "manifest.json").write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        verify_bundle(staged)
        # Snapshot reads use the shared SQLAlchemy QueuePool. Release our own
        # idle connections before counting *other* database clients: without
        # this, a clean maintenance window fails on the backup process itself.
        release_snapshot_reader_pool()
        # Refuse publishing even a fully checksummed bundle if an application
        # session returned during the dump/copy window. This is a second
        # observation, not a transactional cross-store lock.
        require_quiescent_database_connections()
        staged.replace(final)
        return final
    finally:
        if staged.exists():
            shutil.rmtree(staged)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--execute", action="store_true")
    group.add_argument("--verify", type=Path)
    parser.add_argument("--acknowledge-writers-quiesced", action="store_true")
    parser.add_argument("--preserve-with-integrity-deficits", action="store_true")
    parser.add_argument("--preserve-organization-id", type=str)
    parser.add_argument("--expect-verified-legacy-inline", type=int)
    parser.add_argument("--expect-missing-raw", type=int)
    parser.add_argument("--snapshot-root", type=Path, default=Path(os.getenv("SNAPSHOT_ROOT", "/app/data/raw")))
    parser.add_argument("--backup-root", type=Path, default=Path(os.getenv("BACKUP_DIR", "/app/backups")))
    args = parser.parse_args(argv)
    try:
        if args.verify:
            summary = verify_bundle(args.verify)
            format_id = json.loads((args.verify / "manifest.json").read_text(encoding="utf-8"))["format"]
            print(json.dumps({
                "result": ("bundle_artifacts_verified_with_preserved_evidence_deficits"
                           if format_id == PRESERVED_FORMAT else "verified"),
                **summary,
            }, sort_keys=True))
        elif args.execute:
            if not args.acknowledge_writers_quiesced:
                parser.error("--execute requires --acknowledge-writers-quiesced")
            preserve = None
            if args.preserve_with_integrity_deficits:
                if (not args.preserve_organization_id or args.expect_verified_legacy_inline is None
                        or args.expect_missing_raw is None):
                    parser.error("Preservation requires organization and both expected deficit counts")
                preserve = PreservationAllowance(
                    args.preserve_organization_id,
                    args.expect_verified_legacy_inline,
                    args.expect_missing_raw,
                )
            elif (args.preserve_organization_id is not None
                  or args.expect_verified_legacy_inline is not None
                  or args.expect_missing_raw is not None):
                parser.error("Preservation parameters require --preserve-with-integrity-deficits")
            dest = create_bundle(args.snapshot_root, args.backup_root, preserve=preserve)
            print(json.dumps({
                "result": "preserved_with_integrity_deficits" if preserve else "created_and_verified",
                "bundle": str(dest),
            }, sort_keys=True))
        else:
            print(json.dumps({
                "mode": "plan_only", "database_writes": False, "backup_writes": False,
                "requires": "Stop all application and external DB/snapshot writers before execution; restore drill still required.",
            }, sort_keys=True))
        return 0
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Knowledge backup refused or verification failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
