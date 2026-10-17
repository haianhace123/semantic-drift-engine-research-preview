from __future__ import annotations

import uuid
from dataclasses import dataclass

from benchmarks.temporal_knowledge_integrity.metrics import (
    PairedDiscriminationMetrics,
    calculate_paired_discrimination,
)
from benchmarks.temporal_knowledge_integrity.runner import (
    BenchmarkCase,
    ScoredCase,
    ThresholdEvaluation,
    calibrate_threshold,
    evaluate_threshold,
)
from benchmarks.temporal_knowledge_integrity.topology_adapter import (
    ProductionTopologyAdapter,
    graph_state,
)
from benchmarks.temporal_knowledge_integrity.topology_cases import (
    NamedGraphState,
    TopologyBenchmarkCase,
    load_topology_cases,
)


@dataclass(frozen=True)
class TopologyBenchmarkResult:
    calibration: ThresholdEvaluation
    evaluation: ThresholdEvaluation
    calibration_scores: tuple[ScoredCase, ...]
    evaluation_scores: tuple[ScoredCase, ...]
    calibration_paired_discrimination: PairedDiscriminationMetrics
    evaluation_paired_discrimination: PairedDiscriminationMetrics


def _entity_id(
    *,
    organization_id: uuid.UUID,
    entity_name: str,
) -> uuid.UUID:
    return uuid.uuid5(
        organization_id,
        entity_name,
    )


def _materialize_graph_state(
    *,
    organization_id: uuid.UUID,
    state: NamedGraphState,
):
    entity_ids = {
        entity_name: _entity_id(
            organization_id=organization_id,
            entity_name=entity_name,
        )
        for entity_name in state.entities
    }

    return graph_state(
        entity_ids=(
            entity_ids[entity_name]
            for entity_name in state.entities
        ),
        edges=(
            (
                entity_ids[source],
                entity_ids[target],
            )
            for source, target in state.edges
        ),
    )


def _benchmark_case(
    case: TopologyBenchmarkCase,
) -> BenchmarkCase:
    return BenchmarkCase(
        case_id=case.id,
        pair_id=case.pair_id,
        split=case.split,
        category=case.category,
        domain=case.domain,
        difficulty=case.difficulty,
        before="",
        after="",
        consequential_change=case.consequential_change,
        critical_change=case.critical_change,
    )


def _paired_metrics(
    scored_cases: tuple[ScoredCase, ...],
) -> PairedDiscriminationMetrics:
    return calculate_paired_discrimination(
        tuple(
            (
                scored_case.case.pair_id,
                scored_case.case.consequential_change,
                scored_case.score,
            )
            for scored_case in scored_cases
        )
    )


def score_topology_cases(
    cases: tuple[TopologyBenchmarkCase, ...],
    *,
    adapter: ProductionTopologyAdapter | None = None,
) -> tuple[ScoredCase, ...]:
    topology_adapter = (
        adapter
        if adapter is not None
        else ProductionTopologyAdapter()
    )

    scored_cases: list[ScoredCase] = []

    for case in cases:
        organization_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"sde-topology-benchmark:{case.id}",
        )

        previous = _materialize_graph_state(
            organization_id=organization_id,
            state=case.before,
        )
        current = _materialize_graph_state(
            organization_id=organization_id,
            state=case.after,
        )

        result = topology_adapter.score(
            organization_id=organization_id,
            previous=previous,
            current=current,
        )

        scored_cases.append(
            ScoredCase(
                case=_benchmark_case(case),
                score=result.score,
            )
        )

    return tuple(scored_cases)


def run_topology_benchmark(
    *,
    maximum_false_positive_rate: float = 0.0,
) -> TopologyBenchmarkResult:
    cases = load_topology_cases()

    calibration_cases = tuple(
        case
        for case in cases
        if case.split == "calibration"
    )
    evaluation_cases = tuple(
        case
        for case in cases
        if case.split == "evaluation"
    )

    calibration_scores = score_topology_cases(
        calibration_cases
    )
    evaluation_scores = score_topology_cases(
        evaluation_cases
    )

    calibration = calibrate_threshold(
        calibration_scores,
        maximum_false_positive_rate=(
            maximum_false_positive_rate
        ),
    )
    evaluation = evaluate_threshold(
        evaluation_scores,
        threshold=calibration.threshold,
    )

    return TopologyBenchmarkResult(
        calibration=calibration,
        evaluation=evaluation,
        calibration_scores=calibration_scores,
        evaluation_scores=evaluation_scores,
        calibration_paired_discrimination=_paired_metrics(
            calibration_scores
        ),
        evaluation_paired_discrimination=_paired_metrics(
            evaluation_scores
        ),
    )


def print_topology_results(
    result: TopologyBenchmarkResult,
) -> None:
    evaluation_metrics = result.evaluation.metrics
    calibration_pairs = (
        result.calibration_paired_discrimination
    )
    evaluation_pairs = (
        result.evaluation_paired_discrimination
    )

    print(
        "Temporal Knowledge Integrity "
        "Topology Benchmark v0"
    )
    print("=" * 49)
    print()
    print("Method: topology_shift")
    print(
        "  calibration_threshold="
        f"{result.calibration.threshold:.17g}"
    )
    print(
        "  evaluation_precision="
        f"{evaluation_metrics.precision:.4f}"
    )
    print(
        "  evaluation_recall="
        f"{evaluation_metrics.recall:.4f}"
    )
    print(
        "  evaluation_f1="
        f"{evaluation_metrics.f1:.4f}"
    )
    print(
        "  evaluation_fpr="
        f"{evaluation_metrics.false_positive_rate:.4f}"
    )
    print(
        "  critical_change_recall="
        f"{result.evaluation.critical_change_recall:.4f}"
    )
    print(
        "  calibration_paired_discrimination="
        f"{calibration_pairs.accuracy:.4f}"
    )
    print(
        "  calibration_paired_cases="
        f"{calibration_pairs.pair_count}"
    )
    print(
        "  evaluation_paired_discrimination="
        f"{evaluation_pairs.accuracy:.4f}"
    )
    print(
        "  evaluation_paired_cases="
        f"{evaluation_pairs.pair_count}"
    )
    print(
        "  evaluation_paired_correct="
        f"{evaluation_pairs.correctly_ranked}"
    )
    print(
        "  evaluation_paired_tied="
        f"{evaluation_pairs.tied}"
    )
    print(
        "  evaluation_paired_incorrect="
        f"{evaluation_pairs.incorrectly_ranked}"
    )
    print("  cases:")

    for scored_case in result.evaluation_scores:
        predicted = (
            scored_case.score
            >= result.calibration.threshold
        )

        print(
            f"    {scored_case.case.case_id}: "
            f"score={scored_case.score:.6f} "
            f"expected="
            f"{int(scored_case.case.consequential_change)} "
            f"predicted={int(predicted)} "
            f"critical="
            f"{int(scored_case.case.critical_change)}"
        )


def main() -> None:
    result = run_topology_benchmark()
    print_topology_results(result)


if __name__ == "__main__":
    main()