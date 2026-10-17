from __future__ import annotations

import json

from pathlib import Path

import pytest

from benchmarks.temporal_knowledge_integrity.metrics import (
    calculate_binary_metrics,
    calculate_critical_change_recall,
    calculate_paired_discrimination,
)
from benchmarks.temporal_knowledge_integrity.runner import (
    BenchmarkCase,
    ScoredCase,
    calibrate_threshold,
    cases_for_split,
    evaluate_threshold,
    lexical_change_score,
    load_cases,
    normalized_text_change_score,
    token_change_score,
)


def test_binary_metrics_calculate_confusion_matrix_and_rates() -> None:
    result = calculate_binary_metrics(
        labels=[
            True,
            True,
            False,
            False,
        ],
        predictions=[
            True,
            False,
            True,
            False,
        ],
    )

    assert result.true_positive == 1
    assert result.false_positive == 1
    assert result.true_negative == 1
    assert result.false_negative == 1

    assert result.precision == pytest.approx(
        0.5
    )
    assert result.recall == pytest.approx(
        0.5
    )
    assert result.f1 == pytest.approx(
        0.5
    )
    assert result.false_positive_rate == pytest.approx(
        0.5
    )


def test_binary_metrics_reject_mismatched_lengths() -> None:
    with pytest.raises(
        ValueError,
        match=(
            "labels and predictions must contain "
            "the same number of values"
        ),
    ):
        calculate_binary_metrics(
            labels=[
                True,
            ],
            predictions=[
                True,
                False,
            ],
        )


def test_binary_metrics_reject_empty_input() -> None:
    with pytest.raises(
        ValueError,
        match=(
            "labels and predictions must not be empty"
        ),
    ):
        calculate_binary_metrics(
            labels=[],
            predictions=[],
        )


def test_critical_change_recall_counts_only_critical_cases() -> None:
    recall = calculate_critical_change_recall(
        critical_labels=[
            True,
            True,
            False,
            True,
        ],
        predictions=[
            True,
            False,
            True,
            True,
        ],
    )

    assert recall == pytest.approx(
        2.0 / 3.0
    )


def test_critical_change_recall_returns_zero_without_critical_cases() -> None:
    recall = calculate_critical_change_recall(
        critical_labels=[
            False,
            False,
        ],
        predictions=[
            True,
            False,
        ],
    )

    assert recall == 0.0


def test_critical_change_recall_rejects_mismatched_lengths() -> None:
    with pytest.raises(
        ValueError,
        match=(
            "critical_labels and predictions must contain "
            "the same number of values"
        ),
    ):
        calculate_critical_change_recall(
            critical_labels=[
                True,
            ],
            predictions=[
                True,
                False,
            ],
        )

CASES_PATH = (
    Path(__file__).resolve().parents[2]
    / "benchmarks"
    / "temporal_knowledge_integrity"
    / "cases.json"
)


def test_benchmark_cases_load_with_distinct_splits() -> None:
    cases = load_cases(
        CASES_PATH
    )

    calibration_cases = cases_for_split(
        cases,
        split="calibration",
    )
    evaluation_cases = cases_for_split(
        cases,
        split="evaluation",
    )

    assert len(cases) == 40
    assert len(calibration_cases) == 20
    assert len(evaluation_cases) == 20

    assert {
        case.case_id
        for case in calibration_cases
    }.isdisjoint(
        {
            case.case_id
            for case in evaluation_cases
        }
    )


def test_normalized_text_change_ignores_case_and_whitespace() -> None:
    score = normalized_text_change_score(
        "Audit   Records ARE retained.",
        " audit records are retained. ",
    )

    assert score == 0.0


def test_lexical_change_score_is_bounded() -> None:
    score = lexical_change_score(
        "Refunds are available within 30 days.",
        "Refunds are available within 3 days.",
    )

    assert 0.0 < score < 1.0


def test_token_change_score_detects_token_difference() -> None:
    score = token_change_score(
        "External contractors may access production logs.",
        "External contractors may not access production logs.",
    )

    assert 0.0 < score < 1.0


def test_threshold_calibration_respects_false_positive_constraint() -> None:
    scored_cases = (
        _scored_case(
            case_id="negative-low",
            consequential_change=False,
            critical_change=False,
            score=0.10,
        ),
        _scored_case(
            case_id="negative-high",
            consequential_change=False,
            critical_change=False,
            score=0.40,
        ),
        _scored_case(
            case_id="positive",
            consequential_change=True,
            critical_change=True,
            score=0.70,
        ),
    )

    result = calibrate_threshold(
        scored_cases,
        maximum_false_positive_rate=0.0,
    )

    assert result.threshold > 0.40
    assert result.metrics.false_positive_rate == 0.0
    assert result.critical_change_recall == 1.0


