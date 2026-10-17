from __future__ import annotations

from benchmarks.temporal_knowledge_integrity.impact_metrics import (
    aggregate_dependency_impact_metrics,
    aggregate_set_prediction_metrics,
    calculate_dependency_impact_metrics,
    calculate_set_prediction_metrics,
)


def test_set_metrics_are_perfect_for_exact_match() -> None:
    metrics = calculate_set_prediction_metrics(
        expected=(
            "chunk",
            "index",
            "agent",
        ),
        predicted=(
            "agent",
            "chunk",
            "index",
        ),
    )

    assert metrics.true_positive == 3
    assert metrics.false_positive == 0
    assert metrics.false_negative == 0
    assert metrics.precision == 1.0
    assert metrics.recall == 1.0
    assert metrics.f1 == 1.0
    assert metrics.exact_match is True


def test_set_metrics_measure_partial_recall() -> None:
    metrics = calculate_set_prediction_metrics(
        expected=(
            "chunk",
            "index",
            "agent",
            "workflow",
        ),
        predicted=(
            "chunk",
        ),
    )

    assert metrics.true_positive == 1
    assert metrics.false_positive == 0
    assert metrics.false_negative == 3
    assert metrics.precision == 1.0
    assert metrics.recall == 0.25
    assert metrics.f1 == 0.4
    assert metrics.exact_match is False


def test_set_metrics_measure_false_positive() -> None:
    metrics = calculate_set_prediction_metrics(
        expected=(
            "chunk",
        ),
        predicted=(
            "chunk",
            "unrelated",
        ),
    )

    assert metrics.true_positive == 1
    assert metrics.false_positive == 1
    assert metrics.false_negative == 0
    assert metrics.precision == 0.5
    assert metrics.recall == 1.0
    assert metrics.exact_match is False


def test_set_metrics_treat_empty_exact_result_as_correct() -> None:
    metrics = calculate_set_prediction_metrics(
        expected=(),
        predicted=(),
    )

    assert metrics.true_positive == 0
    assert metrics.false_positive == 0
    assert metrics.false_negative == 0
    assert metrics.precision == 1.0
    assert metrics.recall == 1.0
    assert metrics.f1 == 1.0
    assert metrics.exact_match is True


def test_set_metrics_penalize_prediction_when_expected_is_empty() -> None:
    metrics = calculate_set_prediction_metrics(
        expected=(),
        predicted=("unexpected",),
    )

    assert metrics.true_positive == 0
    assert metrics.false_positive == 1
    assert metrics.false_negative == 0
    assert metrics.precision == 0.0
    assert metrics.recall == 1.0
    assert metrics.f1 == 0.0
    assert metrics.exact_match is False


def test_set_metrics_penalize_missing_nonempty_expected_set() -> None:
    metrics = calculate_set_prediction_metrics(
        expected=(
            "agent",
            "workflow",
        ),
        predicted=(),
    )

    assert metrics.true_positive == 0
    assert metrics.false_positive == 0
    assert metrics.false_negative == 2
    assert metrics.precision == 1.0
    assert metrics.recall == 0.0
    assert metrics.f1 == 0.0
    assert metrics.exact_match is False


def test_dependency_metrics_keep_dimensions_separate() -> None:
    metrics = calculate_dependency_impact_metrics(
        expected_affected=(
            "chunk",
            "index",
            "agent",
        ),
        predicted_affected=(
            "chunk",
            "index",
        ),
        expected_invalidation=(
            "chunk",
            "index",
        ),
        predicted_invalidation=(
            "chunk",
        ),
        expected_critical=(
            "agent",
        ),
        predicted_critical=(),
    )

    assert metrics.affected.recall == (
        2 / 3
    )
    assert metrics.invalidation.recall == 0.5
    assert metrics.critical.recall == 0.0

    assert metrics.affected.exact_match is False
    assert metrics.invalidation.exact_match is False
    assert metrics.critical.exact_match is False

def test_aggregate_set_metrics_use_micro_counts() -> None:
    first = calculate_set_prediction_metrics(
        expected=(
            "a",
            "b",
            "c",
            "d",
        ),
        predicted=(
            "a",
        ),
    )
    second = calculate_set_prediction_metrics(
        expected=("e",),
        predicted=("e",),
    )

    metrics = aggregate_set_prediction_metrics(
        (
            first,
            second,
        )
    )

    assert metrics.case_count == 2
    assert metrics.true_positive == 2
    assert metrics.false_positive == 0
    assert metrics.false_negative == 3
    assert metrics.precision == 1.0
    assert metrics.recall == 0.4
    assert metrics.f1 == (
        2.0 * 1.0 * 0.4 / 1.4
    )
    assert metrics.exact_match_count == 1
    assert metrics.exact_match_rate == 0.5


def test_aggregate_set_metrics_handle_empty_suite() -> None:
    metrics = aggregate_set_prediction_metrics(())

    assert metrics.case_count == 0
    assert metrics.precision == 1.0
    assert metrics.recall == 1.0
    assert metrics.f1 == 1.0
    assert metrics.exact_match_count == 0
    assert metrics.exact_match_rate == 1.0


def test_aggregate_dependency_metrics_keep_dimensions_separate() -> None:
    first = calculate_dependency_impact_metrics(
        expected_affected=(
            "chunk",
            "index",
            "agent",
        ),
        predicted_affected=("chunk",),
        expected_invalidation=(
            "chunk",
            "index",
        ),
        predicted_invalidation=("chunk",),
        expected_critical=("agent",),
        predicted_critical=(),
    )
    second = calculate_dependency_impact_metrics(
        expected_affected=("workflow",),
        predicted_affected=("workflow",),
        expected_invalidation=(),
        predicted_invalidation=(),
        expected_critical=("workflow",),
        predicted_critical=("workflow",),
    )

    metrics = aggregate_dependency_impact_metrics(
        (
            first,
            second,
        )
    )

    assert metrics.affected.true_positive == 2
    assert metrics.affected.false_negative == 2
    assert metrics.affected.recall == 0.5

    assert metrics.invalidation.true_positive == 1
    assert metrics.invalidation.false_negative == 1
    assert metrics.invalidation.recall == 0.5

    assert metrics.critical.true_positive == 1
    assert metrics.critical.false_negative == 1
    assert metrics.critical.recall == 0.5