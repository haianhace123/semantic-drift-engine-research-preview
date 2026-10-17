"""Measure unnecessary invalidation rather than dropping false positives."""
from __future__ import annotations

import pytest

from benchmarks.temporal_knowledge_integrity.rag_invalidation_corpus import (
    _calculate_affected_metrics,
    build_rag_invalidation_corpus,
    run_rag_invalidation_corpus,
)
from benchmarks.temporal_knowledge_integrity.rag_invalidation_experiment import (
    run_invalidation_strategy,
)


def test_unrelated_branch_rebuild_counts_false_positives():
    case = next(
        case for case in build_rag_invalidation_corpus()
        if case.case_id == "unrelated-branch"
    )
    result = run_invalidation_strategy(case=case, strategy="full_rebuild")
    metrics = _calculate_affected_metrics(case=case, result=result)

    assert set(result.invalidated_asset_ids) - set(case.expected_affected_asset_ids) == {
        "unrelated:other-chunk", "unrelated:other-index"
    }
    assert metrics.true_positive == 3
    assert metrics.false_positive == 2
    assert metrics.false_negative == 0
    assert metrics.precision == pytest.approx(3 / 5)
    assert metrics.exact_match is False


def test_full_rebuild_aggregate_accounts_for_unnecessary_assets():
    result = run_rag_invalidation_corpus(
        cases=build_rag_invalidation_corpus(),
        strategy="full_rebuild",
    )
    assert result.affected.true_positive == 20
    assert result.affected.false_positive == 2
    assert result.affected.false_negative == 0
    assert result.affected.precision == pytest.approx(20 / 22)
    assert result.affected.exact_match_count == 5
    assert result.affected.exact_match_rate == pytest.approx(5 / 6)
