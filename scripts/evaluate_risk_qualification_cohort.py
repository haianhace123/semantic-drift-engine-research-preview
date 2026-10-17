"""Tenant-scoped, read-only Risk v2/v3 cohort qualification. No provider calls."""

from __future__ import annotations

import argparse
import json
import os
from uuid import UUID

from src.services.risk_qualification_cohort_service import (
    MAX_EVENTS,
    load_qualification_cohort,
    summarize_risk_qualification,
)

OPT_IN = "SDE_RISK_QUALIFICATION_ALLOW_DB_READ"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--organization-id")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--include-review-event-ids", action="store_true")
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= MAX_EVENTS:
        parser.error("--limit must be between 1 and 5000")
    if not args.execute:
        if args.include_review_event_ids:
            parser.error("review ids require --execute")
        print(json.dumps({
            "audit": "risk_qualification_cohort_v1",
            "mode": "plan", "limit": args.limit,
            "scope": "latest v2 assessments, tenant-matched v3 pairing",
            "read_only": True, "provider_calls": 0,
            "ground_truth": "not available from assessments",
        }, indent=2, sort_keys=True))
        return 0

    if os.environ.get(OPT_IN) != "1":
        parser.error("--execute requires explicit read opt-in")
    try:
        org_id = UUID(args.organization_id or "")
    except ValueError:
        parser.error("--organization-id must be UUID")

    # Database imported after validation; denied runs never connect.
    from src.core.database import SessionLocal
    with SessionLocal() as db:
        try:
            db.connection().exec_driver_sql(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
            )
            baseline, candidate, truncated = load_qualification_cohort(
                db, organization_id=org_id, limit=args.limit
            )
            summary = summarize_risk_qualification(
                baseline, candidate, organization_id=org_id, truncated=truncated,
                include_review_event_ids=args.include_review_event_ids,
            )
        finally:
            db.rollback()
    print(json.dumps({
        "audit": "risk_qualification_cohort_v1", "mode": "read_only",
        "organization_id": str(org_id), "summary": summary,
        "limitations": [
            "Risk v2 defines the sampling cohort; v3-only events are excluded.",
            "Paired assessments are not independent ground truth.",
            "Topology flags describe available/comparable evidence, not its correctness.",
            "Removing confidence is an exposure counterfactual, not a replacement model.",
            "No severity quality or model promotion claim follows from this audit.",
            "Review IDs are emitted only by explicit opt-in; no content or snapshots are exported.",
        ],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
