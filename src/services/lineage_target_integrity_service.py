from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import and_
from sqlalchemy.orm import Session, aliased

from src.domain.lineage_revision import LineageRevisionIdentity
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
from src.models.lineage_observation import (
    LineageObservationDependencyRecord,
    LineageObservationRecord,
)
from src.services.lineage_invalidation_projection_service import (
    PersistedLineageInvalidationDecisionView,
    load_lineage_invalidation_decisions_for_target,
    load_lineage_invalidation_decisions_for_targets,
)
from src.services.lineage_verification_evidence_contract import (
    qualifies_as_new_materialization_evidence,
)


LineageTargetIntegrityStatus = Literal[
    "unknown",
    "not_affected",
    "affected",
    "verified",
]

LineageTargetIntegrityEvidenceState = Literal[
    "no_action",
    "unresolved",
    "verified",
]


@dataclass(frozen=True, slots=True)
class LineageTargetIntegrityEvidence:
    decision_id: uuid.UUID
    root: LineageRevisionIdentity
    disposition: Literal[
        "no_action",
        "invalidate",
        "rematerialize",
    ]
    reason_code: str
    state: LineageTargetIntegrityEvidenceState


@dataclass(frozen=True, slots=True)
class LineageTargetIntegrityAssessment:
    target: LineageRevisionIdentity
    status: LineageTargetIntegrityStatus
    evidence: tuple[LineageTargetIntegrityEvidence, ...]


@dataclass(frozen=True, slots=True)
class LineageContextIntegritySummary:
    target_count: int
    unknown_count: int
    not_affected_count: int
    affected_count: int
    verified_count: int
    has_unknown: bool
    has_unresolved_impact: bool


@dataclass(frozen=True, slots=True)
class LineageContextIntegrityAssessment:
    assessments: tuple[LineageTargetIntegrityAssessment, ...]
    summary: LineageContextIntegritySummary


@dataclass(frozen=True, slots=True)
class _VerificationObservation:
    outcome: str
    observation_kind: str
    source_external_id: str
    asset_external_id: str
    revision_external_id: str
    observation_created_at: datetime | None
    result_recorded_at: datetime | None


def assess_lineage_target_integrity(
    db: Session,
    *,
    organization_id: uuid.UUID,
    target: LineageRevisionIdentity,
) -> LineageTargetIntegrityAssessment:
    decisions = load_lineage_invalidation_decisions_for_target(
        db,
        organization_id=organization_id,
        target=target,
    )
    verified_decision_ids = _load_verified_decision_ids(
        db,
        organization_id=organization_id,
        decisions=tuple(
            decision
            for decision in decisions
            if decision.decision.disposition != "no_action"
        ),
    )
    return _assess_target_from_decisions(
        target=target,
        decisions=decisions,
        verified_decision_ids=verified_decision_ids,
    )


def assess_lineage_context_integrity(
    db: Session,
    *,
    organization_id: uuid.UUID,
    targets: tuple[LineageRevisionIdentity, ...],
) -> LineageContextIntegrityAssessment:
    decisions = load_lineage_invalidation_decisions_for_targets(
        db,
        organization_id=organization_id,
        targets=targets,
    )
    decisions_by_target: dict[
        LineageRevisionIdentity,
        list[PersistedLineageInvalidationDecisionView],
    ] = {target: [] for target in targets}
    for decision in decisions:
        decisions_by_target[decision.decision.target].append(decision)

    verified_decision_ids = _load_verified_decision_ids(
        db,
        organization_id=organization_id,
        decisions=tuple(
            decision
            for decision in decisions
            if decision.decision.disposition != "no_action"
        ),
    )
    assessments = tuple(
        _assess_target_from_decisions(
            target=target,
            decisions=tuple(decisions_by_target[target]),
            verified_decision_ids=verified_decision_ids,
        )
        for target in targets
    )
    statuses = tuple(assessment.status for assessment in assessments)
    return LineageContextIntegrityAssessment(
        assessments=assessments,
        summary=LineageContextIntegritySummary(
            target_count=len(assessments),
            unknown_count=statuses.count("unknown"),
            not_affected_count=statuses.count("not_affected"),
            affected_count=statuses.count("affected"),
            verified_count=statuses.count("verified"),
            has_unknown="unknown" in statuses,
            has_unresolved_impact="affected" in statuses,
        ),
    )


def _assess_target_from_decisions(
    *,
    target: LineageRevisionIdentity,
    decisions: tuple[PersistedLineageInvalidationDecisionView, ...],
    verified_decision_ids: frozenset[uuid.UUID],
) -> LineageTargetIntegrityAssessment:
    if not decisions:
        return LineageTargetIntegrityAssessment(
            target=target,
            status="unknown",
            evidence=(),
        )

    evidence = tuple(
        LineageTargetIntegrityEvidence(
            decision_id=decision.record_id,
            root=decision.decision.root,
            disposition=decision.decision.disposition,
            reason_code=decision.decision.reason_code,
            state=(
                "no_action"
                if decision.decision.disposition == "no_action"
                else (
                    "verified"
                    if decision.record_id in verified_decision_ids
                    else "unresolved"
                )
            ),
        )
        for decision in decisions
    )

    if any(item.state == "unresolved" for item in evidence):
        status: LineageTargetIntegrityStatus = "affected"
    elif any(item.state == "verified" for item in evidence):
        status = "verified"
    else:
        status = "not_affected"

    return LineageTargetIntegrityAssessment(
        target=target,
        status=status,
        evidence=evidence,
    )


