import uuid

from src.services.risk_version import (
    RISK_VERSION_V2,
    RISK_VERSION_V3,
)
from src.services.risk_version_comparison_service import (
    RiskComparisonValue,
    compare_risk_values,
)


def _value(
    *,
    score: float,
    topology_score: float,
    topology_contribution: float,
    shift: float,
    centrality: float,
    downstream: float,
) -> RiskComparisonValue:
    return RiskComparisonValue(
        score=score,
        semantic_magnitude_contribution=0.12,
        legacy_impact_contribution=0.06,
        semantic_severity_contribution=0.04,
        page_context_contribution=0.03,
        confidence_contribution=0.05,
        topology_contribution=topology_contribution,
        topology_score=topology_score,
        topology_shift_component=shift,
        topology_centrality_component=centrality,
        topology_downstream_component=downstream,
        has_topology_signal=True,
        has_previous_topology_state=True,
    )


def test_compare_risk_values_does_not_require_persisted_assessment_ids():
    organization_id = uuid.uuid4()
    event_id = uuid.uuid4()

    result = compare_risk_values(
        organization_id=organization_id,
        event_id=event_id,
        baseline=_value(
            score=0.45,
            topology_score=0.60,
            topology_contribution=0.15,
            shift=0.40,
            centrality=0.80,
            downstream=0.80,
        ),
        candidate=_value(
            score=0.40,
            topology_score=0.40,
            topology_contribution=0.10,
            shift=0.40,
            centrality=0.30,
            downstream=0.20,
        ),
    )

    assert result.organization_id == organization_id
    assert result.event_id == event_id
    assert result.baseline_version == RISK_VERSION_V2
    assert result.candidate_version == RISK_VERSION_V3

    assert result.baseline_assessment_id is None
    assert result.candidate_assessment_id is None

    assert result.baseline_score == 0.45
    assert result.candidate_score == 0.40
    assert result.score_delta == -0.05
    assert result.direction == "decreased"

    assert result.contributions.semantic_magnitude == 0.0
    assert result.contributions.legacy_impact == 0.0
    assert result.contributions.semantic_severity == 0.0
    assert result.contributions.page_context == 0.0
    assert result.contributions.confidence == 0.0
    assert result.contributions.topology == -0.05

    assert result.topology.score_delta == -0.20
    assert result.topology.shift_component_delta == 0.0
    assert result.topology.centrality_component_delta == -0.50
    assert result.topology.downstream_component_delta == -0.60

    assert result.topology.baseline_shift_component == 0.40
    assert result.topology.candidate_shift_component == 0.40
    assert result.topology.baseline_centrality_component == 0.80
    assert result.topology.candidate_centrality_component == 0.30
    assert result.topology.baseline_downstream_component == 0.80
    assert result.topology.candidate_downstream_component == 0.20

    assert result.topology.signal_state_changed is False
    assert result.topology.previous_state_changed is False
    assert result.topology.baseline_has_signal is True
    assert result.topology.candidate_has_signal is True
    assert result.topology.baseline_has_previous_state is True
    assert result.topology.candidate_has_previous_state is True
