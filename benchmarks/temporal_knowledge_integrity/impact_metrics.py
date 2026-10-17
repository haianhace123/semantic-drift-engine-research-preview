from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class SetPredictionMetrics:
    true_positive: int
    false_positive: int
    false_negative: int
    precision: float
    recall: float
    f1: float
    exact_match: bool


@dataclass(frozen=True)
class DependencyImpactMetrics:
    affected: SetPredictionMetrics
    invalidation: SetPredictionMetrics
    critical: SetPredictionMetrics

@dataclass(frozen=True)
class AggregateSetPredictionMetrics:
    case_count: int
    true_positive: int
    false_positive: int
    false_negative: int
    precision: float
    recall: float
    f1: float
    exact_match_count: int
    exact_match_rate: float


@dataclass(frozen=True)
class AggregateDependencyImpactMetrics:
    affected: AggregateSetPredictionMetrics
    invalidation: AggregateSetPredictionMetrics
    critical: AggregateSetPredictionMetrics

def calculate_set_prediction_metrics(
    *,
    expected: Iterable[str],
    predicted: Iterable[str],
) -> SetPredictionMetrics:
    expected_values = set(expected)
    predicted_values = set(predicted)

    true_positive = len(
        expected_values
        & predicted_values
    )
    false_positive = len(
        predicted_values
        - expected_values
    )
    false_negative = len(
        expected_values
        - predicted_values
    )

    precision_denominator = (
        true_positive
        + false_positive
    )
    recall_denominator = (
        true_positive
        + false_negative
    )

    precision = (
        true_positive / precision_denominator
        if precision_denominator
        else 1.0
    )
    recall = (
        true_positive / recall_denominator
        if recall_denominator
        else 1.0
    )

    f1 = (
        2.0
        * precision
        * recall
        / (precision + recall)
        if precision + recall
        else 0.0
    )

    return SetPredictionMetrics(
        true_positive=true_positive,
        false_positive=false_positive,
        false_negative=false_negative,
        precision=precision,
        recall=recall,
        f1=f1,
        exact_match=(
            expected_values
            == predicted_values
        ),
    )


def calculate_dependency_impact_metrics(
    *,
    expected_affected: Iterable[str],
    predicted_affected: Iterable[str],
    expected_invalidation: Iterable[str],
    predicted_invalidation: Iterable[str],
    expected_critical: Iterable[str],
    predicted_critical: Iterable[str],
) -> DependencyImpactMetrics:
    return DependencyImpactMetrics(
        affected=calculate_set_prediction_metrics(
            expected=expected_affected,
            predicted=predicted_affected,
        ),
        invalidation=calculate_set_prediction_metrics(
            expected=expected_invalidation,
            predicted=predicted_invalidation,
        ),
        critical=calculate_set_prediction_metrics(
            expected=expected_critical,
            predicted=predicted_critical,
        ),
    )

def aggregate_set_prediction_metrics(
    metrics: Sequence[SetPredictionMetrics],
) -> AggregateSetPredictionMetrics:
    case_count = len(metrics)

    true_positive = sum(
        item.true_positive
        for item in metrics
    )
    false_positive = sum(
        item.false_positive
        for item in metrics
    )
    false_negative = sum(
        item.false_negative
        for item in metrics
    )
    exact_match_count = sum(
        1
        for item in metrics
        if item.exact_match
    )

    precision_denominator = (
        true_positive
        + false_positive
    )
    recall_denominator = (
        true_positive
        + false_negative
    )

    precision = (
        true_positive / precision_denominator
        if precision_denominator
        else 1.0
    )
    recall = (
        true_positive / recall_denominator
        if recall_denominator
        else 1.0
    )

    f1 = (
        2.0
        * precision
        * recall
        / (precision + recall)
        if precision + recall
        else 0.0
    )

    exact_match_rate = (
        exact_match_count / case_count
        if case_count
        else 1.0
    )

    return AggregateSetPredictionMetrics(
        case_count=case_count,
        true_positive=true_positive,
        false_positive=false_positive,
        false_negative=false_negative,
        precision=precision,
        recall=recall,
        f1=f1,
        exact_match_count=exact_match_count,
        exact_match_rate=exact_match_rate,
    )


def aggregate_dependency_impact_metrics(
    metrics: Sequence[DependencyImpactMetrics],
) -> AggregateDependencyImpactMetrics:
    return AggregateDependencyImpactMetrics(
        affected=aggregate_set_prediction_metrics(
            tuple(
                item.affected
                for item in metrics
            )
        ),
        invalidation=aggregate_set_prediction_metrics(
            tuple(
                item.invalidation
                for item in metrics
            )
        ),
        critical=aggregate_set_prediction_metrics(
            tuple(
                item.critical
                for item in metrics
            )
        ),
    )