from __future__ import annotations

from collections import Counter

from benchmarks.temporal_knowledge_integrity.topology_cases import (
    load_topology_cases,
)


def test_topology_corpus_has_expected_size_and_balance() -> None:
    cases = load_topology_cases()

    assert len(cases) == 24

    split_counts = Counter(
        case.split
        for case in cases
    )
    consequential_counts = Counter(
        case.split
        for case in cases
        if case.consequential_change
    )
    critical_counts = Counter(
        case.split
        for case in cases
        if case.critical_change
    )

    assert split_counts == {
        "calibration": 12,
        "evaluation": 12,
    }
    assert consequential_counts == {
        "calibration": 6,
        "evaluation": 6,
    }
    assert critical_counts == {
        "calibration": 6,
        "evaluation": 6,
    }


def test_topology_corpus_has_balanced_categories() -> None:
    cases = load_topology_cases()

    expected_categories = {
        "dependency_path",
        "authority_route",
        "control_bypass",
        "propagation_change",
        "approval_chain",
        "dependency_rewire",
    }

    assert {
        case.category
        for case in cases
    } == expected_categories

    for split in (
        "calibration",
        "evaluation",
    ):
        split_cases = [
            case
            for case in cases
            if case.split == split
        ]

        category_counts = Counter(
            case.category
            for case in split_cases
        )

        assert category_counts == {
            category: 2
            for category in expected_categories
        }


def test_topology_pairs_share_before_graph_and_labels() -> None:
    cases = load_topology_cases()

    by_pair = {}

    for case in cases:
        by_pair.setdefault(
            case.pair_id,
            [],
        ).append(case)

    assert len(by_pair) == 12

    for pair_cases in by_pair.values():
        assert len(pair_cases) == 2

        control = next(
            case
            for case in pair_cases
            if not case.consequential_change
        )
        mutation = next(
            case
            for case in pair_cases
            if case.consequential_change
        )

        assert control.before == mutation.before
        assert control.after != control.before
        assert mutation.after != mutation.before
        assert mutation.critical_change is True


def test_topology_corpus_contains_real_benign_structural_churn() -> None:
    cases = load_topology_cases()

    controls = [
        case
        for case in cases
        if not case.consequential_change
    ]

    assert controls
    assert all(
        case.after != case.before
        for case in controls
    )


def test_topology_graph_edges_reference_declared_entities() -> None:
    cases = load_topology_cases()

    for case in cases:
        for state in (
            case.before,
            case.after,
        ):
            entity_ids = set(
                state.entities
            )

            for source, target in state.edges:
                assert source in entity_ids
                assert target in entity_ids
                assert source != target