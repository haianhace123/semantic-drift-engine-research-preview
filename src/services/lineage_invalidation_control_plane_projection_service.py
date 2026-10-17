from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy.orm import Session

from src.models.lineage_invalidation_execution_attempt import (
    LineageInvalidationExecutionAttemptRecord,
)
from src.models.lineage_invalidation_execution_intent import (
    LineageInvalidationExecutionIntentRecord,
)
from src.models.lineage_invalidation_execution_result import (
    LineageInvalidationExecutionResultRecord,
)
from src.services.lineage_invalidation_eligibility_service import (
    LineageInvalidationEligibilityReason,
    evaluate_lineage_invalidation_eligibility,
)
from src.services.lineage_invalidation_projection_service import (
    PersistedLineageInvalidationDecisionView,
    load_lineage_invalidation_decision_by_record_id,
)
from src.services.lineage_invalidation_execution_verification_projection_service import (
    LineageInvalidationExecutionVerificationView,
    load_lineage_invalidation_execution_verification_view,
)


LineageInvalidationExecutionOutcome = Literal[
    "succeeded",
    "failed",
]


@dataclass(frozen=True)
class LineageInvalidationEligibilityView:
    eligible: bool
    reason_code: LineageInvalidationEligibilityReason


@dataclass(frozen=True)
class LineageInvalidationExecutionIntentView:
    record_id: uuid.UUID
    authorized_at: datetime


@dataclass(frozen=True)
class LineageInvalidationExecutionAttemptView:
    record_id: uuid.UUID
    attempt_number: int
    binding_revision: int
    claimed_at: datetime
    lease_expires_at: datetime


@dataclass(frozen=True)
class LineageInvalidationExecutionResultView:
    record_id: uuid.UUID
    attempt_id: uuid.UUID
    outcome: LineageInvalidationExecutionOutcome
    recorded_at: datetime


@dataclass(frozen=True)
class LineageInvalidationExecutionView:
    intent: LineageInvalidationExecutionIntentView | None
    latest_attempt: LineageInvalidationExecutionAttemptView | None
    result: LineageInvalidationExecutionResultView | None
    verification: LineageInvalidationExecutionVerificationView | None


@dataclass(frozen=True)
class LineageInvalidationControlPlaneView:
    decision: PersistedLineageInvalidationDecisionView
    eligibility: LineageInvalidationEligibilityView
    execution: LineageInvalidationExecutionView


def load_lineage_invalidation_control_plane_view(
    db: Session,
    *,
    organization_id: uuid.UUID,
    decision_record_id: uuid.UUID,
) -> LineageInvalidationControlPlaneView | None:
    decision = load_lineage_invalidation_decision_by_record_id(
        db,
        organization_id=organization_id,
        record_id=decision_record_id,
    )

    if decision is None:
        return None

    eligibility_result = evaluate_lineage_invalidation_eligibility(
        db,
        organization_id=organization_id,
        decision=decision.decision,
    )
    eligibility = LineageInvalidationEligibilityView(
        eligible=eligibility_result.eligible,
        reason_code=eligibility_result.reason_code,
    )

    intent_record = (
        db.query(LineageInvalidationExecutionIntentRecord)
        .filter(
            LineageInvalidationExecutionIntentRecord.organization_id
            == organization_id,
            LineageInvalidationExecutionIntentRecord.decision_id
            == decision_record_id,
        )
        .one_or_none()
    )

    if intent_record is None:
        return LineageInvalidationControlPlaneView(
            decision=decision,
            eligibility=eligibility,
            execution=LineageInvalidationExecutionView(
                intent=None,
                latest_attempt=None,
                result=None,
                verification=None,
            ),
        )

    intent = LineageInvalidationExecutionIntentView(
        record_id=intent_record.id,
        authorized_at=intent_record.authorized_at,
    )

    latest_attempt_record = (
        db.query(LineageInvalidationExecutionAttemptRecord)
        .filter(
            LineageInvalidationExecutionAttemptRecord.organization_id
            == organization_id,
            LineageInvalidationExecutionAttemptRecord.intent_id
            == intent_record.id,
        )
        .order_by(
            LineageInvalidationExecutionAttemptRecord.attempt_number.desc(),
            LineageInvalidationExecutionAttemptRecord.id.desc(),
        )
        .first()
    )

    if latest_attempt_record is None:
        return LineageInvalidationControlPlaneView(
            decision=decision,
            eligibility=eligibility,
            execution=LineageInvalidationExecutionView(
                intent=intent,
                latest_attempt=None,
                result=None,
                verification=None,
            ),
        )

    latest_attempt = LineageInvalidationExecutionAttemptView(
        record_id=latest_attempt_record.id,
        attempt_number=int(latest_attempt_record.attempt_number),
        binding_revision=int(latest_attempt_record.binding_revision),
        claimed_at=latest_attempt_record.claimed_at,
        lease_expires_at=latest_attempt_record.lease_expires_at,
    )

    result_record = (
        db.query(LineageInvalidationExecutionResultRecord)
        .filter(
            LineageInvalidationExecutionResultRecord.organization_id
            == organization_id,
            LineageInvalidationExecutionResultRecord.attempt_id
            == latest_attempt_record.id,
        )
        .one_or_none()
    )

    result = None
    verification = None

    if result_record is not None:
        result = LineageInvalidationExecutionResultView(
            record_id=result_record.id,
            attempt_id=result_record.attempt_id,
            outcome=result_record.outcome,
            recorded_at=result_record.recorded_at,
        )

        if result_record.outcome == "succeeded":
            verification = (
                load_lineage_invalidation_execution_verification_view(
                    db,
                    organization_id=organization_id,
                    decision=decision,
                    execution_result_id=result_record.id,
                )
            )

    return LineageInvalidationControlPlaneView(
        decision=decision,
        eligibility=eligibility,
        execution=LineageInvalidationExecutionView(
            intent=intent,
            latest_attempt=latest_attempt,
            result=result,
            verification=verification,
        ),
    )
