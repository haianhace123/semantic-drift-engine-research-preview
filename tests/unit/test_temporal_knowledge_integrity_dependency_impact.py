from __future__ import annotations

import pytest

from benchmarks.temporal_knowledge_integrity.dependency_impact import (
    DependencyEdge,
    DependencyGraph,
    KnowledgeNode,
    analyze_dependency_impact,
)


def _production_like_graph() -> DependencyGraph:
    return DependencyGraph(
        nodes=(
            KnowledgeNode(
                id="source",
                kind="source",
            ),
            KnowledgeNode(
                id="snapshot",
                kind="snapshot",
            ),
            KnowledgeNode(
                id="chunk",
                kind="chunk",
                materialized=True,
            ),
            KnowledgeNode(
                id="index",
                kind="index",
                materialized=True,
            ),
            KnowledgeNode(
                id="agent",
                kind="agent",
                critical=True,
            ),
            KnowledgeNode(
                id="workflow",
                kind="workflow",
                critical=True,
            ),
        ),
        edges=(
            DependencyEdge(
                "source",
                "snapshot",
                "version_of",
            ),
            DependencyEdge(
                "snapshot",
                "chunk",
                "derived_from",
            ),
            DependencyEdge(
                "chunk",
                "index",
                "indexed_by",
            ),
            DependencyEdge(
                "index",
                "agent",
                "consumed_by",
            ),
            DependencyEdge(
                "agent",
                "workflow",
                "feeds",
            ),
        ),
    )


def test_dependency_impact_propagates_downstream() -> None:
    result = analyze_dependency_impact(
        graph=_production_like_graph(),
        changed_node_ids=("snapshot",),
    )

    assert result.directly_affected_node_ids == (
        "chunk",
    )
    assert result.transitively_affected_node_ids == (
        "agent",
        "index",
        "workflow",
    )
    assert result.affected_node_ids == (
        "agent",
        "chunk",
        "index",
        "workflow",
    )


def test_dependency_impact_separates_repair_from_blast_radius() -> None:
    result = analyze_dependency_impact(
        graph=_production_like_graph(),
        changed_node_ids=("snapshot",),
    )

    assert result.invalidation_candidate_ids == (
        "chunk",
        "index",
    )
    assert result.critical_node_ids == (
        "agent",
        "workflow",
    )


def test_dependency_impact_records_shortest_provenance_paths() -> None:
    result = analyze_dependency_impact(
        graph=_production_like_graph(),
        changed_node_ids=("snapshot",),
    )

    paths = {
        (
            path.changed_node_id,
            path.affected_node_id,
        ): path.node_ids
        for path in result.affected_paths
    }

    assert paths[
        ("snapshot", "chunk")
    ] == (
        "snapshot",
        "chunk",
    )
    assert paths[
        ("snapshot", "workflow")
    ] == (
        "snapshot",
        "chunk",
        "index",
        "agent",
        "workflow",
    )


def test_dependency_impact_preserves_provenance_per_changed_root() -> None:
    graph = DependencyGraph(
        nodes=(
            KnowledgeNode(
                id="policy_a",
                kind="snapshot",
            ),
            KnowledgeNode(
                id="policy_b",
                kind="snapshot",
            ),
            KnowledgeNode(
                id="chunk",
                kind="chunk",
                materialized=True,
            ),
            KnowledgeNode(
                id="index",
                kind="index",
                materialized=True,
            ),
        ),
        edges=(
            DependencyEdge(
                "policy_a",
                "chunk",
                "derived_from",
            ),
            DependencyEdge(
                "policy_b",
                "chunk",
                "derived_from",
            ),
            DependencyEdge(
                "chunk",
                "index",
                "indexed_by",
            ),
        ),
    )

    result = analyze_dependency_impact(
        graph=graph,
        changed_node_ids=(
            "policy_b",
            "policy_a",
        ),
    )

    paths = {
        (
            path.changed_node_id,
            path.affected_node_id,
        ): path.node_ids
        for path in result.affected_paths
    }

    assert paths[
        ("policy_a", "index")
    ] == (
        "policy_a",
        "chunk",
        "index",
    )
    assert paths[
        ("policy_b", "index")
    ] == (
        "policy_b",
        "chunk",
        "index",
    )


