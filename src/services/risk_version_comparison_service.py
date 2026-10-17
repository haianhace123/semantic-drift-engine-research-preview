from __future__ import annotations

import math
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from src.models.risk_assessment import RiskAssessment
from src.services.risk_version import (
    RISK_VERSION_V2,
    RISK_VERSION_V3,
)


@dataclass(frozen=True)
class RiskContributionDelta:
    semantic_magnitude: float
    legacy_impact: float
    semantic_severity: float
    page_context: float
    confidence: float
    topology: float


@dataclass(frozen=True)
class RiskTopologyComparison:
    score_delta: float
    shift_component_delta: float
    centrality_component_delta: float
    downstream_component_delta: float
    baseline_shift_component: float
    candidate_shift_component: float
    baseline_centrality_component: float
    candidate_centrality_component: float
    baseline_downstream_component: float
    candidate_downstream_component: float
    signal_state_changed: bool
    previous_state_changed: bool
    baseline_has_signal: bool
    candidate_has_signal: bool
    baseline_has_previous_state: bool
    candidate_has_previous_state: bool


@dataclass(frozen=True)
class RiskVersionComparison:
    organization_id: uuid.UUID
    event_id: uuid.UUID

    baseline_version: str
    candidate_version: str

    baseline_assessment_id: uuid.UUID | None
    candidate_assessment_id: uuid.UUID | None

    baseline_score: float
    candidate_score: float
    score_delta: float
    direction: str

    contributions: RiskContributionDelta
    topology: RiskTopologyComparison


@dataclass(frozen=True)
class RiskComparisonValue:
    score: float

    semantic_magnitude_contribution: float
    legacy_impact_contribution: float
    semantic_severity_contribution: float
    page_context_contribution: float
    confidence_contribution: float
    topology_contribution: float

    topology_score: float
    topology_shift_component: float
    topology_centrality_component: float
    topology_downstream_component: float

    has_topology_signal: bool
    has_previous_topology_state: bool


class RiskVersionComparisonNotReadyError(RuntimeError):
    pass


def _delta(candidate: float, baseline: float) -> float:
    result = float(candidate) - float(baseline)

    if not math.isfinite(result):
        raise ValueError(
            "risk assessment comparison produced a non-finite delta"
        )

    return round(result, 6)


def compare_risk_values(
    *,
    organization_id: uuid.UUID,
    event_id: uuid.UUID,
    baseline: RiskComparisonValue,
    candidate: RiskComparisonValue,
    baseline_assessment_id: uuid.UUID | None = None,
    candidate_assessment_id: uuid.UUID | None = None,
) -> RiskVersionComparison:
    score_delta = _delta(
        candidate.score,
        baseline.score,
    )

    if score_delta > 0.0:
        direction = "increased"
    elif score_delta < 0.0:
        direction = "decreased"
    else:
        direction = "unchanged"

    return RiskVersionComparison(
        organization_id=organization_id,
        event_id=event_id,
        baseline_version=RISK_VERSION_V2,
        candidate_version=RISK_VERSION_V3,
        baseline_assessment_id=baseline_assessment_id,
        candidate_assessment_id=candidate_assessment_id,
        baseline_score=float(baseline.score),
        candidate_score=float(candidate.score),
        score_delta=score_delta,
        direction=direction,
        contributions=RiskContributionDelta(
            semantic_magnitude=_delta(
                candidate.semantic_magnitude_contribution,
                baseline.semantic_magnitude_contribution,
            ),
            legacy_impact=_delta(
                candidate.legacy_impact_contribution,
                baseline.legacy_impact_contribution,
            ),
            semantic_severity=_delta(
                candidate.semantic_severity_contribution,
                baseline.semantic_severity_contribution,
            ),
            page_context=_delta(
                candidate.page_context_contribution,
                baseline.page_context_contribution,
            ),
            confidence=_delta(
                candidate.confidence_contribution,
                baseline.confidence_contribution,
            ),
            topology=_delta(
                candidate.topology_contribution,
                baseline.topology_contribution,
            ),
        ),
        topology=RiskTopologyComparison(
            score_delta=_delta(
                candidate.topology_score,
                baseline.topology_score,
            ),
            shift_component_delta=_delta(
                candidate.topology_shift_component,
                baseline.topology_shift_component,
            ),
            centrality_component_delta=_delta(
                candidate.topology_centrality_component,
                baseline.topology_centrality_component,
            ),
            downstream_component_delta=_delta(
                candidate.topology_downstream_component,
                baseline.topology_downstream_component,
            ),
            baseline_shift_component=float(
                baseline.topology_shift_component
            ),
            candidate_shift_component=float(
                candidate.topology_shift_component
            ),
            baseline_centrality_component=float(
                baseline.topology_centrality_component
            ),
            candidate_centrality_component=float(
                candidate.topology_centrality_component
            ),
            baseline_downstream_component=float(
                baseline.topology_downstream_component
            ),
            candidate_downstream_component=float(
                candidate.topology_downstream_component
            ),
            signal_state_changed=(
                candidate.has_topology_signal
                != baseline.has_topology_signal
            ),
            previous_state_changed=(
                candidate.has_previous_topology_state
                != baseline.has_previous_topology_state
            ),
            baseline_has_signal=bool(
                baseline.has_topology_signal
            ),
            candidate_has_signal=bool(
                candidate.has_topology_signal
            ),
            baseline_has_previous_state=bool(
                baseline.has_previous_topology_state
            ),
            candidate_has_previous_state=bool(
                candidate.has_previous_topology_state
            ),
        ),
    )


