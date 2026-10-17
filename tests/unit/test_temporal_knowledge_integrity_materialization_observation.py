from __future__ import annotations

import pytest

from benchmarks.temporal_knowledge_integrity.lineage_adapter import (
    LineageAsset,
    LineageAssetRevision,
    LineageDependency,
    SnapshotLineageObservation,
    adapt_snapshot_lineage,
    LineageRevisionDependency,
    AdaptedRevisionDependency,
)
from benchmarks.temporal_knowledge_integrity.lineage_staleness import (
    analyze_lineage_staleness,
)
from benchmarks.temporal_knowledge_integrity.materialization_observation import (
    MaterializedAssetObservation,
    build_snapshot_lineage_observation,
    MaterializedRevisionDependency,
)


OLD_HASH = "a" * 64
NEW_HASH = "b" * 64


def _materializations(
    *,
    snapshot_id: str,
) -> tuple[MaterializedAssetObservation, ...]:
    return (
        MaterializedAssetObservation(
            asset_external_id="chunk-policy",
            asset_kind="chunk",
            revision_external_id=(
                f"chunk-policy@{snapshot_id}"
            ),
            derived_from_snapshot_external_id=(
                snapshot_id
            ),
        ),
        MaterializedAssetObservation(
            asset_external_id="index-policy",
            asset_kind="index",
            revision_external_id=(
                f"index-policy@{snapshot_id}"
            ),
            derived_from_snapshot_external_id=(
                snapshot_id
            ),
            dependencies=("chunk-policy",),
        ),
        MaterializedAssetObservation(
            asset_external_id="agent-policy",
            asset_kind="agent",
            revision_external_id=(
                f"agent-policy@{snapshot_id}"
            ),
            derived_from_snapshot_external_id=(
                snapshot_id
            ),
            critical=True,
            dependencies=("index-policy",),
        ),
    )


def test_builds_minimal_materialization_lineage() -> None:
    observation = build_snapshot_lineage_observation(
        source_external_id="page-policy",
        snapshot_external_id="snapshot-v1",
        content_hash=OLD_HASH,
        materializations=_materializations(
            snapshot_id="snapshot-v1",
        ),
    )

    adapted = adapt_snapshot_lineage(
        observation
    )

    assert adapted.source_node_id == (
        "source:page-policy"
    )
    assert adapted.snapshot_node_id == (
        "snapshot:snapshot-v1"
    )

    assert tuple(
        node.id
        for node in adapted.graph.nodes
    ) == (
        "agent:agent-policy",
        "chunk:chunk-policy",
        "index:index-policy",
        "snapshot:snapshot-v1",
        "source:page-policy",
    )

    assert tuple(
        (
            edge.upstream_node_id,
            edge.downstream_node_id,
        )
        for edge in adapted.graph.edges
    ) == (
        (
            "chunk:chunk-policy",
            "index:index-policy",
        ),
        (
            "index:index-policy",
            "agent:agent-policy",
        ),
        (
            "snapshot:snapshot-v1",
            "chunk:chunk-policy",
        ),
        (
            "source:page-policy",
            "snapshot:snapshot-v1",
        ),
    )


def test_omitted_dependency_defaults_to_snapshot() -> None:
    observation = build_snapshot_lineage_observation(
        source_external_id="page-policy",
        snapshot_external_id="snapshot-v1",
        content_hash=OLD_HASH,
        materializations=(
            MaterializedAssetObservation(
                asset_external_id="chunk-policy",
                asset_kind="chunk",
                revision_external_id="chunk-v1",
                derived_from_snapshot_external_id=(
                    "snapshot-v1"
                ),
            ),
        ),
    )

    assert any(
        (
            dependency.upstream_external_id
            == "snapshot-v1"
            and dependency.downstream_external_id
            == "chunk-policy"
        )
        for dependency in observation.dependencies
    )


def test_explicit_empty_dependencies_are_preserved() -> None:
    observation = build_snapshot_lineage_observation(
        source_external_id="page-policy",
        snapshot_external_id="snapshot-v1",
        content_hash=OLD_HASH,
        materializations=(
            MaterializedAssetObservation(
                asset_external_id="agent-policy",
                asset_kind="agent",
                revision_external_id="agent-v1",
                derived_from_snapshot_external_id=(
                    "snapshot-v1"
                ),
                dependencies=(),
            ),
        ),
    )

    assert not any(
        dependency.downstream_external_id
        == "agent-policy"
        for dependency in observation.dependencies
    )


