from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


InvalidationStrategy = Literal[
    "no_invalidation",
    "full_rebuild",
    "direct_only",
    "dependency_transitive",
]


@dataclass(frozen=True)
class KnowledgeFact:
    fact_id: str
    value: str
    critical: bool = False


@dataclass(frozen=True)
class DerivedAssetState:
    asset_external_id: str
    revision_external_id: str
    fact_id: str
    materialized_value: str
    materialized: bool = True
    critical: bool = False
    upstream_asset_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvaluationQuestion:
    question_id: str
    fact_id: str
    expected_value: str
    critical: bool = False

@dataclass(frozen=True)
class RebuildResult:
    assets: tuple[DerivedAssetState, ...]
    rebuilt_asset_ids: tuple[str, ...]

@dataclass(frozen=True)
class AnswerEvaluation:
    question_id: str
    observed_value: str
    expected_value: str
    stale: bool
    critical: bool

@dataclass(frozen=True)
class InvalidationExperimentCase:
    case_id: str
    assets: tuple[DerivedAssetState, ...]
    current_facts: tuple[KnowledgeFact, ...]
    questions: tuple[EvaluationQuestion, ...]
    answer_asset_ids_by_fact: dict[str, str]
    directly_affected_asset_ids: tuple[str, ...]
    expected_affected_asset_ids: tuple[str, ...]

@dataclass(frozen=True)
class StrategyResult:
    strategy: InvalidationStrategy
    invalidated_asset_ids: tuple[str, ...]
    reprocessing_units: int
    stale_answer_count: int
    critical_stale_answer_count: int
    total_answer_count: int
    critical_answer_count: int
    expected_affected_asset_count: int
    correctly_invalidated_asset_count: int

    @property
    def affected_asset_recall(self) -> float:
        if self.expected_affected_asset_count == 0:
            return 1.0
        return (
            self.correctly_invalidated_asset_count
            / self.expected_affected_asset_count
        )

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

def select_invalidated_asset_ids(
    *,
    strategy: InvalidationStrategy,
    assets: tuple[DerivedAssetState, ...],
    directly_affected_asset_ids: tuple[str, ...],
) -> tuple[str, ...]:
    asset_ids = {
        asset.asset_external_id
        for asset in assets
    }
    direct_ids = set(directly_affected_asset_ids)

    unknown_direct_ids = direct_ids - asset_ids
    if unknown_direct_ids:
        raise ValueError(
            "Directly affected assets must exist in experiment state."
        )

    if strategy == "no_invalidation":
        return ()

    if strategy == "full_rebuild":
        return tuple(sorted(asset_ids))

    if strategy == "direct_only":
        return tuple(sorted(direct_ids))

    affected_ids = set(direct_ids)

    changed = True
    while changed:
        changed = False

        for asset in assets:
            if asset.asset_external_id in affected_ids:
                continue

            if any(
                upstream_asset_id in affected_ids
                for upstream_asset_id in asset.upstream_asset_ids
            ):
                affected_ids.add(asset.asset_external_id)
                changed = True

    return tuple(sorted(affected_ids))

