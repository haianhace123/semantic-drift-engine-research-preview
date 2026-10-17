from __future__ import annotations

import pytest

from src.services.risk_scoring_v3_service import (
    RiskScoreV3Input,
    TopologyRiskV3Input,
    calculate_risk_score_v3,
    calculate_topology_risk_v3,
)


def test_no_topology_signal_contributes_zero():
    result = calculate_risk_score_v3(
        RiskScoreV3Input(
            semantic_magnitude=0.8,
            legacy_impact=0.6,
            semantic_severity=0.7,
            page_context=0.5,
            confidence=0.9,
            topology=None,
        )
    )

    assert result.has_topology_signal is False
    assert result.has_previous_topology_state is False

    assert result.topology_score == 0.0
    assert result.topology_contribution == 0.0

    assert result.score == pytest.approx(
        0.54,
        abs=1e-6,
    )


def test_first_topology_state_is_baseline_not_risk():
    result = calculate_risk_score_v3(
        RiskScoreV3Input(
            semantic_magnitude=0.0,
            legacy_impact=0.0,
            semantic_severity=0.0,
            page_context=0.0,
            confidence=0.0,
            topology=TopologyRiskV3Input(
                shift_score=1.0,
                degree_centrality_delta=1.0,
                downstream_reach_ratio_delta=1.0,
                has_previous_state=False,
            ),
        )
    )

    assert result.has_topology_signal is True
    assert result.has_previous_topology_state is False

    assert result.topology_score == 0.0
    assert result.topology_contribution == 0.0
    assert result.score == 0.0


def test_topology_risk_uses_shift_score_without_double_counting():
    (
        topology_score,
        shift_component,
        centrality_component,
        downstream_component,
        has_signal,
        has_previous,
    ) = calculate_topology_risk_v3(
        TopologyRiskV3Input(
            shift_score=0.8,
            degree_centrality_delta=0.4,
            downstream_reach_ratio_delta=-0.2,
            has_previous_state=True,
        )
    )

    assert has_signal is True
    assert has_previous is True

    assert shift_component == 0.8
    assert centrality_component == 0.4
    assert downstream_component == 0.2
    assert topology_score == 0.8


def test_topology_direction_does_not_hide_risk():
    positive = calculate_risk_score_v3(
        RiskScoreV3Input(
            semantic_magnitude=0.0,
            legacy_impact=0.0,
            semantic_severity=0.0,
            page_context=0.0,
            confidence=0.0,
            topology=TopologyRiskV3Input(
                shift_score=0.4,
                degree_centrality_delta=0.3,
                downstream_reach_ratio_delta=0.2,
                has_previous_state=True,
            ),
        )
    )

    negative = calculate_risk_score_v3(
        RiskScoreV3Input(
            semantic_magnitude=0.0,
            legacy_impact=0.0,
            semantic_severity=0.0,
            page_context=0.0,
            confidence=0.0,
            topology=TopologyRiskV3Input(
                shift_score=0.4,
                degree_centrality_delta=-0.3,
                downstream_reach_ratio_delta=-0.2,
                has_previous_state=True,
            ),
        )
    )

    assert positive.topology_score == (
        negative.topology_score
    )

    assert positive.score == negative.score


