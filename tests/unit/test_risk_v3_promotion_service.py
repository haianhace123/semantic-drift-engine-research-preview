from __future__ import annotations

import pytest

from src.services.risk_shadow_evaluation_service import (
    PersistedRiskShadowEvaluation,
    RiskShadowCoverage,
    RiskShadowDeltaDistribution,
    RiskShadowEvaluation,
    RiskShadowMovement,
    RiskShadowTopologyEvaluation,
)
from src.services.risk_v3_promotion_service import (
    RiskV3PromotionPolicy,
    evaluate_risk_v3_promotion_readiness,
    summarize_risk_v3_qualification_evaluation,
    summarize_risk_v3_qualification_evidence,
)


def _policy(
    *,
    minimum_paired_events=10,
    minimum_pair_coverage=1.0,
    minimum_previous_state_pairs=5,
    maximum_classification_change_rate=0.10,
):
    return RiskV3PromotionPolicy(
        minimum_paired_events=minimum_paired_events,
        minimum_pair_coverage=minimum_pair_coverage,
        minimum_previous_state_pairs=minimum_previous_state_pairs,
        maximum_classification_change_rate=(
            maximum_classification_change_rate
        ),
    )


def _shadow(
    *,
    total_events=10,
    paired=10,
    v2_only=0,
    v3_only=0,
    previous_state_pairs=8,
    exercised_topology_pairs=3,
    divergent_pairs=3,
    classification_changed=1,
    non_topology_mismatches=0,
    signal_state_mismatches=0,
    previous_state_mismatches=0,
):
    evaluation = None

    if paired > 0:
        evaluation = RiskShadowEvaluation(
            paired_count=paired,
            score_delta=RiskShadowDeltaDistribution(
                minimum=-0.05,
                maximum=0.04,
                mean=-0.001,
                mean_absolute=0.02,
                median_absolute=0.01,
                p95_absolute=0.05,
            ),
            movement=RiskShadowMovement(
                increased=3,
                decreased=3,
                unchanged=max(0, paired - 6),
                classification_changed=classification_changed,
            ),
            topology=RiskShadowTopologyEvaluation(
                mean_score_delta=-0.01,
                mean_absolute_score_delta=0.05,
                mean_contribution_delta=-0.0025,
                mean_absolute_contribution_delta=0.0125,
                signal_state_mismatches=signal_state_mismatches,
                previous_state_mismatches=previous_state_mismatches,
                previous_state_pairs=previous_state_pairs,
                exercised_topology_pairs=exercised_topology_pairs,
                divergent_pairs=divergent_pairs,
            ),
            non_topology_contribution_mismatches=(
                non_topology_mismatches
            ),
        )

    return PersistedRiskShadowEvaluation(
        coverage=RiskShadowCoverage(
            total_events=total_events,
            paired=paired,
            v2_only=v2_only,
            v3_only=v3_only,
        ),
        evaluation=evaluation,
    )


def _checks_by_name(result):
    return {
        check.name: check
        for check in result.checks
    }


def test_qualification_evidence_reports_observed_topology_facts():
    evidence = summarize_risk_v3_qualification_evidence(
        _shadow(
            previous_state_pairs=8,
            exercised_topology_pairs=3,
            divergent_pairs=2,
        )
    )

    assert evidence.previous_state_pairs == 8
    assert evidence.exercised_topology_pairs == 3
    assert evidence.divergent_pairs == 2
    assert evidence.topology_exercise_observed is True


def test_qualification_evaluation_matches_persisted_wrapper():
    shadow = _shadow(
        previous_state_pairs=8,
        exercised_topology_pairs=3,
        divergent_pairs=2,
    )

    direct = summarize_risk_v3_qualification_evaluation(
        shadow.evaluation
    )
    persisted = summarize_risk_v3_qualification_evidence(
        shadow
    )

    assert direct == persisted


def test_qualification_evidence_distinguishes_temporal_from_exercised_evidence():
    evidence = summarize_risk_v3_qualification_evidence(
        _shadow(
            previous_state_pairs=8,
            exercised_topology_pairs=0,
            divergent_pairs=0,
        )
    )

    assert evidence.previous_state_pairs == 8
    assert evidence.exercised_topology_pairs == 0
    assert evidence.divergent_pairs == 0
    assert evidence.topology_exercise_observed is False


