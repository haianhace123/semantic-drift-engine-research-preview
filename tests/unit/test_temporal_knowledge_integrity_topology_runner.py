from __future__ import annotations

from benchmarks.temporal_knowledge_integrity.run_topology_benchmark import (
    run_topology_benchmark,
    score_topology_cases,
)
from benchmarks.temporal_knowledge_integrity.topology_cases import (
    load_topology_cases,
)


def test_topology_runner_scores_every_case() -> None:
    cases = load_topology_cases()

    scored_cases = score_topology_cases(cases)

    assert len(scored_cases) == len(cases)
    assert {
        scored_case.case.case_id
        for scored_case in scored_cases
    } == {
        case.id
        for case in cases
    }

    assert all(
        0.0 <= scored_case.score <= 1.0
        for scored_case in scored_cases
    )


def test_topology_runner_preserves_ground_truth_labels() -> None:
    cases = load_topology_cases()
    by_id = {
        case.id: case
        for case in cases
    }

    scored_cases = score_topology_cases(cases)

    for scored_case in scored_cases:
        source_case = by_id[
            scored_case.case.case_id
        ]

        assert (
            scored_case.case.consequential_change
            == source_case.consequential_change
        )
        assert (
            scored_case.case.critical_change
            == source_case.critical_change
        )
        assert (
            scored_case.case.pair_id
            == source_case.pair_id
        )


def test_topology_runner_uses_frozen_calibration_threshold() -> None:
    result = run_topology_benchmark()

    expected_true_positive = sum(
        scored_case.case.consequential_change
        and scored_case.score >= result.calibration.threshold
        for scored_case in result.evaluation_scores
    )
    expected_false_positive = sum(
        not scored_case.case.consequential_change
        and scored_case.score >= result.calibration.threshold
        for scored_case in result.evaluation_scores
    )
    expected_true_negative = sum(
        not scored_case.case.consequential_change
        and scored_case.score < result.calibration.threshold
        for scored_case in result.evaluation_scores
    )
    expected_false_negative = sum(
        scored_case.case.consequential_change
        and scored_case.score < result.calibration.threshold
        for scored_case in result.evaluation_scores
    )

    assert (
        result.evaluation.metrics.true_positive
        == expected_true_positive
    )
    assert (
        result.evaluation.metrics.false_positive
        == expected_false_positive
    )
    assert (
        result.evaluation.metrics.true_negative
        == expected_true_negative
    )
    assert (
        result.evaluation.metrics.false_negative
        == expected_false_negative
    )


def test_topology_runner_keeps_splits_independent() -> None:
    result = run_topology_benchmark()

    calibration_ids = {
        scored_case.case.case_id
        for scored_case in result.calibration_scores
    }
    evaluation_ids = {
        scored_case.case.case_id
        for scored_case in result.evaluation_scores
    }

    assert len(calibration_ids) == 12
    assert len(evaluation_ids) == 12
    assert calibration_ids.isdisjoint(
        evaluation_ids
    )


def test_topology_runner_calibrates_for_zero_fpr() -> None:
    result = run_topology_benchmark(
        maximum_false_positive_rate=0.0
    )

    assert (
        result.calibration.metrics.false_positive_rate
        == 0.0
    )