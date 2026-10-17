"""Operator-only, read-only inventory before organization data lifecycle work.

This is NOT an erasure plan or a completeness attestation. It deliberately
reports unscoped tables and externally retained content as unknown rather
than claiming tenant-level deletion can be performed safely.

Run against the current API image, where the snapshot volume is mounted:
    python -m scripts.audit_organization_data_lifecycle_readonly --organization-id UUID
"""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from src.services.tenant_data_ownership_authority import (
    INDIRECT_OWNER_PATHS,
    indirect_owner_paths_are_valid,
    registered_ownership_metadata,
    summarize_indirect_ownership,
    table_scope,
)

MAX_HASH_BYTES = 20 * 1024 * 1024


def snapshot_location(path: str | None, raw_root: Path) -> tuple[str, Path | None]:
    """Never follow snapshot paths outside the canonical snapshot volume."""
    if not path:
        return "no_file_reference", None
    candidate = Path(path)
    windows = PureWindowsPath(path)
    if windows.drive.startswith("\\") or (not candidate.is_absolute() and (
        PurePosixPath(path).is_absolute() or bool(windows.anchor) or bool(windows.drive)
    )):
        # Foreign-platform absolute, drive-relative and rooted paths are unsafe,
        # never eligible for legacy relative-path attribution.
        return "outside_snapshot_root", None
    if not candidate.is_absolute():
        # Legacy relative paths are unresolved: never guess a working directory.
        return "relative_path_unverified", None
    root = raw_root.resolve()
    if not candidate.is_relative_to(root):
        # Refuse external locations without probing their existence or symlinks.
        return "outside_snapshot_root", None
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root) or resolved == root:
        return "outside_snapshot_root", None
    return "in_snapshot_root", resolved


def snapshot_file_state(
    path: str | None, content_hash: str | None, raw_root: Path,
    *, verify_hash: bool = False,
) -> str:
    kind, location = snapshot_location(path, raw_root)
    if kind != "in_snapshot_root":
        return kind
    assert location is not None
    if not location.is_file():
        return "missing_file"
    if not verify_hash:
        return "present_unverified"
    try:
        if location.stat().st_size > MAX_HASH_BYTES:
            return "file_too_large_for_hash_audit"
    except OSError:
        return "unreadable_file"
    digest = hashlib.sha256()
    try:
        with location.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError:
        return "unreadable_file"
    return "hash_verified" if digest.hexdigest() == content_hash else "hash_mismatch"


def audit(*, organization_id: uuid.UUID, max_snapshots: int, verify_hash: bool) -> dict[str, Any]:
    # Lazy import so the pure inventory helpers can be tested without DB access.
    from sqlalchemy import func, inspect, select, text
    from sqlalchemy.orm import Session
    from src.core.database import engine
    from src.models import Organization, PageSnapshot

    if not (1 <= max_snapshots <= 5000):
        raise ValueError("max_snapshots must be between 1 and 5000")

    raw_root = Path(__file__).resolve().parents[1] / "data" / "raw"
    metadata = registered_ownership_metadata()
    table_descriptions = [table_scope(t) for t in sorted(metadata.tables.values(), key=lambda t: t.name)]
    direct = [t for t in metadata.tables.values() if "organization_id" in t.c]

    # The database explicitly rejects any write in this transaction.
    with Session(engine) as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        database_tables = set(inspect(session.connection()).get_table_names())
        mapped_tables = set(metadata.tables)
        missing_schema_tables = sorted(mapped_tables - database_tables)
        if missing_schema_tables:
            raise ValueError("Database schema is missing mapped tables; inventory blocked")
        unmapped_database_tables = sorted(database_tables - mapped_tables)

        exists = session.execute(
            select(Organization.id).where(Organization.id == organization_id)
        ).scalar_one_or_none()
        if exists is None:
            raise ValueError("Organization not found; no inventory produced")

        direct_counts = {}
        for table in sorted(direct, key=lambda t: t.name):
            direct_counts[table.name] = session.execute(
                select(func.count()).select_from(table).where(table.c.organization_id == organization_id)
            ).scalar_one()

        indirect = summarize_indirect_ownership(session, metadata.tables, organization_id)

        total = session.execute(
            select(func.count()).select_from(PageSnapshot).where(
                PageSnapshot.organization_id == organization_id
            )
        ).scalar_one()
        rows = session.execute(
            select(PageSnapshot.raw_content_path, PageSnapshot.content_hash)
            .where(PageSnapshot.organization_id == organization_id)
            .order_by(PageSnapshot.crawled_at.desc(), PageSnapshot.id.desc())
            .limit(max_snapshots)
        ).all()
        # Report only aggregate states, never paths, documents, vectors or IDs.
        artifact_states: dict[str, int] = {}
        for path, digest in rows:
            state = snapshot_file_state(path, digest, raw_root, verify_hash=verify_hash)
            artifact_states[state] = artifact_states.get(state, 0) + 1

        # Indirect relationships need explicit tenant joins and consistency checks.
        # Avoid silently presenting a partially resolved inventory as complete.
        indirect_tables = [
            entry for entry in table_descriptions
            if entry["scope"] == "indirect_or_global_requires_review"
            and entry["table"] not in indirect["indirect_table_counts"]
        ]
        return {
            "mode": "read_only",
            "database_writes": False,
            "provider_calls": False,
            "organization_id": str(organization_id),
            "direct_table_counts": direct_counts,
            **indirect,
            "snapshot_artifacts": {
                "database_rows": total,
                "rows_inspected": len(rows),
                "inspection_complete": len(rows) == total,
                "content_hash_check_enabled": verify_hash,
                "states": dict(sorted(artifact_states.items())),
            },
            "model_tables": table_descriptions,
            "unmapped_database_tables": unmapped_database_tables,
            "unresolved_table_count": len(indirect_tables),
            "unresolved_tables": [entry["table"] for entry in indirect_tables],
            "scope_limitations": [
                "Indirect counts use only declared FK ownership paths, not cross-table guesses.",
                "Historical unattributed consent records cannot be assigned to a tenant.",
                "Cross-tenant correlations require investigation before lifecycle operations.",
                "Unmapped live tables require schema-specific organization ownership review.",
                "Historical relative snapshot paths cannot be verified automatically.",
                "Other organizations may share the same filesystem volume and PostgreSQL backups.",
                "Provider-side processing/retention, external deliveries, and caches are not measured.",
                "PostgreSQL backup archives do not contain raw snapshot files.",
                "No tenant deletion/export completeness claim is made by this report.",
            ],
            "erasure_ready": False,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-id", type=uuid.UUID, required=True)
    parser.add_argument("--max-snapshots", type=int, default=500)
    parser.add_argument("--verify-hash", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = audit(
            organization_id=args.organization_id,
            max_snapshots=args.max_snapshots,
            verify_hash=args.verify_hash,
        )
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Inventory unavailable: {exc}\n")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