def test_rejects_unknown_materialization_dependency() -> None:
    with pytest.raises(
        ValueError,
        match=(
            "Materialization dependency references "
            "unknown asset"
        ),
    ):
        build_snapshot_lineage_observation(
            source_external_id="page-policy",
            snapshot_external_id="snapshot-v1",
            content_hash=OLD_HASH,
            materializations=(
                MaterializedAssetObservation(
                    asset_external_id="index-policy",
                    asset_kind="index",
                    revision_external_id="index-v1",
                    derived_from_snapshot_external_id=(
                        "snapshot-v1"
                    ),
                    dependencies=(
                        "missing-chunk",
                    ),
                ),
            ),
        )


def test_rejects_unobserved_snapshot_provenance() -> None:
    observation = build_snapshot_lineage_observation(
        source_external_id="page-policy",
        snapshot_external_id="snapshot-v2",
        content_hash=NEW_HASH,
        materializations=(
            MaterializedAssetObservation(
                asset_external_id="index-policy",
                asset_kind="index",
                revision_external_id="index-policy@v1",
                derived_from_snapshot_external_id="snapshot-v1",
            ),
        ),
    )

    with pytest.raises(
        ValueError,
        match="Asset revision references unknown snapshot",
    ):
        adapt_snapshot_lineage(observation)


def test_materialization_contract_drives_freshness_end_to_end() -> None:
    previous = adapt_snapshot_lineage(
        build_snapshot_lineage_observation(
            source_external_id="page-policy",
            snapshot_external_id="snapshot-v1",
            content_hash=OLD_HASH,
            materializations=_materializations(
                snapshot_id="snapshot-v1",
            ),
        )
    )

    current = adapt_snapshot_lineage(
        build_snapshot_lineage_observation(
            source_external_id="page-policy",
            snapshot_external_id="snapshot-v2",
            content_hash=NEW_HASH,
            materializations=_materializations(
                snapshot_id="snapshot-v2",
            ),
        )
    )

    result = analyze_lineage_staleness(
        previous=previous,
        current=current,
    )

    assert result.stale_node_ids == ()
    assert result.unknown_node_ids == ()
    assert result.fresh_node_ids == (
        "agent:agent-policy",
        "chunk:chunk-policy",
        "index:index-policy",
    )


def test_materialization_order_does_not_change_lineage() -> None:
    materializations = _materializations(
        snapshot_id="snapshot-v1",
    )

    forward = build_snapshot_lineage_observation(
        source_external_id="page-policy",
        snapshot_external_id="snapshot-v1",
        content_hash=OLD_HASH,
        materializations=materializations,
    )

    reverse = build_snapshot_lineage_observation(
        source_external_id="page-policy",
        snapshot_external_id="snapshot-v1",
        content_hash=OLD_HASH,
        materializations=reversed(
            materializations
        ),
    )

    assert forward == reverse