def rebuild_invalidated_assets(
    *,
    assets: tuple[DerivedAssetState, ...],
    invalidated_asset_ids: tuple[str, ...],
    current_facts: tuple[KnowledgeFact, ...],
) -> RebuildResult:
    assets_by_id = {
        asset.asset_external_id: asset
        for asset in assets
    }
    facts_by_id = {
        fact.fact_id: fact
        for fact in current_facts
    }
    invalidated_ids = set(invalidated_asset_ids)

    unknown_asset_ids = invalidated_ids - assets_by_id.keys()
    if unknown_asset_ids:
        raise ValueError(
            "Invalidated assets must exist in experiment state."
        )

    rebuilt_assets: dict[str, DerivedAssetState] = dict(
        assets_by_id
    )
    pending_ids = set(invalidated_ids)
    rebuilt_asset_ids: list[str] = []

    while pending_ids:
        progress = False

        for asset_id in sorted(pending_ids):
            asset = rebuilt_assets[asset_id]

            invalidated_upstream_ids = {
                upstream_asset_id
                for upstream_asset_id in asset.upstream_asset_ids
                if upstream_asset_id in invalidated_ids
            }

            if any(
                upstream_asset_id in pending_ids
                for upstream_asset_id in invalidated_upstream_ids
            ):
                continue

            fact = facts_by_id.get(asset.fact_id)
            if fact is None:
                raise ValueError(
                    "Derived asset fact must exist in current facts."
                )

            if asset.upstream_asset_ids:
                upstream_values = {
                    rebuilt_assets[
                        upstream_asset_id
                    ].materialized_value
                    for upstream_asset_id
                    in asset.upstream_asset_ids
                }

                if len(upstream_values) != 1:
                    raise ValueError(
                        "Upstream materialized values must agree."
                    )

                materialized_value = next(
                    iter(upstream_values)
                )
            else:
                materialized_value = fact.value

            rebuilt_assets[asset_id] = DerivedAssetState(
                asset_external_id=asset.asset_external_id,
                revision_external_id=(
                    f"{asset.revision_external_id}:rebuilt"
                ),
                fact_id=asset.fact_id,
                materialized_value=materialized_value,
                materialized=asset.materialized,
                critical=asset.critical,
                upstream_asset_ids=asset.upstream_asset_ids,
            )

            rebuilt_asset_ids.append(asset_id)
            pending_ids.remove(asset_id)
            progress = True

        if not progress:
            raise ValueError(
                "Invalidated asset dependencies cannot be resolved."
            )

    return RebuildResult(
        assets=tuple(
            rebuilt_assets[asset.asset_external_id]
            for asset in assets
        ),
        rebuilt_asset_ids=tuple(rebuilt_asset_ids),
    )

def evaluate_materialized_answers(
    *,
    assets: tuple[DerivedAssetState, ...],
    questions: tuple[EvaluationQuestion, ...],
    answer_asset_ids_by_fact: dict[str, str],
) -> tuple[AnswerEvaluation, ...]:
    assets_by_id = {
        asset.asset_external_id: asset
        for asset in assets
    }

    evaluations: list[AnswerEvaluation] = []

    for question in questions:
        answer_asset_id = answer_asset_ids_by_fact.get(
            question.fact_id
        )
        if answer_asset_id is None:
            raise ValueError(
                "Evaluation fact must have an answer asset."
            )

        answer_asset = assets_by_id.get(answer_asset_id)
        if answer_asset is None:
            raise ValueError(
                "Answer asset must exist in experiment state."
            )

        if answer_asset.fact_id != question.fact_id:
            raise ValueError(
                "Answer asset fact must match evaluation fact."
            )

        evaluations.append(
            AnswerEvaluation(
                question_id=question.question_id,
                observed_value=answer_asset.materialized_value,
                expected_value=question.expected_value,
                stale=(
                    answer_asset.materialized_value
                    != question.expected_value
                ),
                critical=question.critical,
            )
        )

    return tuple(evaluations)

def run_invalidation_strategy(
    *,
    case: InvalidationExperimentCase,
    strategy: InvalidationStrategy,
) -> StrategyResult:
    expected_affected_asset_ids = set(
        case.expected_affected_asset_ids
    )

    invalidated_asset_ids = select_invalidated_asset_ids(
        strategy=strategy,
        assets=case.assets,
        directly_affected_asset_ids=(
            case.directly_affected_asset_ids
        ),
    )

    rebuild_result = rebuild_invalidated_assets(
        assets=case.assets,
        invalidated_asset_ids=invalidated_asset_ids,
        current_facts=case.current_facts,
    )

    evaluations = evaluate_materialized_answers(
        assets=rebuild_result.assets,
        questions=case.questions,
        answer_asset_ids_by_fact=(
            case.answer_asset_ids_by_fact
        ),
    )

    invalidated_ids = set(invalidated_asset_ids)

    return StrategyResult(
        strategy=strategy,
        invalidated_asset_ids=invalidated_asset_ids,
        reprocessing_units=len(
            rebuild_result.rebuilt_asset_ids
        ),
        stale_answer_count=sum(
            evaluation.stale
            for evaluation in evaluations
        ),
        critical_stale_answer_count=sum(
            evaluation.stale and evaluation.critical
            for evaluation in evaluations
        ),
        total_answer_count=len(evaluations),
        critical_answer_count=sum(
            evaluation.critical
            for evaluation in evaluations
        ),
        expected_affected_asset_count=len(
            expected_affected_asset_ids
        ),
        correctly_invalidated_asset_count=len(
            invalidated_ids & expected_affected_asset_ids
        ),
    )