def test_evaluation_uses_supplied_frozen_threshold() -> None:
    scored_cases = (
        _scored_case(
            case_id="negative",
            consequential_change=False,
            critical_change=False,
            score=0.20,
        ),
        _scored_case(
            case_id="critical",
            consequential_change=True,
            critical_change=True,
            score=0.80,
        ),
    )

    result = evaluate_threshold(
        scored_cases,
        threshold=0.50,
    )

    assert result.threshold == 0.50
    assert result.metrics.true_positive == 1
    assert result.metrics.true_negative == 1
    assert result.metrics.false_positive == 0
    assert result.metrics.false_negative == 0
    assert result.critical_change_recall == 1.0

def test_threshold_calibration_can_reject_all_tied_maximum_scores() -> None:
    scored_cases = (
        _scored_case(
            case_id="negative",
            consequential_change=False,
            critical_change=False,
            score=1.0,
        ),
        _scored_case(
            case_id="positive",
            consequential_change=True,
            critical_change=True,
            score=1.0,
        ),
    )

    result = calibrate_threshold(
        scored_cases,
        maximum_false_positive_rate=0.0,
    )

    assert result.threshold > 1.0
    assert result.metrics.false_positive_rate == 0.0
    assert result.metrics.true_positive == 0
    assert result.metrics.false_positive == 0
    assert result.metrics.true_negative == 1
    assert result.metrics.false_negative == 1
    assert result.critical_change_recall == 0.0

def test_benchmark_cases_include_schema_metadata() -> None:
    cases = load_cases(
        CASES_PATH
    )

    assert all(
        case.domain
        for case in cases
    )
    assert all(
        case.difficulty
        in {
            "easy",
            "medium",
            "hard",
        }
        for case in cases
    )
    assert all(
        case.pair_id is not None
        for case in cases
    )


def test_benchmark_cases_reject_invalid_difficulty(
    tmp_path: Path,
) -> None:
    path = _write_case_payload(
        tmp_path,
        [
            _case_payload(
                case_id="invalid-difficulty",
                difficulty="extreme",
            )
        ],
    )

    with pytest.raises(
        ValueError,
        match="Unsupported benchmark difficulty",
    ):
        load_cases(
            path
        )


def test_benchmark_cases_accept_valid_pair(
    tmp_path: Path,
) -> None:
    path = _write_case_payload(
        tmp_path,
        [
            _case_payload(
                case_id="control",
                pair_id="pair-001",
                consequential_change=False,
                critical_change=False,
                after="Customers may request refunds within 30 days.",
            ),
            _case_payload(
                case_id="mutation",
                pair_id="pair-001",
                consequential_change=True,
                critical_change=True,
                after="Refunds are available within 3 days.",
            ),
        ],
    )

    cases = load_cases(
        path
    )

    assert len(cases) == 2
    assert {
        case.pair_id
        for case in cases
    } == {
        "pair-001",
    }


def test_benchmark_cases_reject_incomplete_pair(
    tmp_path: Path,
) -> None:
    path = _write_case_payload(
        tmp_path,
        [
            _case_payload(
                case_id="control",
                pair_id="pair-001",
            )
        ],
    )

    with pytest.raises(
        ValueError,
        match="must contain exactly two cases",
    ):
        load_cases(
            path
        )


def test_benchmark_cases_reject_pair_with_more_than_two_cases(
    tmp_path: Path,
) -> None:
    path = _write_case_payload(
        tmp_path,
        [
            _case_payload(
                case_id="control-a",
                pair_id="pair-001",
            ),
            _case_payload(
                case_id="control-b",
                pair_id="pair-001",
            ),
            _case_payload(
                case_id="mutation",
                pair_id="pair-001",
                consequential_change=True,
                critical_change=True,
            ),
        ],
    )

    with pytest.raises(
        ValueError,
        match="must contain exactly two cases",
    ):
        load_cases(
            path
        )


def test_benchmark_cases_reject_pair_crossing_splits(
    tmp_path: Path,
) -> None:
    path = _write_case_payload(
        tmp_path,
        [
            _case_payload(
                case_id="control",
                pair_id="pair-001",
                split="calibration",
            ),
            _case_payload(
                case_id="mutation",
                pair_id="pair-001",
                split="evaluation",
                consequential_change=True,
                critical_change=True,
            ),
        ],
    )

    with pytest.raises(
        ValueError,
        match="cannot cross splits",
    ):
        load_cases(
            path
        )


