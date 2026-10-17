"""Tenant-scoped, read-only Risk v2/v3 confidence-contribution exposure audit."""

from __future__ import annotations

import argparse
import json
import os
from uuid import UUID

from src.services.risk_confidence_exposure_service import (
    MAX_ROWS_PER_VERSION,
    SUPPORTED_VERSIONS,
    load_scoped_assessments,
    summarize_confidence_exposure,
)

OPT_IN = "SDE_RISK_CONFIDENCE_AUDIT_ALLOW_DB_READ"


def validate_execution(*, execute: bool, organization_id: str | None, environ: dict[str, str]) -> UUID:
    if not execute or environ.get(OPT_IN) != "1":
        raise ValueError("DB audit requires --execute and explicit read opt-in")
    if not organization_id:
        raise ValueError("--organization-id is required for tenant isolation")
    try:
        return UUID(organization_id)
    except (ValueError, AttributeError) as exc:
        raise ValueError("--organization-id must be a UUID") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Read tenant-scoped persisted assessments")
    parser.add_argument("--organization-id", help="Required tenant organization UUID for execution")
    parser.add_argument("--limit", type=int, default=500, help="Latest assessments per scoring version (1..5000)")
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= MAX_ROWS_PER_VERSION:
        parser.error("--limit must be between 1 and 5000")

    if not args.execute:
        print(json.dumps({
            "audit": "risk_confidence_exposure_v1",
            "mode": "plan",
            "versions": list(SUPPORTED_VERSIONS),
            "latest_rows_per_version": args.limit,
            "tenant_scoped": True,
            "writes": False,
            "provider_calls": 0,
            "counterfactual": "persisted score minus confidence contribution; no redistribution",
        }, indent=2, sort_keys=True))
        return 0

    org_id = validate_execution(execute=True, organization_id=args.organization_id, environ=dict(os.environ))

    # Import database machinery only after validation so plan mode does not
    # connect and denied executions never open a connection.
    from src.core.database import SessionLocal

    with SessionLocal() as db:
        try:
            # PostgreSQL-level read-only enforcement. No database writes or
            # changes to scored events, assessments, risk versions or alerts.
            db.connection().exec_driver_sql("SET TRANSACTION READ ONLY")
            reports = []
            for version in SUPPORTED_VERSIONS:
                samples, truncated = load_scoped_assessments(
                    db, organization_id=org_id, risk_version=version, limit=args.limit,
                )
                reports.append(summarize_confidence_exposure(samples, risk_version=version, truncated=truncated))
        finally:
            db.rollback()

    print(json.dumps({
        "audit": "risk_confidence_exposure_v1",
        "mode": "read_only",
        "organization_id": str(org_id),
        "versions": reports,
        "limitations": [
            "Counterfactual removes the persisted confidence contribution without reallocating weights; it is not a new scoring model.",
            "Synthetic 0.90 detector confidence is not calibrated probability of correctness.",
            "Truncated versions represent only the latest sampled assessments, not the full population.",
            "No assessment, event, alert or downstream action was updated.",
        ],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
