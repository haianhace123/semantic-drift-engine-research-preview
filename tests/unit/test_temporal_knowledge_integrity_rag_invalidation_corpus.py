import pytest

from benchmarks.temporal_knowledge_integrity.rag_invalidation_corpus import (
    build_rag_invalidation_corpus,
    run_rag_invalidation_corpus,
)


def test_rag_invalidation_corpus_contains_expected_failure_shapes() -> None:
    cases = build_rag_invalidation_corpus()

    assert tuple(
        case.case_id
        for case in cases
    ) == (
        "complete-lineage",
        "missing-terminal-edge",
        "missing-middle-edge",
        "fan-out",
        "unrelated-branch",
        "shared-dependency",
    )


def test_dependency_transitive_degrades_when_lineage_is_incomplete() -> None:
    result = run_rag_invalidation_corpus(
        cases=build_rag_invalidation_corpus(),
        strategy="dependency_transitive",
    )

    assert result.case_count == 6
    assert result.affected.recall < 1.0
    assert result.affected.recall > 0.0
    assert result.stale_answer_count == 2
    assert result.critical_stale_answer_count == 2
    assert result.critical_stale_answer_rate == pytest.approx(
        2 / 6
    )


def test_full_rebuild_restores_answers_but_does_more_work() -> None:
    cases = build_rag_invalidation_corpus()

    targeted = run_rag_invalidation_corpus(
        cases=cases,
        strategy="dependency_transitive",
    )
    full = run_rag_invalidation_corpus(
        cases=cases,
        strategy="full_rebuild",
    )

    assert full.stale_answer_count == 0
    assert full.critical_stale_answer_count == 0
    assert full.affected.recall == 1.0
    assert targeted.reprocessing_units < full.reprocessing_units


def test_direct_only_has_lower_impact_recall_than_transitive() -> None:
    cases = build_rag_invalidation_corpus()

    direct = run_rag_invalidation_corpus(
        cases=cases,
        strategy="direct_only",
    )
    targeted = run_rag_invalidation_corpus(
        cases=cases,
        strategy="dependency_transitive",
    )

    assert direct.affected.recall < targeted.affected.recall
    assert direct.stale_answer_count > targeted.stale_answer_count