from __future__ import annotations

import pytest

from benchmarks.temporal_knowledge_integrity.lineage_adapter import (
    LineageAsset,
    LineageAssetRevision,
    LineageDependency,
    SnapshotLineageObservation,
    adapt_snapshot_lineage,
)
from benchmarks.temporal_knowledge_integrity.lineage_staleness import (
    analyze_lineage_staleness,
)


OLD_HASH = "a" * 64
NEW_HASH = "b" * 64


def _observation(
    *,
    snapshot_id: str,
    content_hash: str,
    include_downstream: bool = True,
    derived_from_snapshot_id: str | None = None,
) -> SnapshotLineageObservation:
    assets = [
        LineageAsset(
            external_id="page-policy",
            kind="source",
        ),
        LineageAsset(
            external_id=snapshot_id,
            kind="snapshot",
        ),
    ]

    dependencies = [
        LineageDependency(
            upstream_external_id="page-policy",
            downstream_external_id=snapshot_id,
            dependency_type="version_of",
        ),
    ]

    asset_revisions: list[
        LineageAssetRevision
    ] = []

    if include_downstream:
        assets.extend(
            (
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
                    critical=True,
                ),
                LineageAsset(
                    external_id="workflow-approval",
                    kind="workflow",
                    critical=True,
                ),
            )
        )

        dependencies.extend(
            (
                LineageDependency(
                    upstream_external_id=snapshot_id,
                    downstream_external_id="chunk-policy",
                    dependency_type="derived_from",
                ),
                LineageDependency(
                    upstream_external_id="chunk-policy",
                    downstream_external_id="index-policy",
                    dependency_type="indexed_by",
                ),
                LineageDependency(
                    upstream_external_id="index-policy",
                    downstream_external_id="agent-policy",
                    dependency_type="consumed_by",
                ),
                LineageDependency(
                    upstream_external_id="agent-policy",
                    downstream_external_id="workflow-approval",
                    dependency_type="feeds",
                ),
            )
        )

        provenance_snapshot_id = (
            derived_from_snapshot_id
            if derived_from_snapshot_id is not None
            else snapshot_id
        )

        if provenance_snapshot_id != snapshot_id:
            assets.append(
                LineageAsset(
                    external_id=provenance_snapshot_id,
                    kind="snapshot",
                )
            )

        asset_revisions.extend(
            (
                LineageAssetRevision(
                    asset_external_id="chunk-policy",
                    revision_external_id=(
                        f"chunk-policy@{provenance_snapshot_id}"
                    ),
                    derived_from_snapshot_external_id=(
                        provenance_snapshot_id
                    ),
                ),
                LineageAssetRevision(
                    asset_external_id="index-policy",
                    revision_external_id=(
                        f"index-policy@{provenance_snapshot_id}"
                    ),
                    derived_from_snapshot_external_id=(
                        provenance_snapshot_id
                    ),
                ),
                LineageAssetRevision(
                    asset_external_id="agent-policy",
                    revision_external_id=(
                        f"agent-policy@{provenance_snapshot_id}"
                    ),
                    derived_from_snapshot_external_id=(
                        provenance_snapshot_id
                    ),
                ),
                LineageAssetRevision(
                    asset_external_id="workflow-approval",
                    revision_external_id=(
                        f"workflow-approval@{provenance_snapshot_id}"
                    ),
                    derived_from_snapshot_external_id=(
                        provenance_snapshot_id
                    ),
                ),
            )
        )

    return SnapshotLineageObservation(
        source_external_id="page-policy",
        snapshot_external_id=snapshot_id,
        content_hash=content_hash,
        assets=tuple(assets),
        dependencies=tuple(dependencies),
        asset_revisions=tuple(asset_revisions),
    )


def test_staleness_detects_reused_derived_state() -> None:
    previous = adapt_snapshot_lineage(
        _observation(
            snapshot_id="snapshot-v1",
            content_hash=OLD_HASH,
        )
    )
    current = adapt_snapshot_lineage(
        _observation(
            snapshot_id="snapshot-v2",
            content_hash=NEW_HASH,
            derived_from_snapshot_id="snapshot-v1",
        )
    )

    result = analyze_lineage_staleness(
        previous=previous,
        current=current,
    )

    assert result.stale_node_ids == (
        "agent:agent-policy",
        "chunk:chunk-policy",
        "index:index-policy",
        "workflow:workflow-approval",
    )

    assert (
        result.stale_materialized_node_ids
        == (
            "chunk:chunk-policy",
            "index:index-policy",
        )
    )

    assert (
        result.exposed_critical_node_ids
        == (
            "agent:agent-policy",
            "workflow:workflow-approval",
        )
    )


