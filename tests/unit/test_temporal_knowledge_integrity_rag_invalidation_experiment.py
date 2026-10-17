import pytest

from benchmarks.temporal_knowledge_integrity.rag_invalidation_experiment import (
    DerivedAssetState,
    EvaluationQuestion,
    KnowledgeFact,
    StrategyResult,
    select_invalidated_asset_ids,
    rebuild_invalidated_assets,
    evaluate_materialized_answers,
    InvalidationExperimentCase,
    run_invalidation_strategy,
)


def test_experiment_domain_preserves_knowledge_and_derived_state() -> None:
    fact = KnowledgeFact(
        fact_id="refund-window",
        value="14 days",
        critical=True,
    )
    asset = DerivedAssetState(
        asset_external_id="index-refund",
        revision_external_id="index-refund@v1",
        fact_id="refund-window",
        materialized_value="30 days",
        critical=True,
    )
    question = EvaluationQuestion(
        question_id="refund-window-question",
        fact_id="refund-window",
        expected_value="14 days",
        critical=True,
    )

    assert fact.value == "14 days"
    assert asset.materialized_value == "30 days"
    assert question.expected_value == fact.value


def test_stale_answer_rate_is_fraction_of_all_answers() -> None:
    result = StrategyResult(
        strategy="dependency_transitive",
        invalidated_asset_ids=("index-refund",),
        reprocessing_units=1,
        stale_answer_count=2,
        critical_stale_answer_count=1,
        total_answer_count=10,
        critical_answer_count=4,
        expected_affected_asset_count=1,
        correctly_invalidated_asset_count=1,
    )

    assert result.stale_answer_rate == pytest.approx(0.2)


def test_critical_stale_answer_rate_requires_critical_answer_denominator() -> None:
    result = StrategyResult(
        strategy="dependency_transitive",
        invalidated_asset_ids=("index-refund",),
        reprocessing_units=1,
        stale_answer_count=2,
        critical_stale_answer_count=1,
        total_answer_count=10,
        critical_answer_count=4,
        expected_affected_asset_count=1,
        correctly_invalidated_asset_count=1,
    )

    assert result.critical_stale_answer_rate == pytest.approx(0.25)

def test_invalidation_strategies_select_different_blast_radius() -> None:
    assets = (
        DerivedAssetState(
            asset_external_id="chunk-refund",
            revision_external_id="chunk-refund@v1",
            fact_id="refund-window",
            materialized_value="30 days",
        ),
        DerivedAssetState(
            asset_external_id="index-refund",
            revision_external_id="index-refund@v1",
            fact_id="refund-window",
            materialized_value="30 days",
            upstream_asset_ids=("chunk-refund",),
        ),
        DerivedAssetState(
            asset_external_id="agent-refund",
            revision_external_id="agent-refund@v1",
            fact_id="refund-window",
            materialized_value="30 days",
            critical=True,
            upstream_asset_ids=("index-refund",),
        ),
        DerivedAssetState(
            asset_external_id="chunk-shipping",
            revision_external_id="chunk-shipping@v1",
            fact_id="shipping-window",
            materialized_value="5 days",
        ),
        DerivedAssetState(
            asset_external_id="index-shipping",
            revision_external_id="index-shipping@v1",
            fact_id="shipping-window",
            materialized_value="5 days",
            upstream_asset_ids=("chunk-shipping",),
        ),
    )

    direct_ids = ("chunk-refund",)

    assert select_invalidated_asset_ids(
        strategy="no_invalidation",
        assets=assets,
        directly_affected_asset_ids=direct_ids,
    ) == ()

    assert select_invalidated_asset_ids(
        strategy="direct_only",
        assets=assets,
        directly_affected_asset_ids=direct_ids,
    ) == ("chunk-refund",)

    assert select_invalidated_asset_ids(
        strategy="dependency_transitive",
        assets=assets,
        directly_affected_asset_ids=direct_ids,
    ) == (
        "agent-refund",
        "chunk-refund",
        "index-refund",
    )

    assert select_invalidated_asset_ids(
        strategy="full_rebuild",
        assets=assets,
        directly_affected_asset_ids=direct_ids,
    ) == (
        "agent-refund",
        "chunk-refund",
        "chunk-shipping",
        "index-refund",
        "index-shipping",
    )