def test_logical_asset_dependency_cannot_identify_consumed_revision() -> None:
    previous = adapt_snapshot_lineage(
        build_snapshot_lineage_observation(
            source_external_id="page-policy",
            snapshot_external_id="snapshot-v1",
            content_hash=OLD_HASH,
            materializations=_materializations(
                snapshot_id="snapshot-v1"
            ),
        )
    )

    current = adapt_snapshot_lineage(
        SnapshotLineageObservation(
            source_external_id="page-policy",
            snapshot_external_id="snapshot-v2",
            content_hash=NEW_HASH,
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
                    external_id="snapshot-v1",
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
            dependencies=(
                LineageDependency(
                    upstream_external_id="page-policy",
                    downstream_external_id="snapshot-v2",
                    dependency_type="version_of",
                ),
                LineageDependency(
                    upstream_external_id="snapshot-v2",
                    downstream_external_id="chunk-policy",
                    dependency_type="derived_from",
                ),
                LineageDependency(
                    upstream_external_id="chunk-policy",
                    downstream_external_id="index-policy",
                    dependency_type="derived_from",
                ),
                LineageDependency(
                    upstream_external_id="index-policy",
                    downstream_external_id="agent-policy",
                    dependency_type="derived_from",
                ),
            ),
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
        )
    )

    analysis = analyze_lineage_staleness(
        previous=previous,
        current=current,
    )

    assert analysis.fresh_node_ids == (
        "chunk:chunk-policy",
    )
    assert analysis.stale_node_ids == (
        "agent:agent-policy",
        "index:index-policy",
    )

    chunk_revision = next(
        revision
        for revision in current.asset_revisions
        if revision.asset_node_id == "chunk:chunk-policy"
    )
    index_revision = next(
        revision
        for revision in current.asset_revisions
        if revision.asset_node_id == "index:index-policy"
    )

    assert chunk_revision.revision_external_id == "chunk-policy@v2"
    assert index_revision.revision_external_id == "index-policy@v1"

    assert not hasattr(
        index_revision,
        "upstream_revision_external_ids",
    )

def test_builds_revision_level_materialization_dependencies() -> None:
    observation = build_snapshot_lineage_observation(
        source_external_id="page-policy",
        snapshot_external_id="snapshot-v2",
        content_hash=NEW_HASH,
        materializations=(
            MaterializedAssetObservation(
                asset_external_id="chunk-policy",
                asset_kind="chunk",
                revision_external_id="chunk-policy@v2",
                derived_from_snapshot_external_id="snapshot-v2",
            ),
            MaterializedAssetObservation(
                asset_external_id="index-policy",
                asset_kind="index",
                revision_external_id="index-policy@v2",
                derived_from_snapshot_external_id="snapshot-v2",
                dependencies=("chunk-policy",),
                revision_dependencies=(
                    MaterializedRevisionDependency(
                        asset_external_id="chunk-policy",
                        revision_external_id="chunk-policy@v1",
                        dependency_type="contains",
                    ),
                ),
            ),
        ),
    )

    assert observation.revision_dependencies == (
        LineageRevisionDependency(
            upstream_asset_external_id="chunk-policy",
            upstream_revision_external_id="chunk-policy@v1",
            downstream_asset_external_id="index-policy",
            downstream_revision_external_id="index-policy@v2",
            dependency_type="contains",
        ),
    )

def test_partial_rebuild_preserves_mixed_revision_provenance() -> None:
    observation = build_snapshot_lineage_observation(
        source_external_id="page-policy",
        snapshot_external_id="snapshot-v2",
        content_hash=NEW_HASH,
        historical_snapshot_external_ids=("snapshot-v1",),
        materializations=(
            MaterializedAssetObservation(
                asset_external_id="chunk-policy",
                asset_kind="chunk",
                revision_external_id="chunk-policy@v2",
                derived_from_snapshot_external_id="snapshot-v2",
            ),
            MaterializedAssetObservation(
                asset_external_id="index-policy",
                asset_kind="index",
                revision_external_id="index-policy@v1",
                derived_from_snapshot_external_id="snapshot-v1",
                dependencies=("chunk-policy",),
                revision_dependencies=(
                    MaterializedRevisionDependency(
                        asset_external_id="chunk-policy",
                        revision_external_id="chunk-policy@v1",
                        dependency_type="contains",
                    ),
                ),
            ),
            MaterializedAssetObservation(
                asset_external_id="agent-policy",
                asset_kind="agent",
                revision_external_id="agent-policy@v1",
                derived_from_snapshot_external_id="snapshot-v1",
                critical=True,
                dependencies=("index-policy",),
                revision_dependencies=(
                    MaterializedRevisionDependency(
                        asset_external_id="index-policy",
                        revision_external_id="index-policy@v1",
                        dependency_type="consumes",
                    ),
                ),
            ),
        ),
    )

    adapted = adapt_snapshot_lineage(observation)

    revisions = {
        revision.asset_node_id: revision
        for revision in adapted.asset_revisions
    }

    assert (
        revisions["chunk:chunk-policy"]
        .derived_from_snapshot_node_id
        == "snapshot:snapshot-v2"
    )
    assert (
        revisions["index:index-policy"]
        .derived_from_snapshot_node_id
        == "snapshot:snapshot-v1"
    )
    assert (
        revisions["agent:agent-policy"]
        .derived_from_snapshot_node_id
        == "snapshot:snapshot-v1"
    )

    assert tuple(
        (
            dependency.upstream_revision_external_id,
            dependency.downstream_revision_external_id,
        )
        for dependency in adapted.revision_dependencies
    ) == (
        ("chunk-policy@v1", "index-policy@v1"),
        ("index-policy@v1", "agent-policy@v1"),
    )

