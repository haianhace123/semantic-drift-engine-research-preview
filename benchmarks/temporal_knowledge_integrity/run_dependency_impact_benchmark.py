from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from benchmarks.temporal_knowledge_integrity.dependency_impact import (
    ImpactAnalysis,
    analyze_dependency_impact,
)
from benchmarks.temporal_knowledge_integrity.dependency_impact_cases import (
    DependencyImpactCase,
    load_dependency_impact_cases,
)
from benchmarks.temporal_knowledge_integrity.impact_metrics import (
    AggregateDependencyImpactMetrics,
    DependencyImpactMetrics,
    aggregate_dependency_impact_metrics,
    calculate_dependency_impact_metrics,
)


@dataclass(frozen=True)
class ImpactPrediction:
    affected_node_ids: tuple[str, ...]
    invalidation_candidate_ids: tuple[str, ...]
    critical_node_ids: tuple[str, ...]


@dataclass(frozen=True)
class DependencyImpactMethodResult:
    method_name: str
    metrics: AggregateDependencyImpactMetrics


ImpactScorer = Callable[
    [DependencyImpactCase],
    ImpactPrediction,
]


def _prediction_from_analysis(
    analysis: ImpactAnalysis,
) -> ImpactPrediction:
    return ImpactPrediction(
        affected_node_ids=(
            analysis.affected_node_ids
        ),
        invalidation_candidate_ids=(
            analysis.invalidation_candidate_ids
        ),
        critical_node_ids=(
            analysis.critical_node_ids
        ),
    )


def _direct_only(
    case: DependencyImpactCase,
) -> ImpactPrediction:
    graph = case.graph
    changed = set(case.changed_node_ids)

    nodes_by_id = {
        node.id: node
        for node in graph.nodes
    }

    affected = {
        edge.downstream_node_id
        for edge in graph.edges
        if edge.upstream_node_id in changed
        and edge.downstream_node_id not in changed
    }

    invalidation = {
        node_id
        for node_id in affected
        if nodes_by_id[node_id].materialized
    }

    critical = {
        node_id
        for node_id in affected
        if nodes_by_id[node_id].critical
    }

    return ImpactPrediction(
        affected_node_ids=tuple(
            sorted(affected)
        ),
        invalidation_candidate_ids=tuple(
            sorted(invalidation)
        ),
        critical_node_ids=tuple(
            sorted(critical)
        ),
    )


def _materialized_direct_only(
    case: DependencyImpactCase,
) -> ImpactPrediction:
    direct = _direct_only(case)

    nodes_by_id = {
        node.id: node
        for node in case.graph.nodes
    }

    materialized = tuple(
        node_id
        for node_id in direct.affected_node_ids
        if nodes_by_id[node_id].materialized
    )

    return ImpactPrediction(
        affected_node_ids=materialized,
        invalidation_candidate_ids=materialized,
        critical_node_ids=(),
    )


def _dependency_transitive(
    case: DependencyImpactCase,
) -> ImpactPrediction:
    return _prediction_from_analysis(
        analyze_dependency_impact(
            graph=case.graph,
            changed_node_ids=(
                case.changed_node_ids
            ),
        )
    )


def _evaluate_case(
    *,
    case: DependencyImpactCase,
    prediction: ImpactPrediction,
) -> DependencyImpactMetrics:
    return calculate_dependency_impact_metrics(
        expected_affected=(
            case.expected_affected_node_ids
        ),
        predicted_affected=(
            prediction.affected_node_ids
        ),
        expected_invalidation=(
            case.expected_invalidation_candidate_ids
        ),
        predicted_invalidation=(
            prediction.invalidation_candidate_ids
        ),
        expected_critical=(
            case.expected_critical_node_ids
        ),
        predicted_critical=(
            prediction.critical_node_ids
        ),
    )


def evaluate_method(
    *,
    method_name: str,
    cases: tuple[DependencyImpactCase, ...],
    scorer: ImpactScorer,
) -> DependencyImpactMethodResult:
    case_metrics = tuple(
        _evaluate_case(
            case=case,
            prediction=scorer(case),
        )
        for case in cases
    )

    return DependencyImpactMethodResult(
        method_name=method_name,
        metrics=(
            aggregate_dependency_impact_metrics(
                case_metrics
            )
        ),
    )


def run_benchmark() -> tuple[
    DependencyImpactMethodResult,
    ...,
]:
    cases = load_dependency_impact_cases()

    return (
        evaluate_method(
            method_name="direct_only",
            cases=cases,
            scorer=_direct_only,
        ),
        evaluate_method(
            method_name=(
                "materialized_direct_only"
            ),
            cases=cases,
            scorer=_materialized_direct_only,
        ),
        evaluate_method(
            method_name=(
                "dependency_transitive"
            ),
            cases=cases,
            scorer=_dependency_transitive,
        ),
    )


def _print_dimension(
    *,
    name: str,
    metrics: object,
) -> None:
    print(
        f"  {name}:"
        f" precision={metrics.precision:.4f}"
        f" recall={metrics.recall:.4f}"
        f" f1={metrics.f1:.4f}"
        f" exact={metrics.exact_match_rate:.4f}"
    )


def print_results(
    results: tuple[
        DependencyImpactMethodResult,
        ...,
    ],
) -> None:
    print(
        "Temporal Knowledge Integrity "
        "Dependency Impact Benchmark v0"
    )
    print("=" * 58)

    for result in results:
        print()
        print(
            f"Method: {result.method_name}"
        )

        _print_dimension(
            name="affected",
            metrics=result.metrics.affected,
        )
        _print_dimension(
            name="invalidation",
            metrics=result.metrics.invalidation,
        )
        _print_dimension(
            name="critical",
            metrics=result.metrics.critical,
        )


def main() -> None:
    print_results(
        run_benchmark()
    )


if __name__ == "__main__":
    main()