from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.models.risk_assessment import RiskAssessment
from src.models.semantic_drift_event import SemanticDriftEvent
from src.services.event_evidence_lock import (
    acquire_event_evidence_lock,
)
from src.services.event_topology_risk_service import (
    load_event_topology_risk_signal,
)
from src.services.event_topology_risk_v3_service import (
    load_event_topology_risk_signal_v3,
)
from src.services.risk_scoring_v2_service import (
    RiskScoreV2Input,
    RiskScoreV2Result,
    calculate_risk_score_v2,
)
from src.services.risk_scoring_v3_service import (
    RiskScoreV3Input,
    RiskScoreV3Result,
    TopologyRiskV3Input,
    calculate_risk_score_v3,
)
from src.services.risk_version import (
    RISK_VERSION_V2,
    RISK_VERSION_V3,
)


class RiskAssessmentDependencyError(RuntimeError):
    pass


@dataclass(frozen=True)
class RiskAssessmentPersistenceResult:
    assessment_id: uuid.UUID
    organization_id: uuid.UUID
    event_id: uuid.UUID
    risk_version: str
    assessed_at: datetime

    created: bool

    score: float

    semantic_magnitude_contribution: float
    legacy_impact_contribution: float
    semantic_severity_contribution: float
    page_context_contribution: float
    confidence_contribution: float

    topology_score: float
    topology_contribution: float
    topology_shift_component: float
    topology_centrality_component: float
    topology_downstream_component: float

    has_topology_signal: bool
    has_previous_topology_state: bool


def _as_uuid(
    value: object,
    *,
    field_name: str,
) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value

    try:
        return uuid.UUID(str(value))
    except (
        TypeError,
        ValueError,
        AttributeError,
    ) as exc:
        raise ValueError(
            f"{field_name} must be a valid UUID"
        ) from exc


def _validate_event(
    event: SemanticDriftEvent,
) -> tuple[
    uuid.UUID,
    uuid.UUID,
    datetime,
]:
    event_id = getattr(
        event,
        "id",
        None,
    )

    if event_id is None:
        raise ValueError(
            "event.id is required"
        )

    organization_id = getattr(
        event,
        "organization_id",
        None,
    )

    if organization_id is None:
        raise ValueError(
            "event.organization_id is required"
        )

    detected_at = getattr(
        event,
        "detected_at",
        None,
    )

    if detected_at is None:
        raise ValueError(
            "event.detected_at is required"
        )

    return (
        _as_uuid(
            event_id,
            field_name="event.id",
        ),
        _as_uuid(
            organization_id,
            field_name="event.organization_id",
        ),
        detected_at,
    )


def _result_from_row(
    row: RiskAssessment,
    *,
    event_id: uuid.UUID,
    created: bool,
) -> RiskAssessmentPersistenceResult:
    return RiskAssessmentPersistenceResult(
        assessment_id=row.id,
        organization_id=row.organization_id,
        event_id=event_id,
        risk_version=row.risk_version,
        assessed_at=row.assessed_at,
        created=created,
        score=row.score,
        semantic_magnitude_contribution=(
            row.semantic_magnitude_contribution
        ),
        legacy_impact_contribution=(
            row.legacy_impact_contribution
        ),
        semantic_severity_contribution=(
            row.semantic_severity_contribution
        ),
        page_context_contribution=(
            row.page_context_contribution
        ),
        confidence_contribution=(
            row.confidence_contribution
        ),
        topology_score=row.topology_score,
        topology_contribution=(
            row.topology_contribution
        ),
        topology_shift_component=(
            row.topology_shift_component
        ),
        topology_centrality_component=(
            row.topology_centrality_component
        ),
        topology_downstream_component=(
            row.topology_downstream_component
        ),
        has_topology_signal=(
            row.has_topology_signal
        ),
        has_previous_topology_state=(
            row.has_previous_topology_state
        ),
    )