def test_staleness_does_not_require_topology_change() -> None:
    previous = adapt_snapshot_lineage(
        _observation(
            snapshot_id="snapshot-v1",
            content_hash=OLD_HASH,
        )
    )
    current = adapt_snapshot_lineage(
        _observation(
            snapshot_id="snapshot-v2",
            content_hash=NEW_HASH,
            derived_from_snapshot_id="snapshot-v1",
        )
    )

    previous_edges = {
        (
            edge.upstream_node_id,
            edge.downstream_node_id,
            edge.dependency_type,
        )
        for edge in previous.graph.edges
        if not (
            edge.upstream_node_id.startswith(
                "source:"
            )
            or edge.upstream_node_id.startswith(
                "snapshot:"
            )
        )
    }

    current_edges = {
        (
            edge.upstream_node_id,
            edge.downstream_node_id,
            edge.dependency_type,
        )
        for edge in current.graph.edges
        if not (
            edge.upstream_node_id.startswith(
                "source:"
            )
            or edge.upstream_node_id.startswith(
                "snapshot:"
            )
        )
    }

    assert previous_edges == current_edges

    result = analyze_lineage_staleness(
        previous=previous,
        current=current,
    )

    assert result.stale_materialized_node_ids
    assert result.exposed_critical_node_ids


def test_staleness_excludes_assets_absent_from_current_observation() -> None:
    previous = adapt_snapshot_lineage(
        _observation(
            snapshot_id="snapshot-v1",
            content_hash=OLD_HASH,
        )
    )
    current = adapt_snapshot_lineage(
        _observation(
            snapshot_id="snapshot-v2",
            content_hash=NEW_HASH,
            include_downstream=False,
        )
    )

    result = analyze_lineage_staleness(
        previous=previous,
        current=current,
    )

    assert result.stale_node_ids == ()
    assert (
        result.stale_materialized_node_ids
        == ()
    )
    assert (
        result.exposed_critical_node_ids
        == ()
    )


def test_staleness_preserves_previous_snapshot_provenance() -> None:
    previous = adapt_snapshot_lineage(
        _observation(
            snapshot_id="snapshot-v1",
            content_hash=OLD_HASH,
        )
    )
    current = adapt_snapshot_lineage(
        _observation(
            snapshot_id="snapshot-v2",
            content_hash=NEW_HASH,
        )
    )

    result = analyze_lineage_staleness(
        previous=previous,
        current=current,
    )

    workflow_path = next(
        path
        for path in result.impact.affected_paths
        if path.affected_node_id
        == "workflow:workflow-approval"
    )

    assert workflow_path.node_ids == (
        "snapshot:snapshot-v1",
        "chunk:chunk-policy",
        "index:index-policy",
        "agent:agent-policy",
        "workflow:workflow-approval",
    )


def test_staleness_rejects_different_sources() -> None:
    previous = adapt_snapshot_lineage(
        _observation(
            snapshot_id="snapshot-v1",
            content_hash=OLD_HASH,
        )
    )

    current_observation = _observation(
        snapshot_id="snapshot-v2",
        content_hash=NEW_HASH,
    )

    current_observation = (
        SnapshotLineageObservation(
            source_external_id="other-page",
            snapshot_external_id=(
                current_observation.snapshot_external_id
            ),
            content_hash=(
                current_observation.content_hash
            ),
            assets=(
                LineageAsset(
                    external_id="other-page",
                    kind="source",
                ),
                *tuple(
                    asset
                    for asset
                    in current_observation.assets
                    if asset.kind != "source"
                ),
            ),
            dependencies=tuple(
                dependency
                for dependency
                in current_observation.dependencies
                if dependency.dependency_type
                != "version_of"
            ),
        )
    )

    current = adapt_snapshot_lineage(
        current_observation
    )

    with pytest.raises(
        ValueError,
        match="same source",
    ):
        analyze_lineage_staleness(
            previous=previous,
            current=current,
        )


def test_staleness_rejects_same_snapshot() -> None:
    previous = adapt_snapshot_lineage(
        _observation(
            snapshot_id="snapshot-v1",
            content_hash=OLD_HASH,
        )
    )
    current = adapt_snapshot_lineage(
        _observation(
            snapshot_id="snapshot-v1",
            content_hash=NEW_HASH,
        )
    )

    with pytest.raises(
        ValueError,
        match="different snapshots",
    ):
        analyze_lineage_staleness(
            previous=previous,
            current=current,
        )


def test_staleness_rejects_unchanged_content() -> None:
    previous = adapt_snapshot_lineage(
        _observation(
            snapshot_id="snapshot-v1",
            content_hash=OLD_HASH,
        )
    )
    current = adapt_snapshot_lineage(
        _observation(
            snapshot_id="snapshot-v2",
            content_hash=OLD_HASH,
        )
    )

    with pytest.raises(
        ValueError,
        match="different content",
    ):
        analyze_lineage_staleness(
            previous=previous,
            current=current,
        )

