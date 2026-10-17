from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Iterable, Literal


KnowledgeNodeKind = Literal[
    "source",
    "snapshot",
    "chunk",
    "index",
    "agent",
    "workflow",
]


@dataclass(frozen=True)
class KnowledgeNode:
    id: str
    kind: KnowledgeNodeKind
    materialized: bool = False
    critical: bool = False


@dataclass(frozen=True)
class DependencyEdge:
    upstream_node_id: str
    downstream_node_id: str
    dependency_type: str


@dataclass(frozen=True)
class DependencyGraph:
    nodes: tuple[KnowledgeNode, ...]
    edges: tuple[DependencyEdge, ...]


@dataclass(frozen=True)
class DependencyPath:
    changed_node_id: str
    affected_node_id: str
    node_ids: tuple[str, ...]


@dataclass(frozen=True)
class ImpactAnalysis:
    changed_node_ids: tuple[str, ...]
    directly_affected_node_ids: tuple[str, ...]
    transitively_affected_node_ids: tuple[str, ...]
    affected_node_ids: tuple[str, ...]
    critical_node_ids: tuple[str, ...]
    invalidation_candidate_ids: tuple[str, ...]
    affected_paths: tuple[DependencyPath, ...]


def _validate_graph(
    graph: DependencyGraph,
) -> dict[str, KnowledgeNode]:
    nodes_by_id: dict[str, KnowledgeNode] = {}

    for node in graph.nodes:
        if not node.id:
            raise ValueError(
                "Knowledge node id must not be empty."
            )

        if node.id in nodes_by_id:
            raise ValueError(
                f"Duplicate knowledge node id: {node.id!r}."
            )

        nodes_by_id[node.id] = node

    seen_edges: set[tuple[str, str, str]] = set()

    for edge in graph.edges:
        if edge.upstream_node_id not in nodes_by_id:
            raise ValueError(
                "Dependency edge references unknown upstream node: "
                f"{edge.upstream_node_id!r}."
            )

        if edge.downstream_node_id not in nodes_by_id:
            raise ValueError(
                "Dependency edge references unknown downstream node: "
                f"{edge.downstream_node_id!r}."
            )

        if edge.upstream_node_id == edge.downstream_node_id:
            raise ValueError(
                "Dependency graph does not allow self edges."
            )

        if not edge.dependency_type:
            raise ValueError(
                "Dependency type must not be empty."
            )

        edge_key = (
            edge.upstream_node_id,
            edge.downstream_node_id,
            edge.dependency_type,
        )

        if edge_key in seen_edges:
            raise ValueError(
                f"Duplicate dependency edge: {edge_key!r}."
            )

        seen_edges.add(edge_key)

    return nodes_by_id


def _build_adjacency(
    graph: DependencyGraph,
    *,
    node_ids: Iterable[str],
) -> dict[str, tuple[str, ...]]:
    adjacency_sets: dict[str, set[str]] = {
        node_id: set()
        for node_id in node_ids
    }

    for edge in graph.edges:
        adjacency_sets[
            edge.upstream_node_id
        ].add(
            edge.downstream_node_id
        )

    return {
        node_id: tuple(
            sorted(downstream_node_ids)
        )
        for node_id, downstream_node_ids
        in adjacency_sets.items()
    }


def _shortest_paths_from_root(
    *,
    root_node_id: str,
    adjacency: dict[str, tuple[str, ...]],
    changed_node_ids: set[str],
) -> dict[str, tuple[str, ...]]:
    paths_by_node_id: dict[
        str,
        tuple[str, ...],
    ] = {}

    visited = {
        root_node_id,
    }

    queue: deque[
        tuple[str, tuple[str, ...]]
    ] = deque(
        (
            downstream_node_id,
            (
                root_node_id,
                downstream_node_id,
            ),
        )
        for downstream_node_id
        in adjacency[root_node_id]
        if downstream_node_id
        not in changed_node_ids
    )

    while queue:
        node_id, path = queue.popleft()

        if node_id in visited:
            continue

        visited.add(node_id)
        paths_by_node_id[node_id] = path

        for downstream_node_id in adjacency[node_id]:
            if (
                downstream_node_id
                in changed_node_ids
            ):
                continue

            if downstream_node_id in visited:
                continue

            queue.append(
                (
                    downstream_node_id,
                    (
                        *path,
                        downstream_node_id,
                    ),
                )
            )

    return paths_by_node_id


def analyze_dependency_impact(
    *,
    graph: DependencyGraph,
    changed_node_ids: Iterable[str],
) -> ImpactAnalysis:
    nodes_by_id = _validate_graph(graph)

    changed = tuple(
        dict.fromkeys(
            changed_node_ids
        )
    )

    if not changed:
        raise ValueError(
            "At least one changed node is required."
        )

    for node_id in changed:
        if node_id not in nodes_by_id:
            raise ValueError(
                f"Unknown changed node: {node_id!r}."
            )

    adjacency = _build_adjacency(
        graph,
        node_ids=nodes_by_id,
    )

    changed_set = set(changed)

    direct: set[str] = set()
    affected: set[str] = set()
    paths: list[DependencyPath] = []

    for root_node_id in changed:
        root_paths = _shortest_paths_from_root(
            root_node_id=root_node_id,
            adjacency=adjacency,
            changed_node_ids=changed_set,
        )

        for (
            affected_node_id,
            node_path,
        ) in root_paths.items():
            affected.add(
                affected_node_id
            )

            if len(node_path) == 2:
                direct.add(
                    affected_node_id
                )

            paths.append(
                DependencyPath(
                    changed_node_id=root_node_id,
                    affected_node_id=(
                        affected_node_id
                    ),
                    node_ids=node_path,
                )
            )

    transitively_affected = (
        affected - direct
    )

    critical = {
        node_id
        for node_id in affected
        if nodes_by_id[node_id].critical
    }

    invalidation_candidates = {
        node_id
        for node_id in affected
        if nodes_by_id[node_id].materialized
    }

    return ImpactAnalysis(
        changed_node_ids=changed,
        directly_affected_node_ids=tuple(
            sorted(direct)
        ),
        transitively_affected_node_ids=tuple(
            sorted(
                transitively_affected
            )
        ),
        affected_node_ids=tuple(
            sorted(affected)
        ),
        critical_node_ids=tuple(
            sorted(critical)
        ),
        invalidation_candidate_ids=tuple(
            sorted(
                invalidation_candidates
            )
        ),
        affected_paths=tuple(
            sorted(
                paths,
                key=lambda path: (
                    path.changed_node_id,
                    path.affected_node_id,
                    path.node_ids,
                ),
            )
        ),
    )