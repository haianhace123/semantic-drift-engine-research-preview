from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import mean, median
from typing import Sequence

from src.services.risk_classification_service import (
    classify_risk_score,
)
from src.services.risk_version_comparison_service import (
    RiskVersionComparison,
)


@dataclass(frozen=True)
class RiskShadowDeltaDistribution:
    minimum: float
    maximum: float
    mean: float
    mean_absolute: float
    median_absolute: float
    p95_absolute: float


@dataclass(frozen=True)
class RiskShadowMovement:
    increased: int
    decreased: int
    unchanged: int
    classification_changed: int


@dataclass(frozen=True)
class RiskShadowTopologyEvaluation:
    mean_score_delta: float
    mean_absolute_score_delta: float
    mean_contribution_delta: float
    mean_absolute_contribution_delta: float
    signal_state_mismatches: int
    previous_state_mismatches: int
    previous_state_pairs: int
    exercised_topology_pairs: int
    divergent_pairs: int


@dataclass(frozen=True)
class RiskShadowEvaluation:
    paired_count: int
    score_delta: RiskShadowDeltaDistribution
    movement: RiskShadowMovement
    topology: RiskShadowTopologyEvaluation
    non_topology_contribution_mismatches: int


def _round(value: float) -> float:
    return round(float(value), 6)


def _percentile_nearest_rank(
    values: Sequence[float],
    percentile: float,
) -> float:
    if not values:
        raise ValueError(
            "percentile requires at least one value"
        )

    if percentile <= 0.0 or percentile > 1.0:
        raise ValueError(
            "percentile must be greater than 0 and at most 1"
        )

    ordered = sorted(
        float(value)
        for value in values
    )

    rank = math.ceil(
        percentile * len(ordered)
    )

    return ordered[
        max(0, rank - 1)
    ]


def _has_non_topology_mismatch(
    comparison: RiskVersionComparison,
    *,
    tolerance: float,
) -> bool:
    deltas = (
        comparison.contributions.semantic_magnitude,
        comparison.contributions.legacy_impact,
        comparison.contributions.semantic_severity,
        comparison.contributions.page_context,
        comparison.contributions.confidence,
    )

    return any(
        abs(delta) > tolerance
        for delta in deltas
    )


def evaluate_risk_shadow_comparisons(
    comparisons: Sequence[RiskVersionComparison],
    *,
    mismatch_tolerance: float = 1e-6,
) -> RiskShadowEvaluation:
    if not comparisons:
        raise ValueError(
            "risk shadow evaluation requires at least one paired comparison"
        )

    if (
        not math.isfinite(mismatch_tolerance)
        or mismatch_tolerance < 0.0
    ):
        raise ValueError(
            "mismatch_tolerance must be finite and non-negative"
        )

    score_deltas = [
        comparison.score_delta
        for comparison in comparisons
    ]
    absolute_score_deltas = [
        abs(delta)
        for delta in score_deltas
    ]

    topology_score_deltas = [
        comparison.topology.score_delta
        for comparison in comparisons
    ]
    topology_contribution_deltas = [
        comparison.contributions.topology
        for comparison in comparisons
    ]

    increased = sum(
        comparison.direction == "increased"
        for comparison in comparisons
    )
    decreased = sum(
        comparison.direction == "decreased"
        for comparison in comparisons
    )
    unchanged = sum(
        comparison.direction == "unchanged"
        for comparison in comparisons
    )

    classification_changed = sum(
        classify_risk_score(
            comparison.baseline_score
        )
        != classify_risk_score(
            comparison.candidate_score
        )
        for comparison in comparisons
    )

    non_topology_mismatches = sum(
        _has_non_topology_mismatch(
            comparison,
            tolerance=mismatch_tolerance,
        )
        for comparison in comparisons
    )

    return RiskShadowEvaluation(
        paired_count=len(comparisons),
        score_delta=RiskShadowDeltaDistribution(
            minimum=_round(
                min(score_deltas)
            ),
            maximum=_round(
                max(score_deltas)
            ),
            mean=_round(
                mean(score_deltas)
            ),
            mean_absolute=_round(
                mean(absolute_score_deltas)
            ),
            median_absolute=_round(
                median(absolute_score_deltas)
            ),
            p95_absolute=_round(
                _percentile_nearest_rank(
                    absolute_score_deltas,
                    0.95,
                )
            ),
        ),
        movement=RiskShadowMovement(
            increased=increased,
            decreased=decreased,
            unchanged=unchanged,
            classification_changed=classification_changed,
        ),
        topology=RiskShadowTopologyEvaluation(
            mean_score_delta=_round(
                mean(topology_score_deltas)
            ),
            mean_absolute_score_delta=_round(
                mean(
                    abs(delta)
                    for delta in topology_score_deltas
                )
            ),
            mean_contribution_delta=_round(
                mean(topology_contribution_deltas)
            ),
            mean_absolute_contribution_delta=_round(
                mean(
                    abs(delta)
                    for delta in topology_contribution_deltas
                )
            ),
            signal_state_mismatches=sum(
                comparison.topology.signal_state_changed
                for comparison in comparisons
            ),
            previous_state_mismatches=sum(
                comparison.topology.previous_state_changed
                for comparison in comparisons
            ),
            previous_state_pairs=sum(
                comparison.topology.baseline_has_previous_state
                and comparison.topology.candidate_has_previous_state
                for comparison in comparisons
            ),
            exercised_topology_pairs=sum(
                comparison.topology.baseline_has_previous_state
                and comparison.topology.candidate_has_previous_state
                and (
                    abs(comparison.topology.baseline_shift_component)
                    > mismatch_tolerance
                    or abs(comparison.topology.candidate_shift_component)
                    > mismatch_tolerance
                    or abs(
                        comparison.topology.baseline_centrality_component
                    )
                    > mismatch_tolerance
                    or abs(
                        comparison.topology.candidate_centrality_component
                    )
                    > mismatch_tolerance
                    or abs(
                        comparison.topology.baseline_downstream_component
                    )
                    > mismatch_tolerance
                    or abs(
                        comparison.topology.candidate_downstream_component
                    )
                    > mismatch_tolerance
                )
                for comparison in comparisons
            ),
            divergent_pairs=sum(
                abs(comparison.topology.score_delta)
                > mismatch_tolerance
                for comparison in comparisons
            ),
        ),
        non_topology_contribution_mismatches=(
            non_topology_mismatches
        ),
    )


