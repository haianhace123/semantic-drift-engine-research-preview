from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Sequence

if TYPE_CHECKING:
    from benchmarks.temporal_knowledge_integrity.embedding_adapter import (
        ProductionEmbeddingAdapter,
    )
from benchmarks.temporal_knowledge_integrity.runner import (
    BenchmarkCase,
    ScoredCase,
    ThresholdEvaluation,
    calibrate_threshold,
    cases_for_split,
    evaluate_threshold,
    lexical_change_score,
    load_cases,
    normalized_text_change_score,
    score_cases,
    token_change_score,
)
from benchmarks.temporal_knowledge_integrity.metrics import (
    PairedDiscriminationMetrics,
    calculate_paired_discrimination,
)

DEFAULT_CASES_PATH = (
    Path(__file__).resolve().parent
    / "cases.json"
)

DEFAULT_MAXIMUM_FALSE_POSITIVE_RATE = 0.0


@dataclass(frozen=True)
class BenchmarkMethodResult:
    method: str
    calibration: ThresholdEvaluation
    evaluation: ThresholdEvaluation
    calibration_scores: tuple[ScoredCase, ...]
    evaluation_scores: tuple[ScoredCase, ...]
    calibration_paired_discrimination: PairedDiscriminationMetrics
    evaluation_paired_discrimination: PairedDiscriminationMetrics


class EmbeddingScoreCache:
    def __init__(
        self,
        adapter: ProductionEmbeddingAdapter,
    ) -> None:
        self._adapter = adapter
        self._embeddings: dict[
            str,
            tuple[float, ...],
        ] = {}

    def score(
        self,
        before: str,
        after: str,
    ) -> float:
        before_embedding = self._embedding(
            before
        )
        after_embedding = self._embedding(
            after
        )

        result = self._adapter.score_embeddings(
            before_embedding,
            after_embedding,
        )

        return result.semantic_magnitude

    def _embedding(
        self,
        text: str,
    ) -> tuple[float, ...]:
        cached = self._embeddings.get(
            text
        )

        if cached is not None:
            return cached

        embedding = self._adapter.embed_text(
            text
        )
        self._embeddings[text] = embedding

        return embedding


def evaluate_method(
    *,
    method: str,
    calibration_cases: Sequence[BenchmarkCase],
    evaluation_cases: Sequence[BenchmarkCase],
    scorer: Callable[[str, str], float],
    maximum_false_positive_rate: float,
) -> BenchmarkMethodResult:
    calibration_scores = score_cases(
        calibration_cases,
        scorer=scorer,
    )

    calibration_result = calibrate_threshold(
        calibration_scores,
        maximum_false_positive_rate=(
            maximum_false_positive_rate
        ),
    )

    evaluation_scores = score_cases(
        evaluation_cases,
        scorer=scorer,
    )

    evaluation_result = evaluate_threshold(
        evaluation_scores,
        threshold=calibration_result.threshold,
    )

    calibration_paired_discrimination = (
        calculate_paired_discrimination(
            [
                (
                    scored_case.case.pair_id,
                    scored_case.case.consequential_change,
                    scored_case.score,
                )
                for scored_case in calibration_scores
            ]
        )
    )
    evaluation_paired_discrimination = (
        calculate_paired_discrimination(
            [
                (
                    scored_case.case.pair_id,
                    scored_case.case.consequential_change,
                    scored_case.score,
                )
                for scored_case in evaluation_scores
            ]
        )
    )

    return BenchmarkMethodResult(
        method=method,
        calibration=calibration_result,
        evaluation=evaluation_result,
        calibration_scores=calibration_scores,
        evaluation_scores=evaluation_scores,
        calibration_paired_discrimination=(
            calibration_paired_discrimination
        ),
        evaluation_paired_discrimination=(
            evaluation_paired_discrimination
        ),
    )


