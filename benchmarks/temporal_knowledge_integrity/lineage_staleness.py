from __future__ import annotations

from dataclasses import dataclass

from benchmarks.temporal_knowledge_integrity.dependency_impact import (
    ImpactAnalysis,
    analyze_dependency_impact,
)
from benchmarks.temporal_knowledge_integrity.lineage_adapter import (
    AdaptedSnapshotLineage,
)


@dataclass(frozen=True)
class LineageStalenessAnalysis:
    previous_snapshot_node_id: str
    current_snapshot_node_id: str
    stale_node_ids: tuple[str, ...]
    fresh_node_ids: tuple[str, ...]
    unknown_node_ids: tuple[str, ...]
    stale_materialized_node_ids: tuple[str, ...]
    exposed_critical_node_ids: tuple[str, ...]
    impact: ImpactAnalysis


def analyze_lineage_staleness(
    *,
    previous: AdaptedSnapshotLineage,
    current: AdaptedSnapshotLineage,
) -> LineageStalenessAnalysis:
    if (
        previous.source_node_id
        != current.source_node_id
    ):
        raise ValueError(
            "Lineage observations must reference "
            "the same source."
        )

    if (
        previous.snapshot_node_id
        == current.snapshot_node_id
    ):
        raise ValueError(
            "Previous and current observations must "
            "reference different snapshots."
        )

    if (
        previous.content_hash
        == current.content_hash
    ):
        raise ValueError(
            "Previous and current snapshots must "
            "contain different content."
        )

    previous_nodes_by_id = {
        node.id: node
        for node in previous.graph.nodes
    }
    current_nodes_by_id = {
        node.id: node
        for node in current.graph.nodes
    }

    if (
        previous.snapshot_node_id
        not in previous_nodes_by_id
    ):
        raise ValueError(
            "Previous snapshot node is missing "
            "from its lineage graph."
        )

    if (
        current.snapshot_node_id
        not in current_nodes_by_id
    ):
        raise ValueError(
            "Current snapshot node is missing "
            "from its lineage graph."
        )

    impact = analyze_dependency_impact(
        graph=previous.graph,
        changed_node_ids=(
            previous.snapshot_node_id,
        ),
    )

    current_revisions_by_node_id = {
        revision.asset_node_id: revision
        for revision in current.asset_revisions
    }

    current_affected_node_ids = tuple(
        node_id
        for node_id in impact.affected_node_ids
        if node_id in current_nodes_by_id
    )

    stale_node_ids: list[str] = []
    fresh_node_ids: list[str] = []
    unknown_node_ids: list[str] = []

    for node_id in current_affected_node_ids:
        revision = current_revisions_by_node_id.get(
            node_id
        )

        if revision is None:
            unknown_node_ids.append(node_id)
            continue

        if (
            revision.derived_from_snapshot_node_id
            == previous.snapshot_node_id
        ):
            stale_node_ids.append(node_id)
            continue

        if (
            revision.derived_from_snapshot_node_id
            == current.snapshot_node_id
        ):
            fresh_node_ids.append(node_id)
            continue

        unknown_node_ids.append(node_id)

    stale_node_ids_tuple = tuple(
        stale_node_ids
    )
    fresh_node_ids_tuple = tuple(
        fresh_node_ids
    )
    unknown_node_ids_tuple = tuple(
        unknown_node_ids
    )

    stale_node_id_set = set(
        stale_node_ids_tuple
    )

    stale_materialized_node_ids = tuple(
        node_id
        for node_id
        in impact.invalidation_candidate_ids
        if node_id in stale_node_id_set
    )

    exposed_critical_node_ids = tuple(
        node_id
        for node_id
        in impact.critical_node_ids
        if node_id in stale_node_id_set
    )

    return LineageStalenessAnalysis(
        previous_snapshot_node_id=(
            previous.snapshot_node_id
        ),
        current_snapshot_node_id=(
            current.snapshot_node_id
        ),
        stale_node_ids=stale_node_ids_tuple,
        fresh_node_ids=fresh_node_ids_tuple,
        unknown_node_ids=unknown_node_ids_tuple,
        stale_materialized_node_ids=(
            stale_materialized_node_ids
        ),
        exposed_critical_node_ids=(
            exposed_critical_node_ids
        ),
        impact=impact,
    )