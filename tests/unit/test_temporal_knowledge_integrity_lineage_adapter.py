from __future__ import annotations

import pytest

from benchmarks.temporal_knowledge_integrity.dependency_impact import (
    analyze_dependency_impact,
)
from benchmarks.temporal_knowledge_integrity.lineage_adapter import (
    AdaptedRevisionDependency,
    LineageAsset,
    LineageAssetRevision,
    LineageDependency,
    LineageRevisionDependency,
    SnapshotLineageObservation,
    adapt_snapshot_lineage,
)


CONTENT_HASH = "a" * 64


def _observation() -> SnapshotLineageObservation:
    return SnapshotLineageObservation(
        source_external_id="page-001",
        snapshot_external_id="snapshot-002",
        content_hash=CONTENT_HASH,
        assets=(
            LineageAsset(
                external_id="page-001",
                kind="source",
            ),
            LineageAsset(
                external_id="snapshot-002",
                kind="snapshot",
            ),
            LineageAsset(
                external_id="chunk-policy",
                kind="chunk",
                materialized=True,
            ),
            LineageAsset(
                external_id="index-support",
                kind="index",
                materialized=True,
            ),
            LineageAsset(
                external_id="agent-support",
                kind="agent",
                critical=True,
            ),
            LineageAsset(
                external_id="workflow-refund",
                kind="workflow",
                critical=True,
            ),
        ),
        dependencies=(
            LineageDependency(
                upstream_external_id="page-001",
                downstream_external_id="snapshot-002",
                dependency_type="version_of",
            ),
            LineageDependency(
                upstream_external_id="snapshot-002",
                downstream_external_id="chunk-policy",
                dependency_type="derived_from",
            ),
            LineageDependency(
                upstream_external_id="chunk-policy",
                downstream_external_id="index-support",
                dependency_type="indexed_by",
            ),
            LineageDependency(
                upstream_external_id="index-support",
                downstream_external_id="agent-support",
                dependency_type="consumed_by",
            ),
            LineageDependency(
                upstream_external_id="agent-support",
                downstream_external_id="workflow-refund",
                dependency_type="feeds",
            ),
        ),
    )


def test_lineage_adapter_builds_dependency_graph() -> None:
    result = adapt_snapshot_lineage(
        _observation()
    )

    assert result.source_node_id == (
        "source:page-001"
    )
    assert result.snapshot_node_id == (
        "snapshot:snapshot-002"
    )
    assert result.content_hash == CONTENT_HASH

    assert tuple(
        node.id
        for node in result.graph.nodes
    ) == (
        "agent:agent-support",
        "chunk:chunk-policy",
        "index:index-support",
        "snapshot:snapshot-002",
        "source:page-001",
        "workflow:workflow-refund",
    )


def test_lineage_adapter_preserves_asset_semantics() -> None:
    result = adapt_snapshot_lineage(
        _observation()
    )

    nodes_by_id = {
        node.id: node
        for node in result.graph.nodes
    }

    assert nodes_by_id[
        "chunk:chunk-policy"
    ].materialized
    assert nodes_by_id[
        "index:index-support"
    ].materialized
    assert nodes_by_id[
        "agent:agent-support"
    ].critical
    assert nodes_by_id[
        "workflow:workflow-refund"
    ].critical


def test_adapted_lineage_drives_dependency_impact() -> None:
    adapted = adapt_snapshot_lineage(
        _observation()
    )

    result = analyze_dependency_impact(
        graph=adapted.graph,
        changed_node_ids=(
            adapted.snapshot_node_id,
        ),
    )

    assert result.affected_node_ids == (
        "agent:agent-support",
        "chunk:chunk-policy",
        "index:index-support",
        "workflow:workflow-refund",
    )

    assert result.invalidation_candidate_ids == (
        "chunk:chunk-policy",
        "index:index-support",
    )

    assert result.critical_node_ids == (
        "agent:agent-support",
        "workflow:workflow-refund",
    )


