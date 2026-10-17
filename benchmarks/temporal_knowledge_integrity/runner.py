from __future__ import annotations

import json
import math
import re

from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable, Sequence

from benchmarks.temporal_knowledge_integrity.metrics import (
    BinaryClassificationMetrics,
    calculate_binary_metrics,
    calculate_critical_change_recall,
)


VALID_SPLITS = frozenset(
    {
        "calibration",
        "evaluation",
    }
)

VALID_DIFFICULTIES = frozenset(
    {
        "easy",
        "medium",
        "hard",
    }
)

_TOKEN_PATTERN = re.compile(
    r"\w+",
    flags=re.UNICODE,
)


@dataclass(frozen=True)
class BenchmarkCase:
    case_id: str
    pair_id: str | None
    split: str
    category: str
    domain: str
    difficulty: str
    before: str
    after: str
    consequential_change: bool
    critical_change: bool


@dataclass(frozen=True)
class ScoredCase:
    case: BenchmarkCase
    score: float


@dataclass(frozen=True)
class ThresholdEvaluation:
    threshold: float
    metrics: BinaryClassificationMetrics
    critical_change_recall: float


def load_cases(
    path: str | Path,
) -> tuple[BenchmarkCase, ...]:
    source_path = Path(path)

    with source_path.open(
        "r",
        encoding="utf-8",
    ) as handle:
        payload = json.load(
            handle
        )

    if not isinstance(
        payload,
        list,
    ):
        raise ValueError(
            "Benchmark case file must contain a JSON array."
        )

    cases: list[BenchmarkCase] = []
    seen_ids: set[str] = set()

    for index, item in enumerate(
        payload
    ):
        if not isinstance(
            item,
            dict,
        ):
            raise ValueError(
                "Benchmark case entries must be JSON objects."
            )

        required_fields = (
            "id",
            "pair_id",
            "split",
            "category",
            "domain",
            "difficulty",
            "before",
            "after",
            "consequential_change",
            "critical_change",
        )

        missing_fields = [
            field
            for field in required_fields
            if field not in item
        ]

        if missing_fields:
            raise ValueError(
                "Benchmark case "
                f"at index {index} is missing required fields: "
                f"{', '.join(missing_fields)}."
            )

        case_id = _required_string(
            item["id"],
            name="id",
        )

        if case_id in seen_ids:
            raise ValueError(
                f"Duplicate benchmark case id: {case_id}."
            )

        seen_ids.add(
            case_id
        )

        split = _required_string(
            item["split"],
            name="split",
        )

        if split not in VALID_SPLITS:
            raise ValueError(
                f"Unsupported benchmark split: {split}."
            )

        pair_id = _optional_string(
            item["pair_id"],
            name="pair_id",
        )

        difficulty = _required_string(
            item["difficulty"],
            name="difficulty",
        )

        if difficulty not in VALID_DIFFICULTIES:
            raise ValueError(
                "Unsupported benchmark difficulty: "
                f"{difficulty}."
            )

        consequential_change = _required_bool(
            item["consequential_change"],
            name="consequential_change",
        )
        critical_change = _required_bool(
            item["critical_change"],
            name="critical_change",
        )

        if (
            critical_change
            and not consequential_change
        ):
            raise ValueError(
                "critical_change cannot be true when "
                "consequential_change is false."
            )

        cases.append(
            BenchmarkCase(
                case_id=case_id,
                pair_id=pair_id,
                split=split,
                category=_required_string(
                    item["category"],
                    name="category",
                ),
                domain=_required_string(
                    item["domain"],
                    name="domain",
                ),
                difficulty=difficulty,
                before=_required_string(
                    item["before"],
                    name="before",
                ),
                after=_required_string(
                    item["after"],
                    name="after",
                ),
                consequential_change=consequential_change,
                critical_change=critical_change,
            )
        )

    _validate_case_pairs(
        cases
    )

    if not cases:
        raise ValueError(
            "Benchmark case file must not be empty."
        )

    return tuple(
        cases
    )


def normalized_text(
    value: str,
) -> str:
    return " ".join(
        value.casefold().split()
    )


def normalized_text_change_score(
    before: str,
    after: str,
) -> float:
    return float(
        normalized_text(before)
        != normalized_text(after)
    )


def lexical_change_score(
    before: str,
    after: str,
) -> float:
    normalized_before = normalized_text(
        before
    )
    normalized_after = normalized_text(
        after
    )

    similarity = SequenceMatcher(
        None,
        normalized_before,
        normalized_after,
        autojunk=False,
    ).ratio()

    return _bounded_score(
        1.0 - similarity
    )


def token_change_score(
    before: str,
    after: str,
) -> float:
    before_tokens = set(
        _TOKEN_PATTERN.findall(
            normalized_text(before)
        )
    )
    after_tokens = set(
        _TOKEN_PATTERN.findall(
            normalized_text(after)
        )
    )

    union = (
        before_tokens
        | after_tokens
    )

    if not union:
        return 0.0

    intersection = (
        before_tokens
        & after_tokens
    )

    similarity = (
        len(intersection)
        / len(union)
    )

    return _bounded_score(
        1.0 - similarity
    )


def score_cases(
    cases: Iterable[BenchmarkCase],
    *,
    scorer,
) -> tuple[ScoredCase, ...]:
    return tuple(
        ScoredCase(
            case=case,
            score=_bounded_score(
                scorer(
                    case.before,
                    case.after,
                )
            ),
        )
        for case in cases
    )