def test_direct_only_rebuild_leaves_downstream_state_stale() -> None:
    assets = (
        DerivedAssetState(
            asset_external_id="agent-refund",
            revision_external_id="agent-refund@v1",
            fact_id="refund-window",
            materialized_value="30 days",
            critical=True,
            upstream_asset_ids=("index-refund",),
        ),
        DerivedAssetState(
            asset_external_id="index-refund",
            revision_external_id="index-refund@v1",
            fact_id="refund-window",
            materialized_value="30 days",
            upstream_asset_ids=("chunk-refund",),
        ),
        DerivedAssetState(
            asset_external_id="chunk-refund",
            revision_external_id="chunk-refund@v1",
            fact_id="refund-window",
            materialized_value="30 days",
        ),
    )

    result = rebuild_invalidated_assets(
        assets=assets,
        invalidated_asset_ids=("chunk-refund",),
        current_facts=(
            KnowledgeFact(
                fact_id="refund-window",
                value="14 days",
                critical=True,
            ),
        ),
    )

    state_by_id = {
        asset.asset_external_id: asset
        for asset in result.assets
    }

    assert (
        state_by_id["chunk-refund"].materialized_value
        == "14 days"
    )
    assert (
        state_by_id["index-refund"].materialized_value
        == "30 days"
    )
    assert (
        state_by_id["agent-refund"].materialized_value
        == "30 days"
    )
    assert result.rebuilt_asset_ids == ("chunk-refund",)

def test_transitive_rebuild_follows_dependency_order() -> None:
    assets = (
        DerivedAssetState(
            asset_external_id="agent-refund",
            revision_external_id="agent-refund@v1",
            fact_id="refund-window",
            materialized_value="30 days",
            critical=True,
            upstream_asset_ids=("index-refund",),
        ),
        DerivedAssetState(
            asset_external_id="index-refund",
            revision_external_id="index-refund@v1",
            fact_id="refund-window",
            materialized_value="30 days",
            upstream_asset_ids=("chunk-refund",),
        ),
        DerivedAssetState(
            asset_external_id="chunk-refund",
            revision_external_id="chunk-refund@v1",
            fact_id="refund-window",
            materialized_value="30 days",
        ),
    )

    result = rebuild_invalidated_assets(
        assets=assets,
        invalidated_asset_ids=(
            "agent-refund",
            "chunk-refund",
            "index-refund",
        ),
        current_facts=(
            KnowledgeFact(
                fact_id="refund-window",
                value="14 days",
                critical=True,
            ),
        ),
    )

    state_by_id = {
        asset.asset_external_id: asset
        for asset in result.assets
    }

    assert (
        result.rebuilt_asset_ids
        == (
            "chunk-refund",
            "index-refund",
            "agent-refund",
        )
    )
    assert (
        state_by_id["chunk-refund"].materialized_value
        == "14 days"
    )
    assert (
        state_by_id["index-refund"].materialized_value
        == "14 days"
    )
    assert (
        state_by_id["agent-refund"].materialized_value
        == "14 days"
    )

def test_answer_evaluation_reads_materialized_state_only() -> None:
    evaluations = evaluate_materialized_answers(
        assets=(
            DerivedAssetState(
                asset_external_id="agent-refund",
                revision_external_id="agent-refund@v1",
                fact_id="refund-window",
                materialized_value="30 days",
                critical=True,
            ),
            DerivedAssetState(
                asset_external_id="agent-shipping",
                revision_external_id="agent-shipping@v1",
                fact_id="shipping-window",
                materialized_value="5 days",
            ),
        ),
        questions=(
            EvaluationQuestion(
                question_id="refund-question",
                fact_id="refund-window",
                expected_value="14 days",
                critical=True,
            ),
            EvaluationQuestion(
                question_id="shipping-question",
                fact_id="shipping-window",
                expected_value="5 days",
            ),
        ),
        answer_asset_ids_by_fact={
            "refund-window": "agent-refund",
            "shipping-window": "agent-shipping",
        },
    )

    assert evaluations[0].observed_value == "30 days"
    assert evaluations[0].stale is True
    assert evaluations[0].critical is True

    assert evaluations[1].observed_value == "5 days"
    assert evaluations[1].stale is False
    assert evaluations[1].critical is False