def run_benchmark(
    *,
    cases_path: Path,
    maximum_false_positive_rate: float,
    include_embedding: bool,
) -> tuple[BenchmarkMethodResult, ...]:
    cases = load_cases(
        cases_path
    )

    calibration_cases = cases_for_split(
        cases,
        split="calibration",
    )
    evaluation_cases = cases_for_split(
        cases,
        split="evaluation",
    )

    if not calibration_cases:
        raise ValueError(
            "Benchmark requires calibration cases."
        )

    if not evaluation_cases:
        raise ValueError(
            "Benchmark requires evaluation cases."
        )

    methods: list[
        tuple[
            str,
            Callable[[str, str], float],
        ]
    ] = [
        (
            "normalized_text_change",
            normalized_text_change_score,
        ),
        (
            "lexical_change",
            lexical_change_score,
        ),
        (
            "token_change",
            token_change_score,
        ),
    ]

    embedding_adapter = None

    if include_embedding:
        # No provider SDK is needed for the deterministic offline baselines.
        from benchmarks.temporal_knowledge_integrity.embedding_adapter import (
            ProductionEmbeddingAdapter,
        )

        embedding_adapter = ProductionEmbeddingAdapter()
        embedding_cache = EmbeddingScoreCache(
            embedding_adapter
        )
        methods.append(
            (
                "semantic_magnitude",
                embedding_cache.score,
            )
        )

    results = tuple(
        evaluate_method(
            method=method,
            calibration_cases=calibration_cases,
            evaluation_cases=evaluation_cases,
            scorer=scorer,
            maximum_false_positive_rate=(
                maximum_false_positive_rate
            ),
        )
        for method, scorer in methods
    )

    if embedding_adapter is not None:
        metadata = embedding_adapter.metadata
        print(
            "Embedding: "
            f"provider={metadata.provider} "
            f"model={metadata.model} "
            f"dimensions={metadata.dimensions}"
        )
        print()

    return results


def print_results(
    results: Sequence[BenchmarkMethodResult],
) -> None:
    print(
        "Temporal Knowledge Integrity Benchmark v0"
    )
    print(
        "=" * 41
    )

    for result in results:
        metrics = result.evaluation.metrics

        print()
        print(
            f"Method: {result.method}"
        )
        print(
            "  calibration_threshold="
            f"{result.calibration.threshold:.17g}"
        )
        print(
            "  evaluation_precision="
            f"{metrics.precision:.4f}"
        )
        print(
            "  evaluation_recall="
            f"{metrics.recall:.4f}"
        )
        print(
            "  evaluation_f1="
            f"{metrics.f1:.4f}"
        )
        print(
            "  evaluation_fpr="
            f"{metrics.false_positive_rate:.4f}"
        )
        print(
            "  critical_change_recall="
            f"{result.evaluation.critical_change_recall:.4f}"
        )

        calibration_paired = (
            result.calibration_paired_discrimination
        )
        evaluation_paired = (
            result.evaluation_paired_discrimination
        )

        print(
            "  calibration_paired_discrimination="
            f"{calibration_paired.accuracy:.4f}"
        )
        print(
            "  calibration_paired_cases="
            f"{calibration_paired.pair_count}"
        )
        print(
            "  evaluation_paired_discrimination="
            f"{evaluation_paired.accuracy:.4f}"
        )
        print(
            "  evaluation_paired_cases="
            f"{evaluation_paired.pair_count}"
        )
        print(
            "  evaluation_paired_correct="
            f"{evaluation_paired.correctly_ranked}"
        )
        print(
            "  evaluation_paired_tied="
            f"{evaluation_paired.tied}"
        )
        print(
            "  evaluation_paired_incorrect="
            f"{evaluation_paired.incorrectly_ranked}"
        )

        print(
            "  cases:"
        )

        for scored_case in (
            result.evaluation_scores
        ):
            predicted = (
                scored_case.score
                >= result.calibration.threshold
            )

            print(
                "    "
                f"{scored_case.case.case_id}: "
                f"score={scored_case.score:.6f} "
                f"expected="
                f"{int(scored_case.case.consequential_change)} "
                f"predicted={int(predicted)} "
                f"critical="
                f"{int(scored_case.case.critical_change)}"
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the Temporal Knowledge Integrity "
            "Benchmark v0."
        )
    )
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASES_PATH,
    )
    parser.add_argument(
        "--maximum-false-positive-rate",
        type=float,
        default=(
            DEFAULT_MAXIMUM_FALSE_POSITIVE_RATE
        ),
    )
    parser.add_argument(
        "--include-embedding",
        action="store_true",
        help=(
            "Run the configured production embedding "
            "provider in addition to lexical baselines."
        ),
    )

    return parser.parse_args()


def main() -> None:
    args = parse_args()

    results = run_benchmark(
        cases_path=args.cases,
        maximum_false_positive_rate=(
            args.maximum_false_positive_rate
        ),
        include_embedding=args.include_embedding,
    )

    print_results(
        results
    )


if __name__ == "__main__":
    main()