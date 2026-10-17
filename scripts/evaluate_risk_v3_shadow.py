from __future__ import annotations

import argparse
import json
import uuid
from dataclasses import asdict

from src.core.database import SessionLocal
from src.services.risk_shadow_evaluation_service import (
    evaluate_persisted_risk_shadow,
)
from src.services.risk_v3_promotion_service import (
    summarize_risk_v3_qualification_evidence,
)


def _parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Evaluate persisted deterministic_v2 and "
            "deterministic_v3 risk assessments."
        )
    )
    parser.add_argument(
        "--organization-id",
        type=uuid.UUID,
        default=None,
        help=(
            "Limit evaluation to one organization. "
            "Default: all organizations."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    db = SessionLocal()

    try:
        result = evaluate_persisted_risk_shadow(
            db,
            organization_id=args.organization_id,
        )

        qualification_evidence = (
            summarize_risk_v3_qualification_evidence(result)
        )

        payload = {
            "coverage": asdict(
                result.coverage
            ),
            "evaluation": (
                asdict(result.evaluation)
                if result.evaluation is not None
                else None
            ),
            "qualification_evidence": asdict(
                qualification_evidence
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
