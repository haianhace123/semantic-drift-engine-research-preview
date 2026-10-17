from __future__ import annotations

from benchmarks.temporal_knowledge_integrity.embedding_adapter import (
    ProductionEmbeddingAdapter,
)
from benchmarks.temporal_knowledge_integrity.run_benchmark import (
    EmbeddingScoreCache,
    evaluate_method,
)
from benchmarks.temporal_knowledge_integrity.runner import (
    BenchmarkCase,
)


class FakeEmbeddingService:
    provider = "test"
    model = "test"
    dimensions = 2

    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate_embedding(
        self,
        text: str,
    ) -> list[float]:
        self.calls.append(
            text
        )

        embeddings = {
            "shared": [
                1.0,
                0.0,
            ],
            "first": [
                0.0,
                1.0,
            ],
            "second": [
                -1.0,
                0.0,
            ],
        }

        return embeddings[text]


def test_embedding_score_cache_reuses_text_embeddings() -> None:
    service = FakeEmbeddingService()
    adapter = ProductionEmbeddingAdapter(
        service=service
    )
    cache = EmbeddingScoreCache(
        adapter
    )

    cache.score(
        "shared",
        "first",
    )
    cache.score(
        "shared",
        "second",
    )

    assert service.calls == [
        "shared",
        "first",
        "second",
    ]


def test_evaluate_method_freezes_calibration_threshold() -> None:
    calibration_cases = (
        _case(
            case_id="calibration-negative",
            split="calibration",
            before="same",
            after="same",
            consequential_change=False,
            critical_change=False,
        ),
        _case(
            case_id="calibration-positive",
            split="calibration",
            before="same",
            after="changed",
            consequential_change=True,
            critical_change=True,
        ),
    )

    evaluation_cases = (
        _case(
            case_id="evaluation-negative",
            split="evaluation",
            before="same",
            after="same",
            consequential_change=False,
            critical_change=False,
        ),
        _case(
            case_id="evaluation-positive",
            split="evaluation",
            before="same",
            after="changed",
            consequential_change=True,
            critical_change=True,
        ),
    )

    def scorer(
        before: str,
        after: str,
    ) -> float:
        return float(
            before != after
        )

    result = evaluate_method(
        method="test",
        calibration_cases=calibration_cases,
        evaluation_cases=evaluation_cases,
        scorer=scorer,
        maximum_false_positive_rate=0.0,
    )

    assert result.calibration.threshold == 1.0
    assert result.evaluation.threshold == 1.0
    assert result.evaluation.metrics.true_positive == 1
    assert result.evaluation.metrics.true_negative == 1
    assert result.calibration_scores
    assert result.evaluation_scores
    assert (
        result.calibration_paired_discrimination.pair_count
        == 0
    )
    assert (
        result.evaluation_paired_discrimination.pair_count
        == 0
    )

def test_evaluate_method_reports_paired_discrimination() -> None:
    calibration_cases = (
        _case(
            case_id="calibration-negative",
            split="calibration",
            before="same",
            after="same",
            consequential_change=False,
            critical_change=False,
        ),
        _case(
            case_id="calibration-positive",
            split="calibration",
            before="same",
            after="changed",
            consequential_change=True,
            critical_change=True,
        ),
    )

    evaluation_cases = (
        _case(
            case_id="evaluation-control",
            pair_id="pair-001",
            split="evaluation",
            before="shared",
            after="control",
            consequential_change=False,
            critical_change=False,
        ),
        _case(
            case_id="evaluation-mutation",
            pair_id="pair-001",
            split="evaluation",
            before="shared",
            after="mutation",
            consequential_change=True,
            critical_change=True,
        ),
    )

    scores = {
        (
            "shared",
            "control",
        ): 0.2,
        (
            "shared",
            "mutation",
        ): 0.8,
        (
            "same",
            "same",
        ): 0.0,
        (
            "same",
            "changed",
        ): 1.0,
    }

    def scorer(
        before: str,
        after: str,
    ) -> float:
        return scores[
            (
                before,
                after,
            )
        ]

    result = evaluate_method(
        method="test",
        calibration_cases=calibration_cases,
        evaluation_cases=evaluation_cases,
        scorer=scorer,
        maximum_false_positive_rate=0.0,
    )
    assert (
        result.calibration_paired_discrimination.pair_count
        == 0
    )
    paired = (
        result.evaluation_paired_discrimination
    )

    assert paired.pair_count == 1
    assert paired.correctly_ranked == 1
    assert paired.tied == 0
    assert paired.incorrectly_ranked == 0
    assert paired.accuracy == 1.0

def test_evaluate_method_reports_calibration_paired_discrimination() -> None:
    calibration_cases = (
        _case(
            case_id="calibration-control",
            pair_id="calibration-pair-001",
            split="calibration",
            before="shared",
            after="control",
            consequential_change=False,
            critical_change=False,
        ),
        _case(
            case_id="calibration-mutation",
            pair_id="calibration-pair-001",
            split="calibration",
            before="shared",
            after="mutation",
            consequential_change=True,
            critical_change=True,
        ),
    )

    evaluation_cases = (
        _case(
            case_id="evaluation-negative",
            split="evaluation",
            before="same",
            after="same",
            consequential_change=False,
            critical_change=False,
        ),
        _case(
            case_id="evaluation-positive",
            split="evaluation",
            before="same",
            after="changed",
            consequential_change=True,
            critical_change=True,
        ),
    )

    scores = {
        (
            "shared",
            "control",
        ): 0.2,
        (
            "shared",
            "mutation",
        ): 0.8,
        (
            "same",
            "same",
        ): 0.0,
        (
            "same",
            "changed",
        ): 1.0,
    }

    def scorer(
        before: str,
        after: str,
    ) -> float:
        return scores[
            (
                before,
                after,
            )
        ]

    result = evaluate_method(
        method="test",
        calibration_cases=calibration_cases,
        evaluation_cases=evaluation_cases,
        scorer=scorer,
        maximum_false_positive_rate=0.0,
    )

    calibration_paired = (
        result.calibration_paired_discrimination
    )

    assert calibration_paired.pair_count == 1
    assert calibration_paired.correctly_ranked == 1
    assert calibration_paired.tied == 0
    assert calibration_paired.incorrectly_ranked == 0
    assert calibration_paired.accuracy == 1.0
    assert (
        result.evaluation_paired_discrimination.pair_count
        == 0
    )

def _case(
    *,
    case_id: str,
    split: str,
    before: str,
    after: str,
    consequential_change: bool,
    critical_change: bool,
    pair_id: str | None = None,
) -> BenchmarkCase:
    return BenchmarkCase(
        case_id=case_id,
        pair_id=pair_id,
        split=split,
        category="test",
        domain="test",
        difficulty="medium",
        before=before,
        after=after,
        consequential_change=consequential_change,
        critical_change=critical_change,
    )