def test_staleness_cannot_distinguish_rebuilt_asset_without_version_binding() -> None:
    previous = adapt_snapshot_lineage(
        _observation(
            snapshot_id="snapshot-v1",
            content_hash=OLD_HASH,
        )
    )
    current = adapt_snapshot_lineage(
        _observation(
            snapshot_id="snapshot-v2",
            content_hash=NEW_HASH,
        )
    )

    result = analyze_lineage_staleness(
        previous=previous,
        current=current,
    )

    assert (
        "chunk:chunk-policy"
        not in result.stale_node_ids
    )

def test_staleness_distinguishes_rebuilt_asset_with_version_binding() -> None:
    previous_observation = _observation(
        snapshot_id="snapshot-v1",
        content_hash=OLD_HASH,
    )
    previous = adapt_snapshot_lineage(
        SnapshotLineageObservation(
            source_external_id=(
                previous_observation.source_external_id
            ),
            snapshot_external_id=(
                previous_observation.snapshot_external_id
            ),
            content_hash=(
                previous_observation.content_hash
            ),
            assets=previous_observation.assets,
            dependencies=(
                previous_observation.dependencies
            ),
            asset_revisions=(
                LineageAssetRevision(
                    asset_external_id="chunk-policy",
                    revision_external_id="chunk-revision-v1",
                    derived_from_snapshot_external_id=(
                        "snapshot-v1"
                    ),
                ),
            ),
        )
    )

    current_observation = _observation(
        snapshot_id="snapshot-v2",
        content_hash=NEW_HASH,
    )
    current = adapt_snapshot_lineage(
        SnapshotLineageObservation(
            source_external_id=(
                current_observation.source_external_id
            ),
            snapshot_external_id=(
                current_observation.snapshot_external_id
            ),
            content_hash=(
                current_observation.content_hash
            ),
            assets=current_observation.assets,
            dependencies=(
                current_observation.dependencies
            ),
            asset_revisions=(
                LineageAssetRevision(
                    asset_external_id="chunk-policy",
                    revision_external_id="chunk-revision-v2",
                    derived_from_snapshot_external_id=(
                        "snapshot-v2"
                    ),
                ),
            ),
        )
    )

    result = analyze_lineage_staleness(
        previous=previous,
        current=current,
    )

    assert (
        "chunk:chunk-policy"
        not in result.stale_node_ids
    )

def test_staleness_classifies_current_snapshot_bindings_as_fresh() -> None:
    previous = adapt_snapshot_lineage(
        _observation(
            snapshot_id="snapshot-v1",
            content_hash=OLD_HASH,
        )
    )
    current = adapt_snapshot_lineage(
        _observation(
            snapshot_id="snapshot-v2",
            content_hash=NEW_HASH,
        )
    )

    result = analyze_lineage_staleness(
        previous=previous,
        current=current,
    )

    assert result.stale_node_ids == ()
    assert result.fresh_node_ids == (
        "agent:agent-policy",
        "chunk:chunk-policy",
        "index:index-policy",
        "workflow:workflow-approval",
    )
    assert result.unknown_node_ids == ()


def test_staleness_classifies_missing_provenance_as_unknown() -> None:
    previous = adapt_snapshot_lineage(
        _observation(
            snapshot_id="snapshot-v1",
            content_hash=OLD_HASH,
        )
    )

    current_observation = _observation(
        snapshot_id="snapshot-v2",
        content_hash=NEW_HASH,
    )

    current = adapt_snapshot_lineage(
        SnapshotLineageObservation(
            source_external_id=(
                current_observation.source_external_id
            ),
            snapshot_external_id=(
                current_observation.snapshot_external_id
            ),
            content_hash=(
                current_observation.content_hash
            ),
            assets=current_observation.assets,
            dependencies=(
                current_observation.dependencies
            ),
            asset_revisions=(),
        )
    )

    result = analyze_lineage_staleness(
        previous=previous,
        current=current,
    )

    assert result.stale_node_ids == ()
    assert result.fresh_node_ids == ()
    assert result.unknown_node_ids == (
        "agent:agent-policy",
        "chunk:chunk-policy",
        "index:index-policy",
        "workflow:workflow-approval",
    )


def test_staleness_classifies_unrelated_snapshot_binding_as_unknown() -> None:
    previous = adapt_snapshot_lineage(
        _observation(
            snapshot_id="snapshot-v1",
            content_hash=OLD_HASH,
        )
    )

    current_observation = _observation(
        snapshot_id="snapshot-v2",
        content_hash=NEW_HASH,
        derived_from_snapshot_id="snapshot-v0",
    )

    current = adapt_snapshot_lineage(
        current_observation
    )

    result = analyze_lineage_staleness(
        previous=previous,
        current=current,
    )

    assert result.stale_node_ids == ()
    assert result.fresh_node_ids == ()
    assert result.unknown_node_ids == (
        "agent:agent-policy",
        "chunk:chunk-policy",
        "index:index-policy",
        "workflow:workflow-approval",
    )