def test_lineage_adapter_normalizes_identifiers_and_hash() -> None:
    observation = SnapshotLineageObservation(
        source_external_id=" page ",
        snapshot_external_id=" snapshot ",
        content_hash="A" * 64,
        assets=(
            LineageAsset(
                external_id=" page ",
                kind="source",
            ),
            LineageAsset(
                external_id=" snapshot ",
                kind="snapshot",
            ),
        ),
        dependencies=(
            LineageDependency(
                upstream_external_id=" page ",
                downstream_external_id=" snapshot ",
                dependency_type=" version_of ",
            ),
        ),
    )

    result = adapt_snapshot_lineage(
        observation
    )

    assert result.source_node_id == "source:page"
    assert result.snapshot_node_id == (
        "snapshot:snapshot"
    )
    assert result.content_hash == "a" * 64

    assert result.graph.edges[0].dependency_type == (
        "version_of"
    )


def test_lineage_adapter_is_deterministic() -> None:
    observation = _observation()

    reversed_observation = (
        SnapshotLineageObservation(
            source_external_id=(
                observation.source_external_id
            ),
            snapshot_external_id=(
                observation.snapshot_external_id
            ),
            content_hash=(
                observation.content_hash
            ),
            assets=tuple(
                reversed(
                    observation.assets
                )
            ),
            dependencies=tuple(
                reversed(
                    observation.dependencies
                )
            ),
        )
    )

    assert adapt_snapshot_lineage(
        observation
    ) == adapt_snapshot_lineage(
        reversed_observation
    )


def test_lineage_adapter_rejects_duplicate_asset_identity() -> None:
    observation = SnapshotLineageObservation(
        source_external_id="page",
        snapshot_external_id="snapshot",
        content_hash=CONTENT_HASH,
        assets=(
            LineageAsset(
                external_id="page",
                kind="source",
            ),
            LineageAsset(
                external_id="snapshot",
                kind="snapshot",
            ),
            LineageAsset(
                external_id="chunk",
                kind="chunk",
            ),
            LineageAsset(
                external_id="chunk",
                kind="chunk",
            ),
        ),
        dependencies=(),
    )

    with pytest.raises(
        ValueError,
        match="Duplicate lineage asset",
    ):
        adapt_snapshot_lineage(
            observation
        )


def test_lineage_adapter_rejects_missing_source_asset() -> None:
    observation = SnapshotLineageObservation(
        source_external_id="missing",
        snapshot_external_id="snapshot",
        content_hash=CONTENT_HASH,
        assets=(
            LineageAsset(
                external_id="snapshot",
                kind="snapshot",
            ),
        ),
        dependencies=(),
    )

    with pytest.raises(
        ValueError,
        match="source_external_id must reference",
    ):
        adapt_snapshot_lineage(
            observation
        )


def test_lineage_adapter_rejects_wrong_snapshot_kind() -> None:
    observation = SnapshotLineageObservation(
        source_external_id="page",
        snapshot_external_id="snapshot",
        content_hash=CONTENT_HASH,
        assets=(
            LineageAsset(
                external_id="page",
                kind="source",
            ),
            LineageAsset(
                external_id="snapshot",
                kind="chunk",
            ),
        ),
        dependencies=(),
    )

    with pytest.raises(
        ValueError,
        match="must reference a snapshot asset",
    ):
        adapt_snapshot_lineage(
            observation
        )


@pytest.mark.parametrize(
    "content_hash",
    (
        "",
        "a" * 63,
        "a" * 65,
        "g" * 64,
    ),
)
def test_lineage_adapter_rejects_invalid_content_hash(
    content_hash: str,
) -> None:
    observation = SnapshotLineageObservation(
        source_external_id="page",
        snapshot_external_id="snapshot",
        content_hash=content_hash,
        assets=(
            LineageAsset(
                external_id="page",
                kind="source",
            ),
            LineageAsset(
                external_id="snapshot",
                kind="snapshot",
            ),
        ),
        dependencies=(),
    )

    with pytest.raises(
        ValueError,
        match="content_hash",
    ):
        adapt_snapshot_lineage(
            observation
        )


