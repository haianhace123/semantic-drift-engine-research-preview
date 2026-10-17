from __future__ import annotations

from dataclasses import dataclass
import uuid

from sqlalchemy.exc import IntegrityError
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
from src.models.lineage_invalidation_execution_verification import (
    LineageInvalidationExecutionVerificationRecord,
)
from src.models.lineage_observation import LineageObservationRecord
from src.services.lineage_invalidation_projection_service import (
    load_lineage_invalidation_decision_by_record_id,
)
from src.services.lineage_authority_lock_service import (
    acquire_lineage_target_authority_lock,
)
from src.services.lineage_projection_service import (
    load_current_lineage_observation,
)
from src.services.lineage_verification_evidence_contract import (
    qualifies_as_new_materialization_evidence,
)


@dataclass(frozen=True, slots=True)
class LineageInvalidationExecutionVerificationPersistenceResult:
    record: LineageInvalidationExecutionVerificationRecord
    created: bool


class LineageInvalidationExecutionVerificationError(RuntimeError):
    """Base error for execution verification persistence failures."""


class LineageInvalidationExecutionVerificationResultNotFoundError(
    LineageInvalidationExecutionVerificationError
):
    """Raised when the execution result is unavailable."""


class LineageInvalidationExecutionVerificationResultNotSuccessfulError(
    LineageInvalidationExecutionVerificationError
):
    """Raised when verification is requested for a non-successful result."""


class LineageInvalidationExecutionVerificationObservationNotFoundError(
    LineageInvalidationExecutionVerificationError
):
    """Raised when the verification observation is unavailable."""


class LineageInvalidationExecutionVerificationObservationInvalidError(
    LineageInvalidationExecutionVerificationError
):
    """Raised when an observation cannot verify the execution target."""


class LineageInvalidationExecutionVerificationConflictError(
    LineageInvalidationExecutionVerificationError
):
    def __init__(
        self,
        *,
        existing_observation_id: uuid.UUID,
        requested_observation_id: uuid.UUID,
    ) -> None:
        self.existing_observation_id = existing_observation_id
        self.requested_observation_id = requested_observation_id
        super().__init__(
            "Lineage invalidation execution result already has a "
            "different verification observation."
        )