def test_qualification_evidence_handles_empty_shadow():
    evidence = summarize_risk_v3_qualification_evidence(
        _shadow(
            total_events=0,
            paired=0,
            previous_state_pairs=0,
            exercised_topology_pairs=0,
            divergent_pairs=0,
            classification_changed=0,
        )
    )

    assert evidence.previous_state_pairs == 0
    assert evidence.exercised_topology_pairs == 0
    assert evidence.divergent_pairs == 0
    assert evidence.topology_exercise_observed is False


def test_promotion_readiness_passes_when_all_explicit_gates_pass():
    result = evaluate_risk_v3_promotion_readiness(
        _shadow(),
        policy=_policy(),
    )

    assert result.ready is True
    assert all(check.passed for check in result.checks)


def test_zero_divergence_does_not_fail_promotion_contract():
    result = evaluate_risk_v3_promotion_readiness(
        _shadow(
            previous_state_pairs=8,
            divergent_pairs=0,
            classification_changed=0,
        ),
        policy=_policy(),
    )

    checks = _checks_by_name(result)

    assert result.ready is True
    assert "algorithm_divergence_evidence" not in checks


def test_missing_version_and_non_topology_anomaly_fail_closed():
    result = evaluate_risk_v3_promotion_readiness(
        _shadow(
            total_events=11,
            paired=10,
            v2_only=1,
            non_topology_mismatches=1,
        ),
        policy=_policy(
            minimum_pair_coverage=0.90,
        ),
    )

    checks = _checks_by_name(result)

    assert result.ready is False
    assert checks["missing_versions"].passed is False
    assert checks["non_topology_invariance"].passed is False


def test_topology_state_mismatch_fails_closed():
    result = evaluate_risk_v3_promotion_readiness(
        _shadow(
            signal_state_mismatches=1,
        ),
        policy=_policy(),
    )

    checks = _checks_by_name(result)

    assert result.ready is False
    assert checks["topology_state_invariance"].passed is False


def test_classification_change_rate_uses_explicit_policy():
    result = evaluate_risk_v3_promotion_readiness(
        _shadow(
            classification_changed=2,
        ),
        policy=_policy(
            maximum_classification_change_rate=0.10,
        ),
    )

    checks = _checks_by_name(result)

    assert result.ready is False
    assert checks["classification_change_rate"].observed == 0.2
    assert checks["classification_change_rate"].passed is False


def test_empty_shadow_evidence_is_not_ready():
    result = evaluate_risk_v3_promotion_readiness(
        _shadow(
            total_events=0,
            paired=0,
            previous_state_pairs=0,
            divergent_pairs=0,
            classification_changed=0,
        ),
        policy=_policy(),
    )

    checks = _checks_by_name(result)

    assert result.ready is False
    assert checks["paired_events"].passed is False
    assert checks["pair_coverage"].passed is False
    assert checks["previous_topology_evidence"].passed is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("minimum_paired_events", 0),
        ("minimum_paired_events", 1.5),
        ("minimum_paired_events", True),
        ("minimum_pair_coverage", -0.1),
        ("minimum_pair_coverage", 1.1),
        ("minimum_pair_coverage", float("nan")),
        ("minimum_previous_state_pairs", 0),
        ("minimum_previous_state_pairs", 1.5),
        ("maximum_classification_change_rate", -0.1),
        ("maximum_classification_change_rate", 1.1),
        ("maximum_classification_change_rate", float("inf")),
    ],
)
def test_promotion_policy_rejects_invalid_thresholds(
    field,
    value,
):
    values = {
        "minimum_paired_events": 10,
        "minimum_pair_coverage": 1.0,
        "minimum_previous_state_pairs": 5,
        "maximum_classification_change_rate": 0.10,
    }

    values[field] = value

    with pytest.raises(ValueError):
        evaluate_risk_v3_promotion_readiness(
            _shadow(),
            policy=RiskV3PromotionPolicy(**values),
        )
