from __future__ import annotations

from dataclasses import dataclass
import uuid

from sqlalchemy.orm import Session

from src.models.lineage_invalidation_execution_intent import (
    LineageInvalidationExecutionIntentRecord,
)
from src.services.lineage_authority_lock_service import (
    acquire_lineage_policy_authority_lock,
    acquire_lineage_target_authority_lock,
)
from src.services.lineage_invalidation_eligibility_service import (
    LineageInvalidationEligibilityReason,
    evaluate_lineage_invalidation_eligibility,
)
from src.services.lineage_invalidation_projection_service import (
    load_lineage_invalidation_decision_by_record_id,
)


@dataclass(frozen=True, slots=True)
class LineageInvalidationExecutionIntentResult:
    record: LineageInvalidationExecutionIntentRecord
    created: bool


class LineageInvalidationExecutionIntentError(RuntimeError):
    """Base error for execution intent authorization failures."""


class LineageInvalidationDecisionNotFoundError(
    LineageInvalidationExecutionIntentError
):
    """Raised when the requested persisted decision is unavailable."""


class LineageInvalidationExecutionNotEligibleError(
    LineageInvalidationExecutionIntentError
):
    def __init__(
        self,
        *,
        reason_code: LineageInvalidationEligibilityReason,
    ) -> None:
        self.reason_code = reason_code
        super().__init__(
            "Lineage invalidation decision is not eligible for execution: "
            f"{reason_code}."
        )


def authorize_lineage_invalidation_execution(
    db: Session,
    *,
    organization_id: uuid.UUID,
    decision_record_id: uuid.UUID,
) -> LineageInvalidationExecutionIntentResult:
    persisted = load_lineage_invalidation_decision_by_record_id(
        db,
        organization_id=organization_id,
        record_id=decision_record_id,
    )

    if persisted is None:
        raise LineageInvalidationDecisionNotFoundError(
            "Lineage invalidation decision was not found."
        )

    decision = persisted.decision

    if decision.disposition == "no_action":
        raise LineageInvalidationExecutionNotEligibleError(
            reason_code="no_action",
        )

    acquire_lineage_policy_authority_lock(
        db,
        organization_id=organization_id,
        policy_external_id=decision.policy.policy_external_id,
    )
    acquire_lineage_target_authority_lock(
        db,
        organization_id=organization_id,
        source_external_id=decision.target.source_external_id,
        asset_external_id=decision.target.asset_external_id,
    )

    persisted = load_lineage_invalidation_decision_by_record_id(
        db,
        organization_id=organization_id,
        record_id=decision_record_id,
    )

    if persisted is None:
        raise LineageInvalidationDecisionNotFoundError(
            "Lineage invalidation decision was not found."
        )

    decision = persisted.decision

    eligibility = evaluate_lineage_invalidation_eligibility(
        db,
        organization_id=organization_id,
        decision=decision,
    )

    if not eligibility.eligible:
        raise LineageInvalidationExecutionNotEligibleError(
            reason_code=eligibility.reason_code,
        )

    existing = (
        db.query(LineageInvalidationExecutionIntentRecord)
        .filter(
            LineageInvalidationExecutionIntentRecord.organization_id
            == organization_id,
            LineageInvalidationExecutionIntentRecord.decision_id
            == decision_record_id,
        )
        .one_or_none()
    )

    if existing is not None:
        return LineageInvalidationExecutionIntentResult(
            record=existing,
            created=False,
        )

    record = LineageInvalidationExecutionIntentRecord(
        organization_id=organization_id,
        decision_id=decision_record_id,
    )
    db.add(record)
    db.flush()

    return LineageInvalidationExecutionIntentResult(
        record=record,
        created=True,
    )
