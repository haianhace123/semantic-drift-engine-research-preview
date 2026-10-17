from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import asdict

from src.core.database import SessionLocal
from src.services.risk_v3_requalification_service import (
    requalify_risk_v3,
)


def _parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Requalify deterministic Risk V3 from immutable "
            "historical evidence without persisting rebuilt state."
        )
    )
    parser.add_argument(
        "--organization-id",
        type=uuid.UUID,
        required=True,
        help="Organization whose Risk V3 evidence will be requalified.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    db = SessionLocal()

    try:
        result = requalify_risk_v3(
            db,
            organization_id=args.organization_id,
        )

        payload = {
            "organization_id": str(
                result.organization_id
            ),
            "eligible_event_count": (
                result.eligible_event_count
            ),
            "paired_event_count": (
                result.paired_event_count
            ),
            "invalid_event_count": (
                result.invalid_event_count
            ),
            "distinct_timestamp_count": (
                result.distinct_timestamp_count
            ),
            "evaluation": (
                asdict(result.evaluation)
                if result.evaluation is not None
                else None
            ),
            "qualification_evidence": asdict(
                result.qualification_evidence
            ),
        }

        print(
            json.dumps(
                payload,
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
