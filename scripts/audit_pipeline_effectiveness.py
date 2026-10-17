"""Audit one persisted semantic drift event without causing execution.

Usage:
    python -m scripts.audit_pipeline_effectiveness \
        --organization-id UUID --event-id UUID --require-vector-risk

No external provider calls, queue processing, model modifications, or deletion.
"""

from __future__ import annotations

import argparse
import json
import sys
from uuid import UUID


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-id", type=UUID, required=True)
    parser.add_argument("--event-id", type=UUID, required=True)
    parser.add_argument(
        "--require-vector-risk", action="store_true",
        help="Exit nonzero if persisted snapshot->vector->Risk v2 evidence is incomplete.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    from sqlalchemy import text
    from src.core.database import SessionLocal
    from src.services.pipeline_effectiveness_evidence import (
        read_pipeline_event_evidence,
    )

    db = SessionLocal()
    try:
        # Fail closed if the database cannot enforce a read-only transaction.
        db.execute(text("SET TRANSACTION READ ONLY"))
        report = read_pipeline_event_evidence(
            db, organization_id=args.organization_id, event_id=args.event_id
        )
        print(json.dumps(report, indent=2, sort_keys=True))
        if args.require_vector_risk and not report["gates"]["vector_risk_complete"]:
            return 2
        return 0
    except LookupError as exc:
        print(json.dumps({"error": "event_not_found", "detail": str(exc)}), file=sys.stderr)
        return 1
    finally:
        db.rollback()
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