def test_dependency_impact_uses_deterministic_shortest_path() -> None:
    graph = DependencyGraph(
        nodes=(
            KnowledgeNode(
                id="root",
                kind="snapshot",
            ),
            KnowledgeNode(
                id="branch_a",
                kind="chunk",
            ),
            KnowledgeNode(
                id="branch_b",
                kind="chunk",
            ),
            KnowledgeNode(
                id="index",
                kind="index",
                materialized=True,
            ),
        ),
        edges=(
            DependencyEdge(
                "root",
                "branch_b",
                "derived_from",
            ),
            DependencyEdge(
                "root",
                "branch_a",
                "derived_from",
            ),
            DependencyEdge(
                "branch_b",
                "index",
                "indexed_by",
            ),
            DependencyEdge(
                "branch_a",
                "index",
                "indexed_by",
            ),
        ),
    )

    result = analyze_dependency_impact(
        graph=graph,
        changed_node_ids=("root",),
    )

    index_path = next(
        path
        for path in result.affected_paths
        if path.affected_node_id == "index"
    )

    assert index_path.node_ids == (
        "root",
        "branch_a",
        "index",
    )


def test_dependency_impact_is_cycle_safe() -> None:
    graph = DependencyGraph(
        nodes=(
            KnowledgeNode(
                id="snapshot",
                kind="snapshot",
            ),
            KnowledgeNode(
                id="chunk",
                kind="chunk",
            ),
            KnowledgeNode(
                id="index",
                kind="index",
            ),
        ),
        edges=(
            DependencyEdge(
                "snapshot",
                "chunk",
                "derived_from",
            ),
            DependencyEdge(
                "chunk",
                "index",
                "indexed_by",
            ),
            DependencyEdge(
                "index",
                "chunk",
                "feeds",
            ),
        ),
    )

    result = analyze_dependency_impact(
        graph=graph,
        changed_node_ids=("snapshot",),
    )

    assert result.affected_node_ids == (
        "chunk",
        "index",
    )


def test_dependency_impact_does_not_cross_changed_roots() -> None:
    graph = DependencyGraph(
        nodes=(
            KnowledgeNode(
                id="root_a",
                kind="snapshot",
            ),
            KnowledgeNode(
                id="root_b",
                kind="snapshot",
            ),
            KnowledgeNode(
                id="index",
                kind="index",
                materialized=True,
            ),
        ),
        edges=(
            DependencyEdge(
                "root_a",
                "root_b",
                "derived_from",
            ),
            DependencyEdge(
                "root_b",
                "index",
                "indexed_by",
            ),
        ),
    )

    result = analyze_dependency_impact(
        graph=graph,
        changed_node_ids=(
            "root_a",
            "root_b",
        ),
    )

    assert result.affected_node_ids == (
        "index",
    )


def test_dependency_impact_rejects_unknown_changed_node() -> None:
    with pytest.raises(
        ValueError,
        match="Unknown changed node",
    ):
        analyze_dependency_impact(
            graph=_production_like_graph(),
            changed_node_ids=("missing",),
        )


def test_dependency_impact_rejects_empty_changed_nodes() -> None:
    with pytest.raises(
        ValueError,
        match="At least one changed node",
    ):
        analyze_dependency_impact(
            graph=_production_like_graph(),
            changed_node_ids=(),
        )


def test_dependency_graph_rejects_unknown_edge_endpoint() -> None:
    graph = DependencyGraph(
        nodes=(
            KnowledgeNode(
                id="snapshot",
                kind="snapshot",
            ),
        ),
        edges=(
            DependencyEdge(
                "snapshot",
                "missing",
                "derived_from",
            ),
        ),
    )

    with pytest.raises(
        ValueError,
        match="unknown downstream node",
    ):
        analyze_dependency_impact(
            graph=graph,
            changed_node_ids=("snapshot",),
        )

def test_dependency_impact_keeps_direct_classification_across_roots() -> None:
    graph = DependencyGraph(
        nodes=(
            KnowledgeNode(
                id="root_a",
                kind="snapshot",
            ),
            KnowledgeNode(
                id="root_b",
                kind="snapshot",
            ),
            KnowledgeNode(
                id="shared_chunk",
                kind="chunk",
                materialized=True,
            ),
            KnowledgeNode(
                id="index",
                kind="index",
                materialized=True,
            ),
        ),
        edges=(
            DependencyEdge(
                "root_a",
                "shared_chunk",
                "derived_from",
            ),
            DependencyEdge(
                "root_b",
                "shared_chunk",
                "derived_from",
            ),
            DependencyEdge(
                "shared_chunk",
                "index",
                "indexed_by",
            ),
        ),
    )

    result = analyze_dependency_impact(
        graph=graph,
        changed_node_ids=(
            "root_a",
            "root_b",
        ),
    )

    assert result.directly_affected_node_ids == (
        "shared_chunk",
    )
    assert result.transitively_affected_node_ids == (
        "index",
    )

    assert {
        (
            path.changed_node_id,
            path.affected_node_id,
        )
        for path in result.affected_paths
    } == {
        ("root_a", "shared_chunk"),
        ("root_a", "index"),
        ("root_b", "shared_chunk"),
        ("root_b", "index"),
    }