def test_targeted_transitive_rebuild_matches_full_rebuild_correctness_with_less_work() -> None:
    assets = (
        DerivedAssetState(
            asset_external_id="agent-refund",
            revision_external_id="agent-refund@v1",
            fact_id="refund-window",
            materialized_value="30 days",
            critical=True,
            upstream_asset_ids=("index-refund",),
        ),
        DerivedAssetState(
            asset_external_id="index-refund",
            revision_external_id="index-refund@v1",
            fact_id="refund-window",
            materialized_value="30 days",
            upstream_asset_ids=("chunk-refund",),
        ),
        DerivedAssetState(
            asset_external_id="chunk-refund",
            revision_external_id="chunk-refund@v1",
            fact_id="refund-window",
            materialized_value="30 days",
        ),
        DerivedAssetState(
            asset_external_id="agent-shipping",
            revision_external_id="agent-shipping@v1",
            fact_id="shipping-window",
            materialized_value="5 days",
            upstream_asset_ids=("index-shipping",),
        ),
        DerivedAssetState(
            asset_external_id="index-shipping",
            revision_external_id="index-shipping@v1",
            fact_id="shipping-window",
            materialized_value="5 days",
            upstream_asset_ids=("chunk-shipping",),
        ),
        DerivedAssetState(
            asset_external_id="chunk-shipping",
            revision_external_id="chunk-shipping@v1",
            fact_id="shipping-window",
            materialized_value="5 days",
        ),
    )
    current_facts = (
        KnowledgeFact(
            fact_id="refund-window",
            value="14 days",
            critical=True,
        ),
        KnowledgeFact(
            fact_id="shipping-window",
            value="5 days",
        ),
    )
    questions = (
        EvaluationQuestion(
            question_id="refund-question",
            fact_id="refund-window",
            expected_value="14 days",
            critical=True,
        ),
        EvaluationQuestion(
            question_id="shipping-question",
            fact_id="shipping-window",
            expected_value="5 days",
        ),
    )
    answer_assets = {
        "refund-window": "agent-refund",
        "shipping-window": "agent-shipping",
    }
    direct_ids = ("chunk-refund",)

    direct_invalidated = select_invalidated_asset_ids(
        strategy="direct_only",
        assets=assets,
        directly_affected_asset_ids=direct_ids,
    )
    targeted_invalidated = select_invalidated_asset_ids(
        strategy="dependency_transitive",
        assets=assets,
        directly_affected_asset_ids=direct_ids,
    )
    full_invalidated = select_invalidated_asset_ids(
        strategy="full_rebuild",
        assets=assets,
        directly_affected_asset_ids=direct_ids,
    )

    direct_result = rebuild_invalidated_assets(
        assets=assets,
        invalidated_asset_ids=direct_invalidated,
        current_facts=current_facts,
    )
    targeted_result = rebuild_invalidated_assets(
        assets=assets,
        invalidated_asset_ids=targeted_invalidated,
        current_facts=current_facts,
    )
    full_result = rebuild_invalidated_assets(
        assets=assets,
        invalidated_asset_ids=full_invalidated,
        current_facts=current_facts,
    )

    direct_evaluations = evaluate_materialized_answers(
        assets=direct_result.assets,
        questions=questions,
        answer_asset_ids_by_fact=answer_assets,
    )
    targeted_evaluations = evaluate_materialized_answers(
        assets=targeted_result.assets,
        questions=questions,
        answer_asset_ids_by_fact=answer_assets,
    )
    full_evaluations = evaluate_materialized_answers(
        assets=full_result.assets,
        questions=questions,
        answer_asset_ids_by_fact=answer_assets,
    )

    assert sum(
        evaluation.stale
        for evaluation in direct_evaluations
    ) == 1

    assert sum(
        evaluation.stale
        for evaluation in targeted_evaluations
    ) == 0

    assert sum(
        evaluation.stale
        for evaluation in full_evaluations
    ) == 0

    assert (
        targeted_result.rebuilt_asset_ids
        == (
            "chunk-refund",
            "index-refund",
            "agent-refund",
        )
    )
    assert len(targeted_result.rebuilt_asset_ids) == 3
    assert len(full_result.rebuilt_asset_ids) == 6