def _result_from_calculation(
    *,
    assessment_id: uuid.UUID,
    organization_id: uuid.UUID,
    event_id: uuid.UUID,
    assessed_at: datetime,
    risk_version: str,
    result: RiskScoreV2Result | RiskScoreV3Result,
) -> RiskAssessmentPersistenceResult:
    return RiskAssessmentPersistenceResult(
        assessment_id=assessment_id,
        organization_id=organization_id,
        event_id=event_id,
        risk_version=risk_version,
        assessed_at=assessed_at,
        created=True,
        score=result.score,
        semantic_magnitude_contribution=(
            result.semantic_magnitude_contribution
        ),
        legacy_impact_contribution=(
            result.legacy_impact_contribution
        ),
        semantic_severity_contribution=(
            result.semantic_severity_contribution
        ),
        page_context_contribution=(
            result.page_context_contribution
        ),
        confidence_contribution=(
            result.confidence_contribution
        ),
        topology_score=(
            result.topology_score
        ),
        topology_contribution=(
            result.topology_contribution
        ),
        topology_shift_component=(
            result.topology_shift_component
        ),
        topology_centrality_component=(
            result.topology_centrality_component
        ),
        topology_downstream_component=(
            result.topology_downstream_component
        ),
        has_topology_signal=(
            result.has_topology_signal
        ),
        has_previous_topology_state=(
            result.has_previous_topology_state
        ),
    )


def _load_existing_assessment(
    db: Session,
    *,
    organization_id: uuid.UUID,
    event_id: uuid.UUID,
    risk_version: str,
) -> RiskAssessment | None:
    return (
        db.query(RiskAssessment)
        .filter(
            RiskAssessment.organization_id
            == organization_id,
            RiskAssessment.semantic_drift_event_id
            == event_id,
            RiskAssessment.risk_version
            == risk_version,
        )
        .one_or_none()
    )


def _insert_assessment(
    db: Session,
    *,
    organization_id: uuid.UUID,
    event_id: uuid.UUID,
    assessed_at: datetime,
    risk_version: str,
    result: RiskScoreV2Result | RiskScoreV3Result,
) -> uuid.UUID | None:
    assessment_id = uuid.uuid4()

    statement = (
        insert(RiskAssessment)
        .values(
            id=assessment_id,
            organization_id=organization_id,
            semantic_drift_event_id=event_id,
            risk_version=risk_version,
            assessed_at=assessed_at,
            score=result.score,
            semantic_magnitude_contribution=(
                result
                .semantic_magnitude_contribution
            ),
            legacy_impact_contribution=(
                result
                .legacy_impact_contribution
            ),
            semantic_severity_contribution=(
                result
                .semantic_severity_contribution
            ),
            page_context_contribution=(
                result.page_context_contribution
            ),
            confidence_contribution=(
                result.confidence_contribution
            ),
            topology_score=(
                result.topology_score
            ),
            topology_contribution=(
                result.topology_contribution
            ),
            topology_shift_component=(
                result.topology_shift_component
            ),
            topology_centrality_component=(
                result
                .topology_centrality_component
            ),
            topology_downstream_component=(
                result
                .topology_downstream_component
            ),
            has_topology_signal=(
                result.has_topology_signal
            ),
            has_previous_topology_state=(
                result
                .has_previous_topology_state
            ),
        )
        .on_conflict_do_nothing(
            index_elements=[
                RiskAssessment.organization_id,
                RiskAssessment
                .semantic_drift_event_id,
                RiskAssessment.risk_version,
            ],
            index_where=(
                RiskAssessment
                .semantic_drift_event_id
                .is_not(None)
            ),
        )
        .returning(
            RiskAssessment.id
        )
    )

    return db.execute(
        statement
    ).scalar_one_or_none()


