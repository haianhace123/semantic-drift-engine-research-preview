from __future__ import annotations

from dataclasses import dataclass

from benchmarks.temporal_knowledge_integrity.impact_metrics import (
    AggregateSetPredictionMetrics,
    SetPredictionMetrics,
    aggregate_set_prediction_metrics,
    calculate_set_prediction_metrics,
)
from benchmarks.temporal_knowledge_integrity.rag_invalidation_experiment import (
    DerivedAssetState,
    EvaluationQuestion,
    InvalidationExperimentCase,
    InvalidationStrategy,
    KnowledgeFact,
    StrategyResult,
    run_invalidation_strategy,
)


@dataclass(frozen=True)
class StrategyAggregateResult:
    strategy: InvalidationStrategy
    case_count: int
    stale_answer_count: int
    critical_stale_answer_count: int
    total_answer_count: int
    critical_answer_count: int
    reprocessing_units: int
    affected: AggregateSetPredictionMetrics

    @property
    def stale_answer_rate(self) -> float:
        if self.total_answer_count == 0:
            return 0.0
        return self.stale_answer_count / self.total_answer_count

    @property
    def critical_stale_answer_rate(self) -> float:
        if self.critical_answer_count == 0:
            return 0.0
        return (
            self.critical_stale_answer_count
            / self.critical_answer_count
        )


def build_rag_invalidation_corpus(
) -> tuple[InvalidationExperimentCase, ...]:
    return (
        _build_linear_case(
            case_id="complete-lineage",
            include_index_to_agent_edge=True,
            include_chunk_to_index_edge=True,
        ),
        _build_linear_case(
            case_id="missing-terminal-edge",
            include_index_to_agent_edge=False,
            include_chunk_to_index_edge=True,
        ),
        _build_linear_case(
            case_id="missing-middle-edge",
            include_index_to_agent_edge=True,
            include_chunk_to_index_edge=False,
        ),
        _build_fan_out_case(),
        _build_unrelated_branch_case(),
        _build_shared_dependency_case(),
    )


def run_rag_invalidation_corpus(
    *,
    cases: tuple[InvalidationExperimentCase, ...],
    strategy: InvalidationStrategy,
) -> StrategyAggregateResult:
    results = tuple(
        run_invalidation_strategy(
            case=case,
            strategy=strategy,
        )
        for case in cases
    )

    affected_metrics = tuple(
        _calculate_affected_metrics(
            case=case,
            result=result,
        )
        for case, result in zip(
            cases,
            results,
            strict=True,
        )
    )

    return StrategyAggregateResult(
        strategy=strategy,
        case_count=len(results),
        stale_answer_count=sum(
            result.stale_answer_count
            for result in results
        ),
        critical_stale_answer_count=sum(
            result.critical_stale_answer_count
            for result in results
        ),
        total_answer_count=sum(
            result.total_answer_count
            for result in results
        ),
        critical_answer_count=sum(
            result.critical_answer_count
            for result in results
        ),
        reprocessing_units=sum(
            result.reprocessing_units
            for result in results
        ),
        affected=aggregate_set_prediction_metrics(
            affected_metrics
        ),
    )


def _calculate_affected_metrics(
    *,
    case: InvalidationExperimentCase,
    result: StrategyResult,
) -> SetPredictionMetrics:
    return calculate_set_prediction_metrics(
        expected=case.expected_affected_asset_ids,
        predicted=result.invalidated_asset_ids,
    )


def _build_linear_case(
    *,
    case_id: str,
    include_index_to_agent_edge: bool,
    include_chunk_to_index_edge: bool,
) -> InvalidationExperimentCase:
    return InvalidationExperimentCase(
        case_id=case_id,
        assets=(
            DerivedAssetState(
                asset_external_id=f"{case_id}:chunk",
                revision_external_id=f"{case_id}:chunk@v1",
                fact_id=case_id,
                materialized_value="old",
            ),
            DerivedAssetState(
                asset_external_id=f"{case_id}:index",
                revision_external_id=f"{case_id}:index@v1",
                fact_id=case_id,
                materialized_value="old",
                upstream_asset_ids=(
                    (f"{case_id}:chunk",)
                    if include_chunk_to_index_edge
                    else ()
                ),
            ),
            DerivedAssetState(
                asset_external_id=f"{case_id}:agent",
                revision_external_id=f"{case_id}:agent@v1",
                fact_id=case_id,
                materialized_value="old",
                critical=True,
                upstream_asset_ids=(
                    (f"{case_id}:index",)
                    if include_index_to_agent_edge
                    else ()
                ),
            ),
        ),
        current_facts=(
            KnowledgeFact(
                fact_id=case_id,
                value="new",
                critical=True,
            ),
        ),
        questions=(
            EvaluationQuestion(
                question_id=f"{case_id}:question",
                fact_id=case_id,
                expected_value="new",
                critical=True,
            ),
        ),
        answer_asset_ids_by_fact={
            case_id: f"{case_id}:agent",
        },
        directly_affected_asset_ids=(
            f"{case_id}:chunk",
        ),
        expected_affected_asset_ids=(
            f"{case_id}:chunk",
            f"{case_id}:index",
            f"{case_id}:agent",
        ),
    )