@dataclass(frozen=True)
class RiskShadowCoverage:
    total_events: int
    paired: int
    v2_only: int
    v3_only: int

    @property
    def pair_coverage(self) -> float:
        if self.total_events == 0:
            return 0.0

        return round(
            self.paired / self.total_events,
            6,
        )


@dataclass(frozen=True)
class PersistedRiskShadowEvaluation:
    coverage: RiskShadowCoverage
    evaluation: RiskShadowEvaluation | None


def evaluate_persisted_risk_shadow(
    db,
    *,
    organization_id=None,
) -> PersistedRiskShadowEvaluation:
    from src.models.risk_assessment import RiskAssessment
    from src.services.risk_version import (
        RISK_VERSION_V2,
        RISK_VERSION_V3,
    )
    from src.services.risk_version_comparison_service import (
        compare_risk_assessments,
    )

    query = (
        db.query(RiskAssessment)
        .filter(
            RiskAssessment.semantic_drift_event_id.is_not(None),
            RiskAssessment.risk_version.in_(
                [
                    RISK_VERSION_V2,
                    RISK_VERSION_V3,
                ]
            ),
        )
    )

    if organization_id is not None:
        query = query.filter(
            RiskAssessment.organization_id
            == organization_id
        )

    assessments = query.all()

    assessments_by_event = {}

    for assessment in assessments:
        key = (
            assessment.organization_id,
            assessment.semantic_drift_event_id,
        )

        assessments_by_event.setdefault(
            key,
            {},
        )[assessment.risk_version] = assessment

    comparisons = []
    v2_only = 0
    v3_only = 0

    for versions in assessments_by_event.values():
        baseline = versions.get(
            RISK_VERSION_V2
        )
        candidate = versions.get(
            RISK_VERSION_V3
        )

        if baseline is not None and candidate is not None:
            comparisons.append(
                compare_risk_assessments(
                    baseline,
                    candidate,
                )
            )
        elif baseline is not None:
            v2_only += 1
        elif candidate is not None:
            v3_only += 1

    evaluation = (
        evaluate_risk_shadow_comparisons(
            comparisons
        )
        if comparisons
        else None
    )

    return PersistedRiskShadowEvaluation(
        coverage=RiskShadowCoverage(
            total_events=len(
                assessments_by_event
            ),
            paired=len(
                comparisons
            ),
            v2_only=v2_only,
            v3_only=v3_only,
        ),
        evaluation=evaluation,
    )