def test_lineage_adapter_rejects_unknown_dependency_endpoint() -> None:
    observation = SnapshotLineageObservation(
        source_external_id="page",
        snapshot_external_id="snapshot",
        content_hash=CONTENT_HASH,
        assets=(
            LineageAsset(
                external_id="page",
                kind="source",
            ),
            LineageAsset(
                external_id="snapshot",
                kind="snapshot",
            ),
        ),
        dependencies=(
            LineageDependency(
                upstream_external_id="snapshot",
                downstream_external_id="missing",
                dependency_type="derived_from",
            ),
        ),
    )

    with pytest.raises(
        ValueError,
        match="unknown downstream asset",
    ):
        adapt_snapshot_lineage(
            observation
        )


def test_lineage_adapter_rejects_duplicate_dependency() -> None:
    dependency = LineageDependency(
        upstream_external_id="page",
        downstream_external_id="snapshot",
        dependency_type="version_of",
    )

    observation = SnapshotLineageObservation(
        source_external_id="page",
        snapshot_external_id="snapshot",
        content_hash=CONTENT_HASH,
        assets=(
            LineageAsset(
                external_id="page",
                kind="source",
            ),
            LineageAsset(
                external_id="snapshot",
                kind="snapshot",
            ),
        ),
        dependencies=(
            dependency,
            dependency,
        ),
    )

    with pytest.raises(
        ValueError,
        match="Duplicate lineage dependency",
    ):
        adapt_snapshot_lineage(
            observation
        )

def test_adapts_revision_level_dependencies_deterministically() -> None:
    observation = SnapshotLineageObservation(
        source_external_id="page-policy",
        snapshot_external_id="snapshot-v2",
        content_hash="b" * 64,
        assets=(
            LineageAsset(
                external_id="page-policy",
                kind="source",
            ),
            LineageAsset(
                external_id="snapshot-v1",
                kind="snapshot",
            ),
            LineageAsset(
                external_id="snapshot-v2",
                kind="snapshot",
            ),
            LineageAsset(
                external_id="chunk-policy",
                kind="chunk",
                materialized=True,
            ),
            LineageAsset(
                external_id="index-policy",
                kind="index",
                materialized=True,
            ),
            LineageAsset(
                external_id="agent-policy",
                kind="agent",
                materialized=True,
                critical=True,
            ),
        ),
        dependencies=(),
        asset_revisions=(
            LineageAssetRevision(
                asset_external_id="chunk-policy",
                revision_external_id="chunk-policy@v2",
                derived_from_snapshot_external_id="snapshot-v2",
            ),
            LineageAssetRevision(
                asset_external_id="index-policy",
                revision_external_id="index-policy@v1",
                derived_from_snapshot_external_id="snapshot-v1",
            ),
            LineageAssetRevision(
                asset_external_id="agent-policy",
                revision_external_id="agent-policy@v1",
                derived_from_snapshot_external_id="snapshot-v1",
            ),
        ),
        revision_dependencies=(
            LineageRevisionDependency(
                upstream_asset_external_id="index-policy",
                upstream_revision_external_id="index-policy@v1",
                downstream_asset_external_id="agent-policy",
                downstream_revision_external_id="agent-policy@v1",
                dependency_type="consumes",
            ),
            LineageRevisionDependency(
                upstream_asset_external_id="chunk-policy",
                upstream_revision_external_id="chunk-policy@v1",
                downstream_asset_external_id="index-policy",
                downstream_revision_external_id="index-policy@v1",
                dependency_type="contains",
            ),
        ),
    )

    adapted = adapt_snapshot_lineage(observation)

    assert adapted.revision_dependencies == (
        AdaptedRevisionDependency(
            upstream_asset_node_id="chunk:chunk-policy",
            upstream_revision_external_id="chunk-policy@v1",
            downstream_asset_node_id="index:index-policy",
            downstream_revision_external_id="index-policy@v1",
            dependency_type="contains",
        ),
        AdaptedRevisionDependency(
            upstream_asset_node_id="index:index-policy",
            upstream_revision_external_id="index-policy@v1",
            downstream_asset_node_id="agent:agent-policy",
            downstream_revision_external_id="agent-policy@v1",
            dependency_type="consumes",
        ),
    )