def persist_lineage_invalidation_execution_verification(
    db: Session,
    *,
    organization_id: uuid.UUID,
    execution_result_id: uuid.UUID,
    observation_id: uuid.UUID,
) -> LineageInvalidationExecutionVerificationPersistenceResult:
    """
    Bind one successful execution result to explicit post-repair evidence.

    The observation must be a materialization observation for the exact
    target of the persisted invalidation decision.

    The caller must attest actual post-repair collection. This service
    enforces necessary DB ingestion and revision fences as well as current
    target authority; those checks alone do not establish physical repair.

    The caller owns commit / rollback.
    """
    existing = _load_verification(
        db,
        organization_id=organization_id,
        execution_result_id=execution_result_id,
    )

    if existing is not None:
        return _replay_result(
            existing=existing,
            requested_observation_id=observation_id,
        )

    execution_result = (
        db.query(LineageInvalidationExecutionResultRecord)
        .filter(
            LineageInvalidationExecutionResultRecord.organization_id
            == organization_id,
            LineageInvalidationExecutionResultRecord.id
            == execution_result_id,
        )
        .one_or_none()
    )

    if execution_result is None:
        raise LineageInvalidationExecutionVerificationResultNotFoundError(
            "Lineage invalidation execution result was not found."
        )

    if execution_result.outcome != "succeeded":
        raise (
            LineageInvalidationExecutionVerificationResultNotSuccessfulError(
                "Only a successful lineage invalidation execution result "
                "can accept verification evidence."
            )
        )

    observation = (
        db.query(LineageObservationRecord)
        .filter(
            LineageObservationRecord.organization_id == organization_id,
            LineageObservationRecord.id == observation_id,
        )
        .one_or_none()
    )

    if observation is None:
        raise LineageInvalidationExecutionVerificationObservationNotFoundError(
            "Lineage verification observation was not found."
        )

    attempt = (
        db.query(LineageInvalidationExecutionAttemptRecord)
        .filter(
            LineageInvalidationExecutionAttemptRecord.organization_id
            == organization_id,
            LineageInvalidationExecutionAttemptRecord.id
            == execution_result.attempt_id,
        )
        .one_or_none()
    )

    if attempt is None:
        raise LineageInvalidationExecutionVerificationResultNotFoundError(
            "Execution result attempt was not found."
        )

    intent = (
        db.query(LineageInvalidationExecutionIntentRecord)
        .filter(
            LineageInvalidationExecutionIntentRecord.organization_id
            == organization_id,
            LineageInvalidationExecutionIntentRecord.id
            == attempt.intent_id,
        )
        .one_or_none()
    )

    if intent is None:
        raise LineageInvalidationExecutionVerificationResultNotFoundError(
            "Execution result intent was not found."
        )

    persisted_decision = load_lineage_invalidation_decision_by_record_id(
        db,
        organization_id=organization_id,
        record_id=intent.decision_id,
    )

    if persisted_decision is None:
        raise LineageInvalidationExecutionVerificationResultNotFoundError(
            "Execution result decision was not found."
        )

    target = persisted_decision.decision.target

    if observation.observation_kind != "materialization":
        raise LineageInvalidationExecutionVerificationObservationInvalidError(
            "Verification evidence must be a materialization observation."
        )

    if (
        observation.source_external_id != target.source_external_id
        or observation.asset_external_id != target.asset_external_id
    ):
        raise LineageInvalidationExecutionVerificationObservationInvalidError(
            "Verification observation does not describe the execution "
            "decision target."
        )

    acquire_lineage_target_authority_lock(
        db,
        organization_id=organization_id,
        source_external_id=target.source_external_id,
        asset_external_id=target.asset_external_id,
    )

    if not qualifies_as_new_materialization_evidence(
        result_recorded_at=execution_result.recorded_at,
        observation_created_at=observation.created_at,
        invalidated_revision_external_id=target.revision_external_id,
        observation_revision_external_id=observation.revision_external_id,
    ):
        raise LineageInvalidationExecutionVerificationObservationInvalidError(
            "Verification requires a newly ingested materialization of "
            "a different target revision after execution result publication."
        )

    current = load_current_lineage_observation(
        db,
        organization_id=organization_id,
        source_external_id=target.source_external_id,
        asset_external_id=target.asset_external_id,
        observation_kind="materialization",
    )
    if (
        current is None
        or current.sequence != observation.sequence
        or current.revision_external_id != observation.revision_external_id
    ):
        raise LineageInvalidationExecutionVerificationObservationInvalidError(
            "Verification observation has been superseded by a newer "
            "target materialization."
        )

    record = LineageInvalidationExecutionVerificationRecord(
        organization_id=organization_id,
        execution_result_id=execution_result.id,
        observation_id=observation.id,
    )

    try:
        with db.begin_nested():
            db.add(record)
            db.flush()
    except IntegrityError:
        existing = _load_verification(
            db,
            organization_id=organization_id,
            execution_result_id=execution_result.id,
        )

        if existing is None:
            raise

        return _replay_result(
            existing=existing,
            requested_observation_id=observation.id,
        )

    return LineageInvalidationExecutionVerificationPersistenceResult(
        record=record,
        created=True,
    )


def _load_verification(
    db: Session,
    *,
    organization_id: uuid.UUID,
    execution_result_id: uuid.UUID,
) -> LineageInvalidationExecutionVerificationRecord | None:
    return (
        db.query(LineageInvalidationExecutionVerificationRecord)
        .filter(
            LineageInvalidationExecutionVerificationRecord.organization_id
            == organization_id,
            LineageInvalidationExecutionVerificationRecord.execution_result_id
            == execution_result_id,
        )
        .one_or_none()
    )


def _replay_result(
    *,
    existing: LineageInvalidationExecutionVerificationRecord,
    requested_observation_id: uuid.UUID,
) -> LineageInvalidationExecutionVerificationPersistenceResult:
    if existing.observation_id != requested_observation_id:
        raise LineageInvalidationExecutionVerificationConflictError(
            existing_observation_id=existing.observation_id,
            requested_observation_id=requested_observation_id,
        )

    return LineageInvalidationExecutionVerificationPersistenceResult(
        record=existing,
        created=False,
    )