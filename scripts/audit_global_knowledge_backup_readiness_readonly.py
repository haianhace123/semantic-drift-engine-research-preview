"""Read-only, all-tenant snapshot readiness audit before a maintenance window.

This preflight is *not* a database backup, a quiescence gate, or a recovery
attestation. It reports counts only: no snapshot IDs, document paths, customer
content, provider requests, or credentials are emitted.

Run in the knowledge_backup_maintenance image (which mounts snapshot_data
read-only) while the application is still running, before scheduling downtime.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable

from scripts import backup_knowledge_state as kb

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def inspect_snapshot_integrity(
    records: Iterable[tuple[str, str | None, str, bool]],
    root: Path,
    *,
    verify_hash: bool,
) -> dict[str, Any]:
    """Review precisely the full-table snapshot reference population.

    Unlike a per-organization audit, this checks the same *global* corpus that
    the knowledge bundle creator would attempt to archive. The result reports
    each blocking property by count and never includes row identifiers/paths.
    """
    canonical = kb._snapshot_root(root)
    files = dict(kb._volume_files(canonical))  # Reject link/partial artifacts.
    issue_counts: Counter[str] = Counter()
    total_rows = 0
    inline_only = 0
    file_reference_rows = 0
    referenced: dict[str, str] = {}
    checked_hashes: dict[str, str] = {}
    affected_rows = 0

    for _snapshot_id, path, digest, inline_content_present in records:
        total_rows += 1
        row_issues: set[str] = set()
        if not path:
            if inline_content_present:
                inline_only += 1
            else:
                row_issues.add("missing_raw_and_inline_content")
        else:
            file_reference_rows += 1
            candidate = Path(path)
            if not candidate.is_absolute() or candidate.is_symlink():
                row_issues.add("unsafe_or_relative_reference")
            else:
                resolved = candidate.resolve(strict=False)
                if resolved == canonical or not resolved.is_relative_to(canonical):
                    row_issues.add("outside_snapshot_root")
                else:
                    rel = resolved.relative_to(canonical).as_posix()
                    if rel not in files:
                        row_issues.add("missing_snapshot_artifact")
                    if not _SHA256.fullmatch(digest):
                        row_issues.add("invalid_sha256_metadata")
                    previous = referenced.get(rel)
                    if previous is not None and previous != digest:
                        row_issues.add("conflicting_sha256_references")
                    referenced.setdefault(rel, digest)
                    if verify_hash and rel in files and _SHA256.fullmatch(digest):
                        if rel not in checked_hashes:
                            checked_hashes[rel], _size = kb.file_hash(files[rel])
                        if checked_hashes[rel] != digest:
                            row_issues.add("sha256_mismatch")
        for issue in row_issues:
            issue_counts[issue] += 1
        if row_issues:
            affected_rows += 1

    blockers = bool(issue_counts)
    assessment = "blocked" if blockers else ("pass" if verify_hash else "hash_not_verified")
    return {
        "snapshot_integrity": assessment,
        "database_snapshot_rows": total_rows,
        "file_reference_rows": file_reference_rows,
        "inline_only_rows": inline_only,
        "referenced_unique_files": len(referenced),
        "volume_regular_files": len(files),
        "unreferenced_volume_files": len(set(files) - set(referenced)),
        "hashes_verified_files": len(checked_hashes),
        "affected_snapshot_rows": affected_rows,
        "issues": dict(sorted(issue_counts.items())),
        "database_writes": False,
        "backup_writes": False,
        "provider_calls": False,
        "quiescence_checked": False,
        "recovery_verified": False,
        "ready_to_execute_backup": False,
        "note": "Snapshot-only global inventory, not an approval to perform live backup or claim recovery.",
    }


def audit(root: Path, *, verify_hash: bool) -> dict[str, Any]:
    return inspect_snapshot_integrity(
        kb.snapshot_references(), root, verify_hash=verify_hash
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot-root", type=Path, default=Path("/app/data/raw"))
    parser.add_argument("--verify-hash", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = audit(args.snapshot_root, verify_hash=args.verify_hash)
        print(json.dumps({"mode": "read_only_global_snapshot_audit", **report}, sort_keys=True))
        # This diagnostic returns 0 for a successfully performed read-only
        # inspection, even when blockers are found. The JSON is authoritative.
        return 0
    except Exception as exc:
        # Paths, SQL errors, and filesystem exceptions may contain customer
        # data or connection strings. Never echo their message to the terminal.
        print(json.dumps({
            "mode": "read_only_global_snapshot_audit",
            "snapshot_integrity": "inspection_failed",
            "database_writes": False,
            "backup_writes": False,
            "provider_calls": False,
            "ready_to_execute_backup": False,
            "error_type": type(exc).__name__,
        }, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