def test_revision_dependency_rejects_unknown_asset() -> None:
    observation = SnapshotLineageObservation(
        source_external_id="page-policy",
        snapshot_external_id="snapshot-v2",
        content_hash="b" * 64,
        assets=(
            LineageAsset(
                external_id="page-policy",
                kind="source",
            ),
            LineageAsset(
                external_id="snapshot-v2",
                kind="snapshot",
            ),
            LineageAsset(
                external_id="index-policy",
                kind="index",
                materialized=True,
            ),
        ),
        dependencies=(),
        asset_revisions=(
            LineageAssetRevision(
                asset_external_id="index-policy",
                revision_external_id="index-policy@v2",
                derived_from_snapshot_external_id="snapshot-v2",
            ),
        ),
        revision_dependencies=(
            LineageRevisionDependency(
                upstream_asset_external_id="missing-chunk",
                upstream_revision_external_id="chunk-policy@v1",
                downstream_asset_external_id="index-policy",
                downstream_revision_external_id="index-policy@v2",
                dependency_type="contains",
            ),
        ),
    )

    with pytest.raises(
        ValueError,
        match="Revision dependency references unknown upstream asset",
    ):
        adapt_snapshot_lineage(observation)


def test_revision_dependency_rejects_downstream_revision_mismatch() -> None:
    observation = SnapshotLineageObservation(
        source_external_id="page-policy",
        snapshot_external_id="snapshot-v2",
        content_hash="b" * 64,
        assets=(
            LineageAsset(
                external_id="page-policy",
                kind="source",
            ),
            LineageAsset(
                external_id="snapshot-v2",
                kind="snapshot",
            ),
            LineageAsset(
                external_id="chunk-policy",
                kind="chunk",
                materialized=True,
            ),
            LineageAsset(
                external_id="index-policy",
                kind="index",
                materialized=True,
            ),
        ),
        dependencies=(),
        asset_revisions=(
            LineageAssetRevision(
                asset_external_id="chunk-policy",
                revision_external_id="chunk-policy@v2",
                derived_from_snapshot_external_id="snapshot-v2",
            ),
            LineageAssetRevision(
                asset_external_id="index-policy",
                revision_external_id="index-policy@v2",
                derived_from_snapshot_external_id="snapshot-v2",
            ),
        ),
        revision_dependencies=(
            LineageRevisionDependency(
                upstream_asset_external_id="chunk-policy",
                upstream_revision_external_id="chunk-policy@v2",
                downstream_asset_external_id="index-policy",
                downstream_revision_external_id="index-policy@v1",
                dependency_type="contains",
            ),
        ),
    )

    with pytest.raises(
        ValueError,
        match=(
            "Revision dependency downstream revision "
            "must match the observed asset revision"
        ),
    ):
        adapt_snapshot_lineage(observation)


def test_revision_dependency_accepts_historical_upstream_revision() -> None:
    observation = SnapshotLineageObservation(
        source_external_id="page-policy",
        snapshot_external_id="snapshot-v2",
        content_hash="b" * 64,
        assets=(
            LineageAsset(
                external_id="page-policy",
                kind="source",
            ),
            LineageAsset(
                external_id="snapshot-v2",
                kind="snapshot",
            ),
            LineageAsset(
                external_id="chunk-policy",
                kind="chunk",
                materialized=True,
            ),
            LineageAsset(
                external_id="index-policy",
                kind="index",
                materialized=True,
            ),
        ),
        dependencies=(),
        asset_revisions=(
            LineageAssetRevision(
                asset_external_id="chunk-policy",
                revision_external_id="chunk-policy@v2",
                derived_from_snapshot_external_id="snapshot-v2",
            ),
            LineageAssetRevision(
                asset_external_id="index-policy",
                revision_external_id="index-policy@v1",
                derived_from_snapshot_external_id="snapshot-v2",
            ),
        ),
        revision_dependencies=(
            LineageRevisionDependency(
                upstream_asset_external_id="chunk-policy",
                upstream_revision_external_id="chunk-policy@v1",
                downstream_asset_external_id="index-policy",
                downstream_revision_external_id="index-policy@v1",
                dependency_type="contains",
            ),
        ),
    )

    adapted = adapt_snapshot_lineage(observation)

    assert (
        adapted.revision_dependencies[0]
        .upstream_revision_external_id
        == "chunk-policy@v1"
    )