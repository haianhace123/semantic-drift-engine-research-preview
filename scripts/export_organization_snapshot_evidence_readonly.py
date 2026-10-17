"""Export a bounded operator-only snapshot evidence manifest (not full data export).

Example inside an approved dev/test Compose API container:
  python -m scripts.export_organization_snapshot_evidence_readonly \\
    --organization-id UUID --output /operator-output/evidence.json

Output location must be OUTSIDE the repository; never put client data in Git.
No credential, document body, vector, URL, or raw path appears in output.
"""
from __future__ import annotations

import argparse
import json
import os
import uuid
from pathlib import Path


def _write_private_new_file(path: Path, content: str) -> None:
    root = Path(__file__).resolve().parents[1]
    target = path.expanduser().absolute()
    parent = target.parent.resolve(strict=True)
    if not target.is_absolute() or parent.is_relative_to(root):
        raise ValueError("Output must be outside source repository")
    if target.exists() or target.is_symlink():
        raise FileExistsError("Refusing to overwrite an existing output")
    if not parent.is_dir():
        raise ValueError("Output parent must be a directory")
    fd = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        target.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--organization-id", type=uuid.UUID, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--limit", type=int, default=200)
    args = p.parse_args(argv)
    if not args.output.is_absolute():
        p.error("--output must be an absolute path")
    if not 1 <= args.limit <= 200:
        p.error("--limit must be between 1 and 200")
    try:
        from src.core.database import engine
        from src.services.organization_evidence_manifest_service import (
            build_operator_snapshot_evidence_manifest,
        )
        report = build_operator_snapshot_evidence_manifest(
            engine, organization_id=args.organization_id, limit=args.limit
        )
        _write_private_new_file(
            args.output,
            json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        )
    except (Exception,) as exc:
        # Keep DB error text, paths and organization UUID out of CLI logs.
        print("SDE_SNAPSHOT_EVIDENCE_EXPORT_FAILED")
        print("ERROR_CLASS:", type(exc).__name__)
        return 2
    print("SDE_SNAPSHOT_EVIDENCE_MANIFEST_WRITTEN")
    print("SCOPE: SNAPSHOT_METADATA_ONLY")
    print("TRUNCATED:", report["status"] == "TRUNCATED")
    print("RECORDS_RETURNED:", report["records_returned"])
    print("FULL_ORGANIZATION_EXPORT: False")
    print("NO_DATABASE_WRITES_OR_PROVIDER_REQUESTS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
