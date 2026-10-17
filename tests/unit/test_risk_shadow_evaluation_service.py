from __future__ import annotations

import uuid

import pytest

from src.services.risk_shadow_evaluation_service import (
    evaluate_risk_shadow_comparisons,
)
from src.services.risk_version import (
    RISK_VERSION_V2,
    RISK_VERSION_V3,
)
from src.services.risk_version_comparison_service import (
    RiskContributionDelta,
    RiskTopologyComparison,
    RiskVersionComparison,
)


def _comparison(
    *,
    baseline_score,
    candidate_score,
    score_delta,
    direction,
    topology_delta,
    topology_contribution_delta,
    semantic_delta=0.0,
    has_signal=True,
    has_previous_state=True,
    baseline_shift_component=0.0,
    candidate_shift_component=0.0,
    baseline_centrality_component=0.0,
    candidate_centrality_component=0.0,
    baseline_downstream_component=0.0,
    candidate_downstream_component=0.0,
):
    return RiskVersionComparison(
        organization_id=uuid.uuid4(),
        event_id=uuid.uuid4(),
        baseline_version=RISK_VERSION_V2,
        candidate_version=RISK_VERSION_V3,
        baseline_assessment_id=uuid.uuid4(),
        candidate_assessment_id=uuid.uuid4(),
        baseline_score=baseline_score,
        candidate_score=candidate_score,
        score_delta=score_delta,
        direction=direction,
        contributions=RiskContributionDelta(
            semantic_magnitude=semantic_delta,
            legacy_impact=0.0,
            semantic_severity=0.0,
            page_context=0.0,
            confidence=0.0,
            topology=topology_contribution_delta,
        ),
        topology=RiskTopologyComparison(
            score_delta=topology_delta,
            shift_component_delta=0.0,
            centrality_component_delta=0.0,
            downstream_component_delta=0.0,
            baseline_shift_component=baseline_shift_component,
            candidate_shift_component=candidate_shift_component,
            baseline_centrality_component=(
                baseline_centrality_component
            ),
            candidate_centrality_component=(
                candidate_centrality_component
            ),
            baseline_downstream_component=(
                baseline_downstream_component
            ),
            candidate_downstream_component=(
                candidate_downstream_component
            ),
            signal_state_changed=False,
            previous_state_changed=False,
            baseline_has_signal=has_signal,
            candidate_has_signal=has_signal,
            baseline_has_previous_state=has_previous_state,
            candidate_has_previous_state=has_previous_state,
        ),
    )


def test_evaluate_shadow_comparisons_aggregates_deltas():
    comparisons = [
        _comparison(
            baseline_score=0.39,
            candidate_score=0.41,
            score_delta=0.02,
            direction="increased",
            topology_delta=0.08,
            topology_contribution_delta=0.02,
        ),
        _comparison(
            baseline_score=0.70,
            candidate_score=0.65,
            score_delta=-0.05,
            direction="decreased",
            topology_delta=-0.20,
            topology_contribution_delta=-0.05,
        ),
        _comparison(
            baseline_score=0.20,
            candidate_score=0.20,
            score_delta=0.0,
            direction="unchanged",
            topology_delta=0.0,
            topology_contribution_delta=0.0,
        ),
    ]

    result = evaluate_risk_shadow_comparisons(
        comparisons
    )

    assert result.paired_count == 3

    assert result.score_delta.minimum == -0.05
    assert result.score_delta.maximum == 0.02
    assert result.score_delta.mean == pytest.approx(
        -0.01,
        abs=1e-6,
    )
    assert result.score_delta.mean_absolute == pytest.approx(
        0.023333,
        abs=1e-6,
    )
    assert result.score_delta.median_absolute == 0.02
    assert result.score_delta.p95_absolute == 0.05

    assert result.movement.increased == 1
    assert result.movement.decreased == 1
    assert result.movement.unchanged == 1
    assert result.movement.classification_changed == 1

    assert result.topology.previous_state_pairs == 3
    assert result.topology.exercised_topology_pairs == 0
    assert result.topology.divergent_pairs == 2

    assert (
        result.non_topology_contribution_mismatches
        == 0
    )


def test_evaluate_shadow_comparisons_detects_non_topology_anomaly():
    result = evaluate_risk_shadow_comparisons(
        [
            _comparison(
                baseline_score=0.50,
                candidate_score=0.51,
                score_delta=0.01,
                direction="increased",
                topology_delta=0.04,
                topology_contribution_delta=0.01,
                semantic_delta=0.001,
            )
        ]
    )

    assert (
        result.non_topology_contribution_mismatches
        == 1
    )


def test_evaluate_shadow_comparisons_respects_mismatch_tolerance():
    result = evaluate_risk_shadow_comparisons(
        [
            _comparison(
                baseline_score=0.50,
                candidate_score=0.50,
                score_delta=0.0,
                direction="unchanged",
                topology_delta=0.0,
                topology_contribution_delta=0.0,
                semantic_delta=0.000001,
            )
        ],
        mismatch_tolerance=0.000001,
    )

    assert (
        result.non_topology_contribution_mismatches
        == 0
    )


def test_evaluate_shadow_comparisons_rejects_empty_input():
    with pytest.raises(
        ValueError,
        match="at least one paired comparison",
    ):
        evaluate_risk_shadow_comparisons([])


@pytest.mark.parametrize(
    "tolerance",
    [
        -0.1,
        float("nan"),
        float("inf"),
    ],
)
def test_evaluate_shadow_comparisons_rejects_invalid_tolerance(
    tolerance,
):
    comparison = _comparison(
        baseline_score=0.50,
        candidate_score=0.50,
        score_delta=0.0,
        direction="unchanged",
        topology_delta=0.0,
        topology_contribution_delta=0.0,
    )

    with pytest.raises(
        ValueError,
        match="finite and non-negative",
    ):
        evaluate_risk_shadow_comparisons(
            [comparison],
            mismatch_tolerance=tolerance,
        )

def test_evaluate_shadow_comparisons_counts_exercised_topology_separately():
    result = evaluate_risk_shadow_comparisons(
        [
            _comparison(
                baseline_score=0.40,
                candidate_score=0.40,
                score_delta=0.0,
                direction="unchanged",
                topology_delta=0.0,
                topology_contribution_delta=0.0,
                baseline_shift_component=0.40,
                candidate_shift_component=0.40,
            ),
            _comparison(
                baseline_score=0.30,
                candidate_score=0.30,
                score_delta=0.0,
                direction="unchanged",
                topology_delta=0.0,
                topology_contribution_delta=0.0,
            ),
            _comparison(
                baseline_score=0.20,
                candidate_score=0.20,
                score_delta=0.0,
                direction="unchanged",
                topology_delta=0.0,
                topology_contribution_delta=0.0,
                baseline_shift_component=0.50,
                candidate_shift_component=0.50,
                has_previous_state=False,
            ),
        ]
    )

    assert result.topology.previous_state_pairs == 2
    assert result.topology.exercised_topology_pairs == 1
    assert result.topology.divergent_pairs == 0


def test_exercised_topology_respects_mismatch_tolerance():
    result = evaluate_risk_shadow_comparisons(
        [
            _comparison(
                baseline_score=0.40,
                candidate_score=0.40,
                score_delta=0.0,
                direction="unchanged",
                topology_delta=0.0,
                topology_contribution_delta=0.0,
                baseline_shift_component=0.000001,
                candidate_shift_component=0.000001,
            )
        ],
        mismatch_tolerance=0.000001,
    )

    assert result.topology.exercised_topology_pairs == 0
