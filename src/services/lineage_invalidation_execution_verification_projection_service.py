from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
import uuid

from sqlalchemy.orm import Session, joinedload

from src.domain.lineage_revision import LineageRevisionIdentity
from src.models.lineage_invalidation_execution_verification import (
    LineageInvalidationExecutionVerificationRecord,
)
from src.models.lineage_observation import (
    LineageObservationDependencyRecord,
    LineageObservationRecord,
)
from src.services.lineage_invalidation_projection_service import (
    PersistedLineageInvalidationDecisionView,
)
from src.models.lineage_invalidation_execution_result import (
    LineageInvalidationExecutionResultRecord,
)
from src.services.lineage_projection_service import (
    load_current_lineage_observation,
)
from src.services.lineage_verification_evidence_contract import (
    qualifies_as_new_materialization_evidence,
)


LineageInvalidationVerificationState = Literal[
    "awaiting_evidence",
    "stale_dependency_present",
    "verified",
]


@dataclass(frozen=True, slots=True)
class LineageInvalidationExecutionVerificationView:
    state: LineageInvalidationVerificationState
    execution_result_id: uuid.UUID
    verification_record_id: uuid.UUID | None
    observation_id: uuid.UUID | None
    stale_dependency: LineageRevisionIdentity


def load_lineage_invalidation_execution_verification_view(
    db: Session,
    *,
    organization_id: uuid.UUID,
    decision: PersistedLineageInvalidationDecisionView,
    execution_result_id: uuid.UUID,
) -> LineageInvalidationExecutionVerificationView:
    """
    Project post-repair verification evidence for one successful execution.

    The persisted invalidation decision is historical evidence. Its direct
    predecessor of the target is the exact stale dependency whose continued
    presence is evaluated in the explicitly bound materialization observation.

    The explicit binding records the producer's attestation. A necessary
    ingestion/revision fence and the current target are checked independently;
    timestamps and sequence alone are never proof of physical repair.
    """
    stale_dependency = _stale_direct_target_dependency(decision)

    verification = (
        db.query(LineageInvalidationExecutionVerificationRecord)
        .filter(
            LineageInvalidationExecutionVerificationRecord.organization_id
            == organization_id,
            LineageInvalidationExecutionVerificationRecord.execution_result_id
            == execution_result_id,
        )
        .one_or_none()
    )

    if verification is None:
        return LineageInvalidationExecutionVerificationView(
            state="awaiting_evidence",
            execution_result_id=execution_result_id,
            verification_record_id=None,
            observation_id=None,
            stale_dependency=stale_dependency,
        )

    observation = (
        db.query(LineageObservationRecord)
        .options(
            joinedload(
                LineageObservationRecord.dependencies
            )
        )
        .filter(
            LineageObservationRecord.organization_id
            == organization_id,
            LineageObservationRecord.id
            == verification.observation_id,
        )
        .one_or_none()
    )

    if observation is None:
        raise RuntimeError(
            "Lineage invalidation execution verification references "
            "a missing observation."
        )

    if observation.observation_kind != "materialization":
        raise RuntimeError(
            "Lineage invalidation execution verification references "
            "a non-materialization observation."
        )

    target = decision.decision.target

    if (
        observation.source_external_id != target.source_external_id
        or observation.asset_external_id != target.asset_external_id
    ):
        raise RuntimeError(
            "Lineage invalidation execution verification observation "
            "does not match the persisted decision target."
        )

    execution_result = (
        db.query(LineageInvalidationExecutionResultRecord)
        .filter(
            LineageInvalidationExecutionResultRecord.organization_id
            == organization_id,
            LineageInvalidationExecutionResultRecord.id == execution_result_id,
            LineageInvalidationExecutionResultRecord.outcome == "succeeded",
        )
        .one_or_none()
    )
    current = load_current_lineage_observation(
        db,
        organization_id=organization_id,
        source_external_id=target.source_external_id,
        asset_external_id=target.asset_external_id,
        observation_kind="materialization",
    )
    if (
        execution_result is None
        or not qualifies_as_new_materialization_evidence(
            result_recorded_at=execution_result.recorded_at,
            observation_created_at=observation.created_at,
            invalidated_revision_external_id=target.revision_external_id,
            observation_revision_external_id=observation.revision_external_id,
        )
        or current is None
        or current.sequence != observation.sequence
        or current.revision_external_id != observation.revision_external_id
    ):
        return LineageInvalidationExecutionVerificationView(
            state="awaiting_evidence",
            execution_result_id=execution_result_id,
            verification_record_id=verification.id,
            observation_id=observation.id,
            stale_dependency=stale_dependency,
        )

    state: LineageInvalidationVerificationState = (
        "stale_dependency_present"
        if _contains_exact_stale_dependency(
            observation.dependencies,
            stale_dependency=stale_dependency,
        )
        else "verified"
    )

    return LineageInvalidationExecutionVerificationView(
        state=state,
        execution_result_id=execution_result_id,
        verification_record_id=verification.id,
        observation_id=observation.id,
        stale_dependency=stale_dependency,
    )


def _stale_direct_target_dependency(
    decision: PersistedLineageInvalidationDecisionView,
) -> LineageRevisionIdentity:
    causal_path = decision.decision.causal_path

    if len(causal_path) < 2:
        raise RuntimeError(
            "Persisted lineage invalidation decision does not contain "
            "a direct predecessor for its target."
        )

    stale_dependency = causal_path[-2]

    if causal_path[-1] != decision.decision.target:
        raise RuntimeError(
            "Persisted lineage invalidation decision causal path does "
            "not terminate at its target."
        )

    return stale_dependency


def _contains_exact_stale_dependency(
    dependencies: list[LineageObservationDependencyRecord],
    *,
    stale_dependency: LineageRevisionIdentity,
) -> bool:
    return any(
        dependency.asset_external_id
        == stale_dependency.asset_external_id
        and dependency.revision_external_id
        == stale_dependency.revision_external_id
        for dependency in dependencies
    )