def test_partial_rebuild_drives_targeted_invalidation_end_to_end() -> None:
    previous = adapt_snapshot_lineage(
        build_snapshot_lineage_observation(
            source_external_id="page-policy",
            snapshot_external_id="snapshot-v1",
            content_hash=OLD_HASH,
            materializations=(
                MaterializedAssetObservation(
                    asset_external_id="chunk-policy",
                    asset_kind="chunk",
                    revision_external_id="chunk-policy@v1",
                    derived_from_snapshot_external_id="snapshot-v1",
                ),
                MaterializedAssetObservation(
                    asset_external_id="index-policy",
                    asset_kind="index",
                    revision_external_id="index-policy@v1",
                    derived_from_snapshot_external_id="snapshot-v1",
                    dependencies=("chunk-policy",),
                    revision_dependencies=(
                        MaterializedRevisionDependency(
                            asset_external_id="chunk-policy",
                            revision_external_id="chunk-policy@v1",
                            dependency_type="contains",
                        ),
                    ),
                ),
                MaterializedAssetObservation(
                    asset_external_id="agent-policy",
                    asset_kind="agent",
                    revision_external_id="agent-policy@v1",
                    derived_from_snapshot_external_id="snapshot-v1",
                    critical=True,
                    dependencies=("index-policy",),
                    revision_dependencies=(
                        MaterializedRevisionDependency(
                            asset_external_id="index-policy",
                            revision_external_id="index-policy@v1",
                            dependency_type="consumes",
                        ),
                    ),
                ),
            ),
        )
    )

    current = adapt_snapshot_lineage(
        build_snapshot_lineage_observation(
            source_external_id="page-policy",
            snapshot_external_id="snapshot-v2",
            content_hash=NEW_HASH,
            historical_snapshot_external_ids=("snapshot-v1",),
            materializations=(
                MaterializedAssetObservation(
                    asset_external_id="chunk-policy",
                    asset_kind="chunk",
                    revision_external_id="chunk-policy@v2",
                    derived_from_snapshot_external_id="snapshot-v2",
                ),
                MaterializedAssetObservation(
                    asset_external_id="index-policy",
                    asset_kind="index",
                    revision_external_id="index-policy@v1",
                    derived_from_snapshot_external_id="snapshot-v1",
                    dependencies=("chunk-policy",),
                    revision_dependencies=(
                        MaterializedRevisionDependency(
                            asset_external_id="chunk-policy",
                            revision_external_id="chunk-policy@v1",
                            dependency_type="contains",
                        ),
                    ),
                ),
                MaterializedAssetObservation(
                    asset_external_id="agent-policy",
                    asset_kind="agent",
                    revision_external_id="agent-policy@v1",
                    derived_from_snapshot_external_id="snapshot-v1",
                    critical=True,
                    dependencies=("index-policy",),
                    revision_dependencies=(
                        MaterializedRevisionDependency(
                            asset_external_id="index-policy",
                            revision_external_id="index-policy@v1",
                            dependency_type="consumes",
                        ),
                    ),
                ),
            ),
        )
    )

    analysis = analyze_lineage_staleness(
        previous=previous,
        current=current,
    )

    assert analysis.fresh_node_ids == (
        "chunk:chunk-policy",
    )
    assert analysis.stale_node_ids == (
        "agent:agent-policy",
        "index:index-policy",
    )
    assert analysis.unknown_node_ids == ()
    assert analysis.stale_materialized_node_ids == (
        "agent:agent-policy",
        "index:index-policy",
    )
    assert analysis.exposed_critical_node_ids == (
        "agent:agent-policy",
    )

    assert current.revision_dependencies == (
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