def _build_fan_out_case() -> InvalidationExperimentCase:
    case_id = "fan-out"

    return InvalidationExperimentCase(
        case_id=case_id,
        assets=(
            DerivedAssetState(
                asset_external_id="fan-out:chunk",
                revision_external_id="fan-out:chunk@v1",
                fact_id=case_id,
                materialized_value="old",
            ),
            DerivedAssetState(
                asset_external_id="fan-out:index-a",
                revision_external_id="fan-out:index-a@v1",
                fact_id=case_id,
                materialized_value="old",
                upstream_asset_ids=("fan-out:chunk",),
            ),
            DerivedAssetState(
                asset_external_id="fan-out:index-b",
                revision_external_id="fan-out:index-b@v1",
                fact_id=case_id,
                materialized_value="old",
                upstream_asset_ids=("fan-out:chunk",),
            ),
            DerivedAssetState(
                asset_external_id="fan-out:agent",
                revision_external_id="fan-out:agent@v1",
                fact_id=case_id,
                materialized_value="old",
                critical=True,
                upstream_asset_ids=(
                    "fan-out:index-a",
                    "fan-out:index-b",
                ),
            ),
        ),
        current_facts=(
            KnowledgeFact(
                fact_id=case_id,
                value="new",
                critical=True,
            ),
        ),
        questions=(
            EvaluationQuestion(
                question_id="fan-out:question",
                fact_id=case_id,
                expected_value="new",
                critical=True,
            ),
        ),
        answer_asset_ids_by_fact={
            case_id: "fan-out:agent",
        },
        directly_affected_asset_ids=("fan-out:chunk",),
        expected_affected_asset_ids=(
            "fan-out:chunk",
            "fan-out:index-a",
            "fan-out:index-b",
            "fan-out:agent",
        ),
    )


def _build_unrelated_branch_case(
) -> InvalidationExperimentCase:
    case_id = "unrelated-branch"

    return InvalidationExperimentCase(
        case_id=case_id,
        assets=(
            DerivedAssetState(
                asset_external_id="unrelated:chunk",
                revision_external_id="unrelated:chunk@v1",
                fact_id=case_id,
                materialized_value="old",
            ),
            DerivedAssetState(
                asset_external_id="unrelated:index",
                revision_external_id="unrelated:index@v1",
                fact_id=case_id,
                materialized_value="old",
                upstream_asset_ids=("unrelated:chunk",),
            ),
            DerivedAssetState(
                asset_external_id="unrelated:agent",
                revision_external_id="unrelated:agent@v1",
                fact_id=case_id,
                materialized_value="old",
                critical=True,
                upstream_asset_ids=("unrelated:index",),
            ),
            DerivedAssetState(
                asset_external_id="unrelated:other-chunk",
                revision_external_id="unrelated:other-chunk@v1",
                fact_id="unchanged",
                materialized_value="stable",
            ),
            DerivedAssetState(
                asset_external_id="unrelated:other-index",
                revision_external_id="unrelated:other-index@v1",
                fact_id="unchanged",
                materialized_value="stable",
                upstream_asset_ids=(
                    "unrelated:other-chunk",
                ),
            ),
        ),
        current_facts=(
            KnowledgeFact(
                fact_id=case_id,
                value="new",
                critical=True,
            ),
            KnowledgeFact(
                fact_id="unchanged",
                value="stable",
            ),
        ),
        questions=(
            EvaluationQuestion(
                question_id="unrelated:question",
                fact_id=case_id,
                expected_value="new",
                critical=True,
            ),
        ),
        answer_asset_ids_by_fact={
            case_id: "unrelated:agent",
        },
        directly_affected_asset_ids=("unrelated:chunk",),
        expected_affected_asset_ids=(
            "unrelated:chunk",
            "unrelated:index",
            "unrelated:agent",
        ),
    )


def _build_shared_dependency_case(
) -> InvalidationExperimentCase:
    case_id = "shared-dependency"

    return InvalidationExperimentCase(
        case_id=case_id,
        assets=(
            DerivedAssetState(
                asset_external_id="shared:chunk",
                revision_external_id="shared:chunk@v1",
                fact_id=case_id,
                materialized_value="old",
            ),
            DerivedAssetState(
                asset_external_id="shared:index",
                revision_external_id="shared:index@v1",
                fact_id=case_id,
                materialized_value="old",
                upstream_asset_ids=("shared:chunk",),
            ),
            DerivedAssetState(
                asset_external_id="shared:agent-a",
                revision_external_id="shared:agent-a@v1",
                fact_id=case_id,
                materialized_value="old",
                critical=True,
                upstream_asset_ids=("shared:index",),
            ),
            DerivedAssetState(
                asset_external_id="shared:agent-b",
                revision_external_id="shared:agent-b@v1",
                fact_id=case_id,
                materialized_value="old",
                critical=True,
                upstream_asset_ids=("shared:index",),
            ),
        ),
        current_facts=(
            KnowledgeFact(
                fact_id=case_id,
                value="new",
                critical=True,
            ),
        ),
        questions=(
            EvaluationQuestion(
                question_id="shared:question",
                fact_id=case_id,
                expected_value="new",
                critical=True,
            ),
        ),
        answer_asset_ids_by_fact={
            case_id: "shared:agent-a",
        },
        directly_affected_asset_ids=("shared:chunk",),
        expected_affected_asset_ids=(
            "shared:chunk",
            "shared:index",
            "shared:agent-a",
            "shared:agent-b",
        ),
    )