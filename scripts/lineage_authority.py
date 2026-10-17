from __future__ import annotations

import argparse
import uuid

from src.core.database import SessionLocal
from src.domain.lineage_invalidation import (
    LineageInvalidationPolicyIdentity,
)
from src.services.lineage_invalidation_execution_intent_service import (
    authorize_lineage_invalidation_execution,
)
from src.services.lineage_invalidation_policy_activation_service import (
    activate_lineage_invalidation_policy,
)


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid UUID: {value}"
        ) from exc


def _non_negative_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid integer: {value}"
        ) from exc

    if parsed < 0:
        raise argparse.ArgumentTypeError(
            "Revision must not be negative."
        )

    return parsed


def _activate_policy(args: argparse.Namespace) -> int:
    db = SessionLocal()

    try:
        result = activate_lineage_invalidation_policy(
            db,
            organization_id=args.organization_id,
            policy=LineageInvalidationPolicyIdentity(
                policy_external_id=args.policy_external_id,
                revision=args.revision,
            ),
        )
        db.commit()

        print(
            "Lineage invalidation policy authority: "
            f"organization_id={result.state.organization_id} "
            f"policy_external_id={result.state.policy.policy_external_id} "
            f"revision={result.state.policy.revision} "
            f"outcome={result.outcome}"
        )
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _authorize_execution(args: argparse.Namespace) -> int:
    db = SessionLocal()

    try:
        result = authorize_lineage_invalidation_execution(
            db,
            organization_id=args.organization_id,
            decision_record_id=args.decision_id,
        )
        db.commit()

        print(
            "Lineage invalidation execution authority: "
            f"organization_id={result.record.organization_id} "
            f"decision_id={result.record.decision_id} "
            f"intent_id={result.record.id} "
            f"created={str(result.created).lower()}"
        )
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Manage explicit Semantic Drift Engine lineage "
            "invalidation authority."
        ),
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    activate_policy_parser = subparsers.add_parser(
        "activate-policy",
        help="Activate an invalidation policy revision.",
    )
    activate_policy_parser.add_argument(
        "--organization-id",
        required=True,
        type=_uuid,
    )
    activate_policy_parser.add_argument(
        "--policy-external-id",
        required=True,
    )
    activate_policy_parser.add_argument(
        "--revision",
        required=True,
        type=_non_negative_integer,
    )
    activate_policy_parser.set_defaults(
        handler=_activate_policy,
    )

    authorize_execution_parser = subparsers.add_parser(
        "authorize-execution",
        help="Authorize an eligible persisted invalidation decision.",
    )
    authorize_execution_parser.add_argument(
        "--organization-id",
        required=True,
        type=_uuid,
    )
    authorize_execution_parser.add_argument(
        "--decision-id",
        required=True,
        type=_uuid,
    )
    authorize_execution_parser.set_defaults(
        handler=_authorize_execution,
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())