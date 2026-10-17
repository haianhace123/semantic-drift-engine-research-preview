from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
import uuid

from sqlalchemy import func, select
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


LineageInvalidationExecutionOutcome = Literal[
    "succeeded",
    "failed",
]


@dataclass(frozen=True, slots=True)
class LineageInvalidationExecutionResultPersistenceResult:
    record: LineageInvalidationExecutionResultRecord
    created: bool


class LineageInvalidationExecutionResultError(RuntimeError):
    """Base error for execution result persistence failures."""


class LineageInvalidationExecutionAttemptNotFoundError(
    LineageInvalidationExecutionResultError
):
    """Raised when the requested execution attempt is unavailable."""


class LineageInvalidationExecutionResultNotEligibleError(
    LineageInvalidationExecutionResultError
):
    def __init__(
        self,
        *,
        reason_code: LineageInvalidationEligibilityReason,
    ) -> None:
        self.reason_code = reason_code
        super().__init__(
            "Lineage invalidation execution attempt is not currently "
            f"eligible to publish a result: {reason_code}."
        )


class LineageInvalidationExecutionAttemptSupersededError(
    LineageInvalidationExecutionResultError
):
    def __init__(
        self,
        *,
        current_attempt_id: uuid.UUID,
    ) -> None:
        self.current_attempt_id = current_attempt_id
        super().__init__(
            "Lineage invalidation execution attempt has been superseded "
            "by a newer attempt."
        )


class LineageInvalidationExecutionAttemptLeaseExpiredError(
    LineageInvalidationExecutionResultError
):
    def __init__(
        self,
        *,
        lease_expires_at: datetime,
    ) -> None:
        self.lease_expires_at = lease_expires_at
        super().__init__(
            "Lineage invalidation execution attempt lease has expired."
        )


class LineageInvalidationExecutionResultConflictError(
    LineageInvalidationExecutionResultError
):
    def __init__(
        self,
        *,
        existing_outcome: str,
        requested_outcome: str,
    ) -> None:
        self.existing_outcome = existing_outcome
        self.requested_outcome = requested_outcome
        super().__init__(
            "Lineage invalidation execution attempt already has a "
            "different terminal result."
        )


class LineageInvalidationExecutionResultConfigurationError(
    LineageInvalidationExecutionResultError
):
    """Raised when execution result parameters are invalid."""


def persist_lineage_invalidation_execution_result(
    db: Session,
    *,
    organization_id: uuid.UUID,
    attempt_id: uuid.UUID,
    outcome: LineageInvalidationExecutionOutcome,
) -> LineageInvalidationExecutionResultPersistenceResult:
    """
    Persist one authoritative terminal result for an exact execution attempt.

    The execution intent row serializes result publication with reclaim.
    Policy and target authority locks then serialize publication-time
    eligibility with the corresponding authority writers.

    The caller owns commit / rollback. This function never performs an
    external side effect and never commits the caller transaction.
    """
    if outcome not in ("succeeded", "failed"):
        raise LineageInvalidationExecutionResultConfigurationError(
            "Execution result outcome must be 'succeeded' or 'failed'."
        )

    attempt = (
        db.query(LineageInvalidationExecutionAttemptRecord)
        .filter(
            LineageInvalidationExecutionAttemptRecord.organization_id
            == organization_id,
            LineageInvalidationExecutionAttemptRecord.id == attempt_id,
        )
        .one_or_none()
    )

    if attempt is None:
        raise LineageInvalidationExecutionAttemptNotFoundError(
            "Lineage invalidation execution attempt was not found."
        )

    intent = (
        db.query(LineageInvalidationExecutionIntentRecord)
        .filter(
            LineageInvalidationExecutionIntentRecord.organization_id
            == organization_id,
            LineageInvalidationExecutionIntentRecord.id == attempt.intent_id,
        )
        .with_for_update()
        .one_or_none()
    )

    if intent is None:
        raise LineageInvalidationExecutionAttemptNotFoundError(
            "Execution attempt intent was not found."
        )

    existing = (
        db.query(LineageInvalidationExecutionResultRecord)
        .filter(
            LineageInvalidationExecutionResultRecord.organization_id
            == organization_id,
            LineageInvalidationExecutionResultRecord.attempt_id == attempt.id,
        )
        .one_or_none()
    )

    if existing is not None:
        if existing.outcome != outcome:
            raise LineageInvalidationExecutionResultConflictError(
                existing_outcome=existing.outcome,
                requested_outcome=outcome,
            )

        return LineageInvalidationExecutionResultPersistenceResult(
            record=existing,
            created=False,
        )

    persisted = load_lineage_invalidation_decision_by_record_id(
        db,
        organization_id=organization_id,
        record_id=intent.decision_id,
    )

    if persisted is None:
        raise LineageInvalidationExecutionAttemptNotFoundError(
            "Execution attempt decision was not found."
        )

    decision = persisted.decision

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
        record_id=intent.decision_id,
    )

    if persisted is None:
        raise LineageInvalidationExecutionAttemptNotFoundError(
            "Execution attempt decision was not found."
        )

    decision = persisted.decision

    eligibility = evaluate_lineage_invalidation_eligibility(
        db,
        organization_id=organization_id,
        decision=decision,
    )

    if not eligibility.eligible:
        raise LineageInvalidationExecutionResultNotEligibleError(
            reason_code=eligibility.reason_code,
        )

    effective_now = db.execute(
        select(func.clock_timestamp())
    ).scalar_one()

    latest_attempt = (
        db.query(LineageInvalidationExecutionAttemptRecord)
        .filter(
            LineageInvalidationExecutionAttemptRecord.organization_id
            == organization_id,
            LineageInvalidationExecutionAttemptRecord.intent_id == intent.id,
        )
        .order_by(
            LineageInvalidationExecutionAttemptRecord.attempt_number.desc(),
        )
        .first()
    )

    if latest_attempt is None:
        raise LineageInvalidationExecutionAttemptNotFoundError(
            "Execution intent has no execution attempt."
        )

    if latest_attempt.id != attempt.id:
        raise LineageInvalidationExecutionAttemptSupersededError(
            current_attempt_id=latest_attempt.id,
        )

    if effective_now >= attempt.lease_expires_at:
        raise LineageInvalidationExecutionAttemptLeaseExpiredError(
            lease_expires_at=attempt.lease_expires_at,
        )

    record = LineageInvalidationExecutionResultRecord(
        organization_id=organization_id,
        attempt_id=attempt.id,
        outcome=outcome,
        recorded_at=effective_now,
    )
    db.add(record)
    db.flush()

    return LineageInvalidationExecutionResultPersistenceResult(
        record=record,
        created=True,
    )