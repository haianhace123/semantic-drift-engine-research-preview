from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
from typing import Sequence

@dataclass(frozen=True)
class BinaryClassificationMetrics:
    true_positive: int
    false_positive: int
    true_negative: int
    false_negative: int
    precision: float
    recall: float
    f1: float
    false_positive_rate: float


def calculate_binary_metrics(
    *,
    labels: Iterable[bool],
    predictions: Iterable[bool],
) -> BinaryClassificationMetrics:
    label_values = tuple(labels)
    prediction_values = tuple(predictions)

    if len(label_values) != len(prediction_values):
        raise ValueError(
            "labels and predictions must contain the same number of values."
        )

    if not label_values:
        raise ValueError(
            "labels and predictions must not be empty."
        )

    true_positive = sum(
        label and prediction
        for label, prediction in zip(
            label_values,
            prediction_values,
            strict=True,
        )
    )
    false_positive = sum(
        not label and prediction
        for label, prediction in zip(
            label_values,
            prediction_values,
            strict=True,
        )
    )
    true_negative = sum(
        not label and not prediction
        for label, prediction in zip(
            label_values,
            prediction_values,
            strict=True,
        )
    )
    false_negative = sum(
        label and not prediction
        for label, prediction in zip(
            label_values,
            prediction_values,
            strict=True,
        )
    )

    precision_denominator = (
        true_positive
        + false_positive
    )
    recall_denominator = (
        true_positive
        + false_negative
    )
    false_positive_rate_denominator = (
        false_positive
        + true_negative
    )

    precision = (
        true_positive / precision_denominator
        if precision_denominator
        else 0.0
    )
    recall = (
        true_positive / recall_denominator
        if recall_denominator
        else 0.0
    )
    f1 = (
        2.0
        * precision
        * recall
        / (precision + recall)
        if precision + recall
        else 0.0
    )
    false_positive_rate = (
        false_positive
        / false_positive_rate_denominator
        if false_positive_rate_denominator
        else 0.0
    )

    return BinaryClassificationMetrics(
        true_positive=true_positive,
        false_positive=false_positive,
        true_negative=true_negative,
        false_negative=false_negative,
        precision=precision,
        recall=recall,
        f1=f1,
        false_positive_rate=false_positive_rate,
    )


def calculate_critical_change_recall(
    *,
    critical_labels: Iterable[bool],
    predictions: Iterable[bool],
) -> float:
    critical_values = tuple(critical_labels)
    prediction_values = tuple(predictions)

    if len(critical_values) != len(
        prediction_values
    ):
        raise ValueError(
            "critical_labels and predictions must contain "
            "the same number of values."
        )

    critical_count = sum(
        critical_values
    )

    if critical_count == 0:
        return 0.0

    detected_count = sum(
        is_critical and prediction
        for is_critical, prediction in zip(
            critical_values,
            prediction_values,
            strict=True,
        )
    )

    return detected_count / critical_count

@dataclass(frozen=True)
class PairedDiscriminationMetrics:
    pair_count: int
    correctly_ranked: int
    tied: int
    incorrectly_ranked: int
    accuracy: float


def calculate_paired_discrimination(
    scored_cases: Sequence[
        tuple[str | None, bool, float]
    ],
) -> PairedDiscriminationMetrics:
    pairs: dict[
        str,
        list[tuple[bool, float]],
    ] = {}

    for (
        pair_id,
        consequential_change,
        score,
    ) in scored_cases:
        if pair_id is None:
            continue

        pairs.setdefault(
            pair_id,
            [],
        ).append(
            (
                consequential_change,
                float(score),
            )
        )

    correctly_ranked = 0
    tied = 0
    incorrectly_ranked = 0

    for pair_id, values in pairs.items():
        if len(values) != 2:
            raise ValueError(
                "Paired discrimination requires exactly "
                f"two cases per pair: {pair_id}."
            )

        benign_scores = [
            score
            for label, score in values
            if not label
        ]
        consequential_scores = [
            score
            for label, score in values
            if label
        ]

        if (
            len(benign_scores) != 1
            or len(consequential_scores) != 1
        ):
            raise ValueError(
                "Paired discrimination requires one "
                "benign and one consequential case per "
                f"pair: {pair_id}."
            )

        benign_score = benign_scores[0]
        consequential_score = (
            consequential_scores[0]
        )

        if consequential_score > benign_score:
            correctly_ranked += 1
        elif consequential_score == benign_score:
            tied += 1
        else:
            incorrectly_ranked += 1

    pair_count = len(
        pairs
    )

    accuracy = (
        correctly_ranked / pair_count
        if pair_count
        else 0.0
    )

    return PairedDiscriminationMetrics(
        pair_count=pair_count,
        correctly_ranked=correctly_ranked,
        tied=tied,
        incorrectly_ranked=incorrectly_ranked,
        accuracy=accuracy,
    )