def test_benchmark_cases_reject_pair_with_different_before_text(
    tmp_path: Path,
) -> None:
    path = _write_case_payload(
        tmp_path,
        [
            _case_payload(
                case_id="control",
                pair_id="pair-001",
                before="Refunds are available within 30 days.",
            ),
            _case_payload(
                case_id="mutation",
                pair_id="pair-001",
                before="Refunds are available within 14 days.",
                consequential_change=True,
                critical_change=True,
            ),
        ],
    )

    with pytest.raises(
        ValueError,
        match="must share the same before text",
    ):
        load_cases(
            path
        )


def test_benchmark_cases_reject_pair_without_opposing_labels(
    tmp_path: Path,
) -> None:
    path = _write_case_payload(
        tmp_path,
        [
            _case_payload(
                case_id="control-a",
                pair_id="pair-001",
            ),
            _case_payload(
                case_id="control-b",
                pair_id="pair-001",
            ),
        ],
    )

    with pytest.raises(
        ValueError,
        match=(
            "must contain one benign control and one "
            "consequential mutation"
        ),
    ):
        load_cases(
            path
        )


def test_paired_discrimination_counts_rankings() -> None:
    result = calculate_paired_discrimination(
        [
            (
                "correct",
                False,
                0.2,
            ),
            (
                "correct",
                True,
                0.8,
            ),
            (
                "tied",
                False,
                0.4,
            ),
            (
                "tied",
                True,
                0.4,
            ),
            (
                "incorrect",
                False,
                0.9,
            ),
            (
                "incorrect",
                True,
                0.1,
            ),
        ]
    )

    assert result.pair_count == 3
    assert result.correctly_ranked == 1
    assert result.tied == 1
    assert result.incorrectly_ranked == 1
    assert result.accuracy == pytest.approx(
        1.0 / 3.0
    )


def test_paired_discrimination_returns_zero_without_pairs() -> None:
    result = calculate_paired_discrimination(
        [
            (
                None,
                False,
                0.1,
            ),
            (
                None,
                True,
                0.9,
            ),
        ]
    )

    assert result.pair_count == 0
    assert result.correctly_ranked == 0
    assert result.tied == 0
    assert result.incorrectly_ranked == 0
    assert result.accuracy == 0.0

def _write_case_payload(
    tmp_path: Path,
    cases: list[dict[str, object]],
) -> Path:
    path = (
        tmp_path
        / "cases.json"
    )
    path.write_text(
        json.dumps(
            cases
        ),
        encoding="utf-8",
    )

    return path


def _case_payload(
    *,
    case_id: str,
    pair_id: str | None = None,
    split: str = "calibration",
    category: str = "test",
    domain: str = "test",
    difficulty: str = "medium",
    before: str = "Refunds are available within 30 days.",
    after: str = "Refunds remain available within 30 days.",
    consequential_change: bool = False,
    critical_change: bool = False,
) -> dict[str, object]:
    return {
        "id": case_id,
        "pair_id": pair_id,
        "split": split,
        "category": category,
        "domain": domain,
        "difficulty": difficulty,
        "before": before,
        "after": after,
        "consequential_change": consequential_change,
        "critical_change": critical_change,
    }

def _scored_case(
    *,
    case_id: str,
    consequential_change: bool,
    critical_change: bool,
    score: float,
) -> ScoredCase:
    return ScoredCase(
        case=BenchmarkCase(
            case_id=case_id,
            pair_id=None,
            split="calibration",
            category="test",
            domain="test",
            difficulty="medium",
            before="before",
            after="after",
            consequential_change=consequential_change,
            critical_change=critical_change,
        ),
        score=score,
    )

def test_benchmark_corpus_has_balanced_paired_families() -> None:
    cases = load_cases(
        CASES_PATH
    )

    expected_categories = {
        "numeric_critical_change",
        "negation",
        "threshold_change",
        "restriction_deletion",
        "authority_change",
    }

    assert {
        case.category
        for case in cases
    } == expected_categories

    for split in (
        "calibration",
        "evaluation",
    ):
        split_cases = cases_for_split(
            cases,
            split=split,
        )

        assert sum(
            case.consequential_change
            for case in split_cases
        ) == 10
        assert sum(
            not case.consequential_change
            for case in split_cases
        ) == 10
        assert sum(
            case.critical_change
            for case in split_cases
        ) == 10

        for category in expected_categories:
            category_cases = [
                case
                for case in split_cases
                if case.category == category
            ]

            assert len(category_cases) == 4
            assert sum(
                case.consequential_change
                for case in category_cases
            ) == 2
            assert sum(
                not case.consequential_change
                for case in category_cases
            ) == 2