def test_full_risk_v3_formula():
    result = calculate_risk_score_v3(
        RiskScoreV3Input(
            semantic_magnitude=0.8,
            legacy_impact=0.6,
            semantic_severity=0.7,
            page_context=0.5,
            confidence=0.9,
            topology=TopologyRiskV3Input(
                shift_score=0.8,
                degree_centrality_delta=0.4,
                downstream_reach_ratio_delta=0.2,
                has_previous_state=True,
            ),
        )
    )

    assert (
        result.semantic_magnitude_contribution
        == 0.24
    )

    assert result.legacy_impact_contribution == 0.09
    assert (
        result.semantic_severity_contribution
        == 0.07
    )
    assert result.page_context_contribution == 0.05
    assert result.confidence_contribution == 0.09

    assert result.topology_score == 0.8

    assert result.topology_contribution == pytest.approx(
        0.20,
        abs=1e-6,
    )

    assert result.score == pytest.approx(
        0.74,
        abs=1e-6,
    )


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("semantic_magnitude", -0.01),
        ("semantic_magnitude", 1.01),
        ("legacy_impact", -0.01),
        ("legacy_impact", 1.01),
        ("semantic_severity", -0.01),
        ("semantic_severity", 1.01),
        ("page_context", -0.01),
        ("page_context", 1.01),
        ("confidence", -0.01),
        ("confidence", 1.01),
    ),
)
def test_rejects_scalar_inputs_outside_unit_interval(
    field_name,
    value,
):
    values = {
        "semantic_magnitude": 0.5,
        "legacy_impact": 0.5,
        "semantic_severity": 0.5,
        "page_context": 0.5,
        "confidence": 0.5,
    }
    values[field_name] = value

    with pytest.raises(
        ValueError,
        match=field_name,
    ):
        calculate_risk_score_v3(
            RiskScoreV3Input(**values)
        )


@pytest.mark.parametrize(
    "value",
    (
        float("nan"),
        float("inf"),
        float("-inf"),
    ),
)
def test_rejects_non_finite_scalar_input(value):
    with pytest.raises(
        ValueError,
        match="semantic_magnitude must be finite",
    ):
        calculate_risk_score_v3(
            RiskScoreV3Input(
                semantic_magnitude=value,
                legacy_impact=0.5,
                semantic_severity=0.5,
                page_context=0.5,
                confidence=0.5,
            )
        )


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("shift_score", 1.01),
        ("degree_centrality_delta", 1.01),
        ("degree_centrality_delta", -1.01),
        ("downstream_reach_ratio_delta", 1.01),
        ("downstream_reach_ratio_delta", -1.01),
    ),
)
def test_rejects_topology_components_outside_unit_interval(
    field_name,
    value,
):
    topology_values = {
        "shift_score": 0.5,
        "degree_centrality_delta": 0.25,
        "downstream_reach_ratio_delta": 0.25,
        "has_previous_state": True,
    }
    topology_values[field_name] = value

    with pytest.raises(ValueError):
        calculate_topology_risk_v3(
            TopologyRiskV3Input(**topology_values)
        )


def test_zero_topology_change_adds_no_risk():
    result = calculate_risk_score_v3(
        RiskScoreV3Input(
            semantic_magnitude=0.5,
            legacy_impact=0.5,
            semantic_severity=0.5,
            page_context=0.5,
            confidence=0.5,
            topology=TopologyRiskV3Input(
                shift_score=0.0,
                degree_centrality_delta=0.0,
                downstream_reach_ratio_delta=0.0,
                has_previous_state=True,
            ),
        )
    )

    assert result.has_topology_signal is True
    assert result.has_previous_topology_state is True

    assert result.topology_score == 0.0
    assert result.topology_contribution == 0.0

    # Non-topology weights total 0.75.
    assert result.score == pytest.approx(
        0.375,
        abs=1e-6,
    )


@pytest.mark.parametrize(
    "value",
    (
        float("nan"),
        float("inf"),
        float("-inf"),
    ),
)
def test_rejects_non_finite_topology_component(value):
    with pytest.raises(
        ValueError,
        match="topology.shift_score must be finite",
    ):
        calculate_topology_risk_v3(
            TopologyRiskV3Input(
                shift_score=value,
                degree_centrality_delta=0.25,
                downstream_reach_ratio_delta=0.25,
                has_previous_state=True,
            )
        )


def test_maximum_inputs_produce_maximum_score():
    result = calculate_risk_score_v3(
        RiskScoreV3Input(
            semantic_magnitude=1.0,
            legacy_impact=1.0,
            semantic_severity=1.0,
            page_context=1.0,
            confidence=1.0,
            topology=TopologyRiskV3Input(
                shift_score=1.0,
                degree_centrality_delta=1.0,
                downstream_reach_ratio_delta=1.0,
                has_previous_state=True,
            ),
        )
    )

    assert result.topology_score == 1.0
    assert result.score == 1.0