def calibrate_threshold(
    scored_cases: Sequence[ScoredCase],
    *,
    maximum_false_positive_rate: float,
) -> ThresholdEvaluation:
    if not scored_cases:
        raise ValueError(
            "Calibration cases must not be empty."
        )

    if not (
        0.0
        <= maximum_false_positive_rate
        <= 1.0
    ):
        raise ValueError(
            "maximum_false_positive_rate must be "
            "between 0 and 1."
        )

    thresholds = _candidate_thresholds(
        scored_cases
    )

    evaluations = tuple(
        evaluate_threshold(
            scored_cases,
            threshold=threshold,
        )
        for threshold in thresholds
    )

    eligible = tuple(
        evaluation
        for evaluation in evaluations
        if (
            evaluation.metrics.false_positive_rate
            <= maximum_false_positive_rate
        )
    )

    if not eligible:
        raise ValueError(
            "No threshold satisfies the requested "
            "false-positive-rate constraint."
        )

    return max(
        eligible,
        key=lambda evaluation: (
            evaluation.critical_change_recall,
            evaluation.metrics.recall,
            evaluation.metrics.precision,
            -evaluation.threshold,
        ),
    )


def evaluate_threshold(
    scored_cases: Sequence[ScoredCase],
    *,
    threshold: float,
) -> ThresholdEvaluation:
    if not scored_cases:
        raise ValueError(
            "Evaluation cases must not be empty."
        )

    normalized_threshold = float(
        threshold
    )

    if not math.isfinite(
        normalized_threshold
    ):
        raise ValueError(
            "Benchmark threshold must be finite."
        )

    if normalized_threshold < 0.0:
        raise ValueError(
            "Benchmark threshold must not be negative."
        )

    predictions = tuple(
        item.score
        >= normalized_threshold
        for item in scored_cases
    )

    metrics = calculate_binary_metrics(
        labels=(
            item.case.consequential_change
            for item in scored_cases
        ),
        predictions=predictions,
    )

    critical_change_recall = (
        calculate_critical_change_recall(
            critical_labels=(
                item.case.critical_change
                for item in scored_cases
            ),
            predictions=predictions,
        )
    )

    return ThresholdEvaluation(
        threshold=normalized_threshold,
        metrics=metrics,
        critical_change_recall=critical_change_recall,
    )


def cases_for_split(
    cases: Iterable[BenchmarkCase],
    *,
    split: str,
) -> tuple[BenchmarkCase, ...]:
    if split not in VALID_SPLITS:
        raise ValueError(
            f"Unsupported benchmark split: {split}."
        )

    return tuple(
        case
        for case in cases
        if case.split == split
    )


def _candidate_thresholds(
    scored_cases: Sequence[ScoredCase],
) -> tuple[float, ...]:
    scores = {
        item.score
        for item in scored_cases
    }

    maximum_score = max(
        scores
    )
    reject_all_threshold = math.nextafter(
        maximum_score,
        math.inf,
    )

    return tuple(
        sorted(
            scores
            | {
                0.0,
                1.0,
                reject_all_threshold,
            }
        )
    )


def _bounded_score(
    value: float,
) -> float:
    score = float(
        value
    )

    if not math.isfinite(
        score
    ):
        raise ValueError(
            "Benchmark scores must be finite."
        )

    if not (
        0.0
        <= score
        <= 1.0
    ):
        raise ValueError(
            "Benchmark scores must be between 0 and 1."
        )

    return score


def _required_string(
    value: object,
    *,
    name: str,
) -> str:
    if not isinstance(
        value,
        str,
    ):
        raise ValueError(
            f"{name} must be a string."
        )

    normalized = value.strip()

    if not normalized:
        raise ValueError(
            f"{name} must not be empty."
        )

    return normalized


def _required_bool(
    value: object,
    *,
    name: str,
) -> bool:
    if not isinstance(
        value,
        bool,
    ):
        raise ValueError(
            f"{name} must be a boolean."
        )

    return value

def _optional_string(
    value: object,
    *,
    name: str,
) -> str | None:
    if value is None:
        return None

    return _required_string(
        value,
        name=name,
    )


def _validate_case_pairs(
    cases: Sequence[BenchmarkCase],
) -> None:
    pairs: dict[
        str,
        list[BenchmarkCase],
    ] = {}

    for case in cases:
        if case.pair_id is None:
            continue

        pairs.setdefault(
            case.pair_id,
            [],
        ).append(
            case
        )

    for pair_id, pair_cases in pairs.items():
        if len(pair_cases) != 2:
            raise ValueError(
                "Benchmark pair must contain exactly "
                f"two cases: {pair_id}."
            )

        splits = {
            case.split
            for case in pair_cases
        }

        if len(splits) != 1:
            raise ValueError(
                "Benchmark pair cannot cross splits: "
                f"{pair_id}."
            )

        domains = {
            case.domain
            for case in pair_cases
        }

        if len(domains) != 1:
            raise ValueError(
                "Benchmark pair must use one domain: "
                f"{pair_id}."
            )

        labels = sorted(
            case.consequential_change
            for case in pair_cases
        )

        if labels != [
            False,
            True,
        ]:
            raise ValueError(
                "Benchmark pair must contain one benign "
                "control and one consequential mutation: "
                f"{pair_id}."
            )

        before_values = {
            case.before
            for case in pair_cases
        }

        if len(before_values) != 1:
            raise ValueError(
                "Benchmark pair must share the same "
                f"before text: {pair_id}."
            )