def _comparison_value(
    assessment: RiskAssessment,
) -> RiskComparisonValue:
    return RiskComparisonValue(
        score=float(assessment.score),
        semantic_magnitude_contribution=float(
            assessment.semantic_magnitude_contribution
        ),
        legacy_impact_contribution=float(
            assessment.legacy_impact_contribution
        ),
        semantic_severity_contribution=float(
            assessment.semantic_severity_contribution
        ),
        page_context_contribution=float(
            assessment.page_context_contribution
        ),
        confidence_contribution=float(
            assessment.confidence_contribution
        ),
        topology_contribution=float(
            assessment.topology_contribution
        ),
        topology_score=float(
            assessment.topology_score
        ),
        topology_shift_component=float(
            assessment.topology_shift_component
        ),
        topology_centrality_component=float(
            assessment.topology_centrality_component
        ),
        topology_downstream_component=float(
            assessment.topology_downstream_component
        ),
        has_topology_signal=bool(
            assessment.has_topology_signal
        ),
        has_previous_topology_state=bool(
            assessment.has_previous_topology_state
        ),
    )


def compare_risk_assessments(
    baseline: RiskAssessment,
    candidate: RiskAssessment,
) -> RiskVersionComparison:
    if baseline.risk_version != RISK_VERSION_V2:
        raise ValueError(
            "baseline assessment must use "
            f"{RISK_VERSION_V2}"
        )

    if candidate.risk_version != RISK_VERSION_V3:
        raise ValueError(
            "candidate assessment must use "
            f"{RISK_VERSION_V3}"
        )

    if baseline.organization_id != candidate.organization_id:
        raise ValueError(
            "risk assessment comparison requires matching organizations"
        )

    if (
        baseline.semantic_drift_event_id is None
        or candidate.semantic_drift_event_id is None
        or baseline.semantic_drift_event_id
        != candidate.semantic_drift_event_id
    ):
        raise ValueError(
            "risk assessment comparison requires matching events"
        )

    return compare_risk_values(
        organization_id=baseline.organization_id,
        event_id=baseline.semantic_drift_event_id,
        baseline=_comparison_value(baseline),
        candidate=_comparison_value(candidate),
        baseline_assessment_id=baseline.id,
        candidate_assessment_id=candidate.id,
    )


def _load_assessment(
    db: Session,
    *,
    organization_id: uuid.UUID,
    event_id: uuid.UUID,
    risk_version: str,
) -> RiskAssessment | None:
    return (
        db.query(RiskAssessment)
        .filter(
            RiskAssessment.organization_id == organization_id,
            RiskAssessment.semantic_drift_event_id == event_id,
            RiskAssessment.risk_version == risk_version,
        )
        .one_or_none()
    )


def compare_persisted_risk_versions(
    db: Session,
    *,
    organization_id: uuid.UUID,
    event_id: uuid.UUID,
) -> RiskVersionComparison:
    baseline = _load_assessment(
        db,
        organization_id=organization_id,
        event_id=event_id,
        risk_version=RISK_VERSION_V2,
    )
    candidate = _load_assessment(
        db,
        organization_id=organization_id,
        event_id=event_id,
        risk_version=RISK_VERSION_V3,
    )

    missing_versions: list[str] = []

    if baseline is None:
        missing_versions.append(RISK_VERSION_V2)

    if candidate is None:
        missing_versions.append(RISK_VERSION_V3)

    if missing_versions:
        raise RiskVersionComparisonNotReadyError(
            "risk version comparison requires persisted assessments for: "
            + ", ".join(missing_versions)
        )

    return compare_risk_assessments(
        baseline,
        candidate,
    )
