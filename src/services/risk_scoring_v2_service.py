from __future__ import annotations
import math

from dataclasses import dataclass


SEMANTIC_MAGNITUDE_WEIGHT = 0.30
LEGACY_IMPACT_WEIGHT = 0.15
SEMANTIC_SEVERITY_WEIGHT = 0.10
PAGE_CONTEXT_WEIGHT = 0.10
CONFIDENCE_WEIGHT = 0.10
TOPOLOGY_WEIGHT = 0.25

TOPOLOGY_SHIFT_SCORE_WEIGHT = 0.50
TOPOLOGY_CENTRALITY_WEIGHT = 0.25
TOPOLOGY_DOWNSTREAM_WEIGHT = 0.25


@dataclass(frozen=True)
class TopologyRiskInput:
    shift_score: float
    degree_centrality_delta: float
    downstream_reach_ratio_delta: float
    has_previous_state: bool


@dataclass(frozen=True)
class RiskScoreV2Input:
    semantic_magnitude: float
    legacy_impact: float
    semantic_severity: float
    page_context: float
    confidence: float
    topology: TopologyRiskInput | None = None


@dataclass(frozen=True)
class RiskScoreV2Result:
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


def _clamp_unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))

def _require_unit_interval(
    value: float,
    *,
    name: str,
) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{name} must be numeric."
        ) from exc

    if not math.isfinite(result):
        raise ValueError(
            f"{name} must be finite."
        )

    if result < 0.0 or result > 1.0:
        raise ValueError(
            f"{name} must be between 0 and 1."
        )

    return result

def _round(value: float) -> float:
    return round(float(value), 6)


def calculate_topology_risk(
    topology: TopologyRiskInput | None,
) -> tuple[
    float,
    float,
    float,
    float,
    bool,
    bool,
]:
    """
    Return:
        topology_score,
        shift_component,
        centrality_component,
        downstream_component,
        has_topology_signal,
        has_previous_topology_state

    The first persisted topology state is a baseline, not a real
    topology change. Therefore it contributes zero risk.
    """
    if topology is None:
        return (
            0.0,
            0.0,
            0.0,
            0.0,
            False,
            False,
        )

    if not topology.has_previous_state:
        return (
            0.0,
            0.0,
            0.0,
            0.0,
            True,
            False,
        )

    shift_component = _require_unit_interval(
        topology.shift_score,
        name="topology.shift_score",
    )

    centrality_component = _require_unit_interval(
        abs(topology.degree_centrality_delta),
        name="abs(topology.degree_centrality_delta)",
    )

    downstream_component = _require_unit_interval(
        abs(topology.downstream_reach_ratio_delta),
        name="abs(topology.downstream_reach_ratio_delta)",
    )

    topology_score = (
        shift_component
        * TOPOLOGY_SHIFT_SCORE_WEIGHT
        + centrality_component
        * TOPOLOGY_CENTRALITY_WEIGHT
        + downstream_component
        * TOPOLOGY_DOWNSTREAM_WEIGHT
    )

    return (
        _round(_clamp_unit(topology_score)),
        _round(shift_component),
        _round(centrality_component),
        _round(downstream_component),
        True,
        True,
    )


def calculate_risk_score_v2(
    risk_input: RiskScoreV2Input,
) -> RiskScoreV2Result:
    semantic_magnitude = _require_unit_interval(
        risk_input.semantic_magnitude,
        name="semantic_magnitude",
    )

    legacy_impact = _require_unit_interval(
        risk_input.legacy_impact,
        name="legacy_impact",
    )

    semantic_severity = _require_unit_interval(
        risk_input.semantic_severity,
        name="semantic_severity",
    )

    page_context = _require_unit_interval(
        risk_input.page_context,
        name="page_context",
    )

    confidence = _require_unit_interval(
        risk_input.confidence,
        name="confidence",
    )

    (
        topology_score,
        topology_shift_component,
        topology_centrality_component,
        topology_downstream_component,
        has_topology_signal,
        has_previous_topology_state,
    ) = calculate_topology_risk(
        risk_input.topology
    )

    semantic_magnitude_contribution = (
        semantic_magnitude
        * SEMANTIC_MAGNITUDE_WEIGHT
    )

    legacy_impact_contribution = (
        legacy_impact
        * LEGACY_IMPACT_WEIGHT
    )

    semantic_severity_contribution = (
        semantic_severity
        * SEMANTIC_SEVERITY_WEIGHT
    )

    page_context_contribution = (
        page_context
        * PAGE_CONTEXT_WEIGHT
    )

    confidence_contribution = (
        confidence
        * CONFIDENCE_WEIGHT
    )

    topology_contribution = (
        topology_score
        * TOPOLOGY_WEIGHT
    )

    score = (
        semantic_magnitude_contribution
        + legacy_impact_contribution
        + semantic_severity_contribution
        + page_context_contribution
        + confidence_contribution
        + topology_contribution
    )

    return RiskScoreV2Result(
        score=_round(_clamp_unit(score)),
        semantic_magnitude_contribution=_round(
            semantic_magnitude_contribution
        ),
        legacy_impact_contribution=_round(
            legacy_impact_contribution
        ),
        semantic_severity_contribution=_round(
            semantic_severity_contribution
        ),
        page_context_contribution=_round(
            page_context_contribution
        ),
        confidence_contribution=_round(
            confidence_contribution
        ),
        topology_score=_round(topology_score),
        topology_contribution=_round(
            topology_contribution
        ),
        topology_shift_component=_round(
            topology_shift_component
        ),
        topology_centrality_component=_round(
            topology_centrality_component
        ),
        topology_downstream_component=_round(
            topology_downstream_component
        ),
        has_topology_signal=has_topology_signal,
        has_previous_topology_state=(
            has_previous_topology_state
        ),
    )