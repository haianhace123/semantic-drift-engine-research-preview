from __future__ import annotations

from benchmarks.temporal_knowledge_integrity.dependency_impact_cases import (
    load_dependency_impact_cases,
)
from benchmarks.temporal_knowledge_integrity.run_dependency_impact_benchmark import (
    run_benchmark,
)


def test_dependency_impact_corpus_has_distinct_failure_families() -> None:
    cases = load_dependency_impact_cases()

    assert len(cases) == 6
    assert {
        case.category
        for case in cases
    } == {
        "fan_out",
        "irrelevant_branch",
        "multi_hop",
        "multiple_roots",
        "shared_dependency",
        "topology_unchanged",
    }

    assert len(
        {
            case.case_id
            for case in cases
        }
    ) == len(cases)


def test_dependency_impact_ground_truth_is_nontrivial() -> None:
    cases = load_dependency_impact_cases()

    assert all(
        case.expected_affected_node_ids
        for case in cases
    )
    assert all(
        case.expected_invalidation_candidate_ids
        for case in cases
    )
    assert all(
        case.expected_critical_node_ids
        for case in cases
    )

    assert any(
        len(case.expected_affected_node_ids)
        >= 5
        for case in cases
    )


def test_dependency_transitive_beats_direct_only_on_affected_recall() -> None:
    results = {
        result.method_name: result
        for result in run_benchmark()
    }

    direct = results["direct_only"]
    transitive = results[
        "dependency_transitive"
    ]

    assert (
        transitive.metrics.affected.recall
        > direct.metrics.affected.recall
    )


def test_dependency_transitive_recovers_critical_surfaces() -> None:
    results = {
        result.method_name: result
        for result in run_benchmark()
    }

    direct = results["direct_only"]
    transitive = results[
        "dependency_transitive"
    ]

    assert (
        direct.metrics.critical.recall
        < 1.0
    )
    assert (
        transitive.metrics.critical.recall
        == 1.0
    )


def test_dependency_transitive_matches_frozen_ground_truth() -> None:
    results = {
        result.method_name: result
        for result in run_benchmark()
    }

    transitive = results[
        "dependency_transitive"
    ]

    assert (
        transitive.metrics.affected.exact_match_rate
        == 1.0
    )
    assert (
        transitive.metrics.invalidation.exact_match_rate
        == 1.0
    )
    assert (
        transitive.metrics.critical.exact_match_rate
        == 1.0
    )