def persist_risk_assessment_v2(
    db: Session,
    *,
    event: SemanticDriftEvent,
    semantic_magnitude: float,
    legacy_impact: float,
    semantic_severity: float,
    page_context: float,
    confidence: float,
) -> RiskAssessmentPersistenceResult:
    """
    Calculate and persist immutable deterministic Risk v2.

    Contract:
      - persisted topology shift evidence must already exist;
      - first topology state is a valid baseline and contributes zero;
      - retry of the same event/version returns the already persisted
        assessment, even if the caller supplies different inputs;
      - caller owns commit/rollback.

    This prevents an incomplete pre-topology assessment from becoming
    permanently locked as deterministic_v2.
    """
    (
        event_id,
        organization_id,
        detected_at,
    ) = _validate_event(event)

    acquire_event_evidence_lock(
        db,
        organization_id=organization_id,
        event_id=event_id,
    )

    existing = _load_existing_assessment(
        db,
        organization_id=organization_id,
        event_id=event_id,
        risk_version=RISK_VERSION_V2,
    )

    if existing is not None:
        return _result_from_row(
            existing,
            event_id=event_id,
            created=False,
        )

    topology_signal = (
        load_event_topology_risk_signal(
            db,
            event,
        )
    )

    if topology_signal is None:
        raise RiskAssessmentDependencyError(
            "Risk v2 requires persisted topology shift "
            "snapshots for the event"
        )

    risk_result = calculate_risk_score_v2(
        RiskScoreV2Input(
            semantic_magnitude=semantic_magnitude,
            legacy_impact=legacy_impact,
            semantic_severity=semantic_severity,
            page_context=page_context,
            confidence=confidence,
            topology=topology_signal.risk_input,
        )
    )

    inserted_id = _insert_assessment(
        db,
        organization_id=organization_id,
        event_id=event_id,
        assessed_at=detected_at,
        risk_version=RISK_VERSION_V2,
        result=risk_result,
    )

    if inserted_id is not None:
        return _result_from_calculation(
            assessment_id=inserted_id,
            organization_id=organization_id,
            event_id=event_id,
            assessed_at=detected_at,
            risk_version=RISK_VERSION_V2,
            result=risk_result,
        )

    # PostgreSQL ON CONFLICT may reach here when another transaction
    # won the event/version race. A new SELECT under READ COMMITTED
    # sees the committed winner.
    existing = _load_existing_assessment(
        db,
        organization_id=organization_id,
        event_id=event_id,
        risk_version=RISK_VERSION_V2,
    )

    if existing is None:
        raise RuntimeError(
            "risk assessment conflict occurred but "
            "the persisted assessment could not be loaded"
        )

    return _result_from_row(
        existing,
        event_id=event_id,
        created=False,
    )


def persist_risk_assessment_v3(
    db: Session,
    *,
    event: SemanticDriftEvent,
    semantic_magnitude: float,
    legacy_impact: float,
    semantic_severity: float,
    page_context: float,
    confidence: float,
) -> RiskAssessmentPersistenceResult:
    """
    Calculate and persist immutable deterministic Risk v3.

    Contract:
      - persisted topology shift evidence must already exist;
      - topology evidence is reduced to one coherent representative entity;
      - first topology state is a valid baseline and contributes zero;
      - retry of the same event/version returns the already persisted
        assessment, even if the caller supplies different inputs;
      - Risk v3 persists independently from Risk v2;
      - caller owns commit/rollback.

    This function does not project Risk v3 onto SemanticDriftEvent. Version
    promotion and event-facing projection are separate concerns.
    """
    (
        event_id,
        organization_id,
        detected_at,
    ) = _validate_event(event)

    acquire_event_evidence_lock(
        db,
        organization_id=organization_id,
        event_id=event_id,
    )

    existing = _load_existing_assessment(
        db,
        organization_id=organization_id,
        event_id=event_id,
        risk_version=RISK_VERSION_V3,
    )

    if existing is not None:
        return _result_from_row(
            existing,
            event_id=event_id,
            created=False,
        )

    topology_signal = (
        load_event_topology_risk_signal_v3(
            db,
            event,
        )
    )

    if topology_signal is None:
        raise RiskAssessmentDependencyError(
            "Risk v3 requires persisted topology shift "
            "snapshots for the event"
        )

    topology_input = TopologyRiskV3Input(
        shift_score=topology_signal.shift_score,
        degree_centrality_delta=(
            topology_signal.degree_centrality_delta
        ),
        downstream_reach_ratio_delta=(
            topology_signal.downstream_reach_ratio_delta
        ),
        has_previous_state=(
            topology_signal.has_previous_state
        ),
    )

    risk_result = calculate_risk_score_v3(
        RiskScoreV3Input(
            semantic_magnitude=semantic_magnitude,
            legacy_impact=legacy_impact,
            semantic_severity=semantic_severity,
            page_context=page_context,
            confidence=confidence,
            topology=topology_input,
        )
    )

    inserted_id = _insert_assessment(
        db,
        organization_id=organization_id,
        event_id=event_id,
        assessed_at=detected_at,
        risk_version=RISK_VERSION_V3,
        result=risk_result,
    )

    if inserted_id is not None:
        return _result_from_calculation(
            assessment_id=inserted_id,
            organization_id=organization_id,
            event_id=event_id,
            assessed_at=detected_at,
            risk_version=RISK_VERSION_V3,
            result=risk_result,
        )

    existing = _load_existing_assessment(
        db,
        organization_id=organization_id,
        event_id=event_id,
        risk_version=RISK_VERSION_V3,
    )

    if existing is None:
        raise RuntimeError(
            "risk assessment conflict occurred but "
            "the persisted assessment could not be loaded"
        )

    return _result_from_row(
        existing,
        event_id=event_id,
        created=False,
    )