def test_strategy_runner_exposes_correctness_and_work_tradeoff() -> None:
    case = InvalidationExperimentCase(
        case_id="refund-policy-change",
        assets=(
            DerivedAssetState(
                asset_external_id="chunk-refund",
                revision_external_id="chunk-refund@v1",
                fact_id="refund-window",
                materialized_value="30 days",
            ),
            DerivedAssetState(
                asset_external_id="index-refund",
                revision_external_id="index-refund@v1",
                fact_id="refund-window",
                materialized_value="30 days",
                upstream_asset_ids=("chunk-refund",),
            ),
            DerivedAssetState(
                asset_external_id="agent-refund",
                revision_external_id="agent-refund@v1",
                fact_id="refund-window",
                materialized_value="30 days",
                critical=True,
                upstream_asset_ids=("index-refund",),
            ),
            DerivedAssetState(
                asset_external_id="chunk-shipping",
                revision_external_id="chunk-shipping@v1",
                fact_id="shipping-window",
                materialized_value="5 days",
            ),
            DerivedAssetState(
                asset_external_id="index-shipping",
                revision_external_id="index-shipping@v1",
                fact_id="shipping-window",
                materialized_value="5 days",
                upstream_asset_ids=("chunk-shipping",),
            ),
            DerivedAssetState(
                asset_external_id="agent-shipping",
                revision_external_id="agent-shipping@v1",
                fact_id="shipping-window",
                materialized_value="5 days",
                upstream_asset_ids=("index-shipping",),
            ),
        ),
        current_facts=(
            KnowledgeFact(
                fact_id="refund-window",
                value="14 days",
                critical=True,
            ),
            KnowledgeFact(
                fact_id="shipping-window",
                value="5 days",
            ),
        ),
        questions=(
            EvaluationQuestion(
                question_id="refund-question",
                fact_id="refund-window",
                expected_value="14 days",
                critical=True,
            ),
            EvaluationQuestion(
                question_id="shipping-question",
                fact_id="shipping-window",
                expected_value="5 days",
            ),
        ),
        answer_asset_ids_by_fact={
            "refund-window": "agent-refund",
            "shipping-window": "agent-shipping",
        },
        directly_affected_asset_ids=("chunk-refund",),
        expected_affected_asset_ids=(
            "chunk-refund",
            "index-refund",
            "agent-refund",
        ),
    )

    results = {
        strategy: run_invalidation_strategy(
            case=case,
            strategy=strategy,
        )
        for strategy in (
            "no_invalidation",
            "direct_only",
            "dependency_transitive",
            "full_rebuild",
        )
    }

    assert results["no_invalidation"].stale_answer_count == 1
    assert results["no_invalidation"].reprocessing_units == 0
    assert results["no_invalidation"].affected_asset_recall == 0.0

    assert results["direct_only"].stale_answer_count == 1
    assert results["direct_only"].reprocessing_units == 1
    assert results["direct_only"].affected_asset_recall == pytest.approx(
        1 / 3
    )

    assert (
        results["dependency_transitive"].stale_answer_count
        == 0
    )
    assert (
        results["dependency_transitive"].critical_stale_answer_count
        == 0
    )
    assert (
        results["dependency_transitive"].reprocessing_units
        == 3
    )
    assert (
        results["dependency_transitive"].affected_asset_recall
        == 1.0
    )

    assert results["full_rebuild"].stale_answer_count == 0
    assert results["full_rebuild"].reprocessing_units == 6
    assert results["full_rebuild"].affected_asset_recall == 1.0

def test_incomplete_lineage_reduces_recall_and_leaves_critical_state_stale() -> None:
    case = InvalidationExperimentCase(
        case_id="refund-policy-missing-agent-edge",
        assets=(
            DerivedAssetState(
                asset_external_id="chunk-refund",
                revision_external_id="chunk-refund@v1",
                fact_id="refund-window",
                materialized_value="30 days",
            ),
            DerivedAssetState(
                asset_external_id="index-refund",
                revision_external_id="index-refund@v1",
                fact_id="refund-window",
                materialized_value="30 days",
                upstream_asset_ids=("chunk-refund",),
            ),
            DerivedAssetState(
                asset_external_id="agent-refund",
                revision_external_id="agent-refund@v1",
                fact_id="refund-window",
                materialized_value="30 days",
                critical=True,
            ),
        ),
        current_facts=(
            KnowledgeFact(
                fact_id="refund-window",
                value="14 days",
                critical=True,
            ),
        ),
        questions=(
            EvaluationQuestion(
                question_id="refund-question",
                fact_id="refund-window",
                expected_value="14 days",
                critical=True,
            ),
        ),
        answer_asset_ids_by_fact={
            "refund-window": "agent-refund",
        },
        directly_affected_asset_ids=("chunk-refund",),
        expected_affected_asset_ids=(
            "chunk-refund",
            "index-refund",
            "agent-refund",
        ),
    )

    targeted = run_invalidation_strategy(
        case=case,
        strategy="dependency_transitive",
    )
    full = run_invalidation_strategy(
        case=case,
        strategy="full_rebuild",
    )

    assert targeted.invalidated_asset_ids == (
        "chunk-refund",
        "index-refund",
    )
    assert targeted.affected_asset_recall == pytest.approx(
        2 / 3
    )
    assert targeted.stale_answer_count == 1
    assert targeted.critical_stale_answer_count == 1
    assert targeted.critical_stale_answer_rate == 1.0

    assert full.affected_asset_recall == 1.0
    assert full.stale_answer_count == 0
    assert full.critical_stale_answer_count == 0