def _load_verified_decision_ids(
    db: Session,
    *,
    organization_id: uuid.UUID,
    decisions: tuple[PersistedLineageInvalidationDecisionView, ...],
) -> frozenset[uuid.UUID]:
    if not decisions:
        return frozenset()

    decision_by_id = {
        decision.record_id: decision
        for decision in decisions
    }

    newer = aliased(LineageObservationRecord)

    rows = (
        db.query(
            LineageInvalidationExecutionIntentRecord.decision_id,
            LineageInvalidationExecutionResultRecord.outcome,
            LineageObservationRecord.observation_kind,
            LineageObservationRecord.source_external_id,
            LineageObservationRecord.asset_external_id,
            LineageObservationRecord.revision_external_id,
            LineageObservationRecord.created_at.label("observation_created_at"),
            LineageInvalidationExecutionResultRecord.recorded_at.label(
                "result_recorded_at"
            ),
            LineageObservationDependencyRecord.asset_external_id.label(
                "dependency_asset_external_id"
            ),
            LineageObservationDependencyRecord.revision_external_id.label(
                "dependency_revision_external_id"
            ),
        )
        .join(
            LineageInvalidationExecutionAttemptRecord,
            LineageInvalidationExecutionAttemptRecord.intent_id
            == LineageInvalidationExecutionIntentRecord.id,
        )
        .join(
            LineageInvalidationExecutionResultRecord,
            LineageInvalidationExecutionResultRecord.attempt_id
            == LineageInvalidationExecutionAttemptRecord.id,
        )
        .join(
            LineageInvalidationExecutionVerificationRecord,
            LineageInvalidationExecutionVerificationRecord.execution_result_id
            == LineageInvalidationExecutionResultRecord.id,
        )
        .join(
            LineageObservationRecord,
            LineageObservationRecord.id
            == LineageInvalidationExecutionVerificationRecord.observation_id,
        )
        .outerjoin(
            newer,
            and_(
                newer.organization_id == LineageObservationRecord.organization_id,
                newer.source_external_id == LineageObservationRecord.source_external_id,
                newer.asset_external_id == LineageObservationRecord.asset_external_id,
                newer.observation_kind == LineageObservationRecord.observation_kind,
                newer.sequence > LineageObservationRecord.sequence,
            ),
        )
        .outerjoin(
            LineageObservationDependencyRecord,
            LineageObservationDependencyRecord.observation_id
            == LineageObservationRecord.id,
        )
        .filter(
            LineageInvalidationExecutionIntentRecord.organization_id
            == organization_id,
            LineageInvalidationExecutionAttemptRecord.organization_id
            == organization_id,
            LineageInvalidationExecutionResultRecord.organization_id
            == organization_id,
            LineageInvalidationExecutionVerificationRecord.organization_id
            == organization_id,
            LineageObservationRecord.organization_id == organization_id,
            newer.id.is_(None),
            LineageInvalidationExecutionIntentRecord.decision_id.in_(
                tuple(decision_by_id)
            ),
        )
        .all()
    )

    observations: dict[uuid.UUID, _VerificationObservation] = {}
    dependencies_by_decision: dict[
        uuid.UUID,
        set[tuple[str, str]],
    ] = {}

    for row in rows:
        decision_id = row.decision_id
        observation = _VerificationObservation(
            outcome=row.outcome,
            observation_kind=row.observation_kind,
            source_external_id=row.source_external_id,
            asset_external_id=row.asset_external_id,
            revision_external_id=row.revision_external_id,
            observation_created_at=row.observation_created_at,
            result_recorded_at=row.result_recorded_at,
        )
        existing = observations.get(decision_id)
        if existing is not None and existing != observation:
            raise RuntimeError(
                "Lineage invalidation decision has conflicting "
                "verification observations."
            )
        observations[decision_id] = observation

        if row.dependency_asset_external_id is not None:
            dependencies_by_decision.setdefault(
                decision_id,
                set(),
            ).add(
                (
                    row.dependency_asset_external_id,
                    row.dependency_revision_external_id,
                )
            )

    verified: set[uuid.UUID] = set()

    for decision_id, observation in observations.items():
        decision = decision_by_id[decision_id]
        target = decision.decision.target

        if observation.outcome != "succeeded":
            continue
        if not qualifies_as_new_materialization_evidence(
            result_recorded_at=observation.result_recorded_at,
            observation_created_at=observation.observation_created_at,
            invalidated_revision_external_id=target.revision_external_id,
            observation_revision_external_id=observation.revision_external_id,
        ):
            continue
        if observation.observation_kind != "materialization":
            raise RuntimeError(
                "Lineage invalidation execution verification references "
                "a non-materialization observation."
            )
        if (
            observation.source_external_id != target.source_external_id
            or observation.asset_external_id != target.asset_external_id
        ):
            raise RuntimeError(
                "Lineage invalidation execution verification observation "
                "does not match the persisted decision target."
            )

        causal_path = decision.decision.causal_path
        if len(causal_path) < 2 or causal_path[-1] != target:
            raise RuntimeError(
                "Persisted lineage invalidation decision does not contain "
                "a valid direct predecessor for its target."
            )

        stale_dependency = causal_path[-2]
        if (
            stale_dependency.asset_external_id,
            stale_dependency.revision_external_id,
        ) not in dependencies_by_decision.get(decision_id, set()):
            verified.add(decision_id)

    return frozenset(verified)
