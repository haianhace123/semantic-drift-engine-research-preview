import pytest

from benchmarks.temporal_knowledge_integrity.materialization_event import (
    MaterializationEvent,
    RevisionReference,
    build_lineage_from_projection,
    project_materialization_events,
)
from benchmarks.temporal_knowledge_integrity.lineage_adapter import (
    adapt_snapshot_lineage,
)
from benchmarks.temporal_knowledge_integrity.lineage_staleness import (
    analyze_lineage_staleness,
)
OLD_HASH = "1" * 64
NEW_HASH = "2" * 64
def test_materialization_event_contains_only_external_lineage_facts() -> None:
    event = MaterializationEvent(
        source_external_id="page-policy",
        snapshot_external_id="snapshot-v1",
        asset_external_id="index-policy",
        asset_kind="index",
        revision_external_id="index-policy@v1",
        dependencies=(
            RevisionReference(
                asset_external_id="chunk-policy",
                revision_external_id="chunk-policy@v1",
                dependency_type="contains",
            ),
        ),
    )

    assert event.source_external_id == "page-policy"
    assert event.snapshot_external_id == "snapshot-v1"
    assert event.asset_external_id == "index-policy"
    assert event.revision_external_id == "index-policy@v1"
    assert event.dependencies == (
        RevisionReference(
            asset_external_id="chunk-policy",
            revision_external_id="chunk-policy@v1",
            dependency_type="contains",
        ),
    )


def test_partial_rebuild_requires_no_historical_graph_payload() -> None:
    events = (
        MaterializationEvent(
            source_external_id="page-policy",
            snapshot_external_id="snapshot-v2",
            asset_external_id="chunk-policy",
            asset_kind="chunk",
            revision_external_id="chunk-policy@v2",
        ),
        MaterializationEvent(
            source_external_id="page-policy",
            snapshot_external_id="snapshot-v1",
            asset_external_id="index-policy",
            asset_kind="index",
            revision_external_id="index-policy@v1",
            dependencies=(
                RevisionReference(
                    asset_external_id="chunk-policy",
                    revision_external_id="chunk-policy@v1",
                    dependency_type="contains",
                ),
            ),
        ),
        MaterializationEvent(
            source_external_id="page-policy",
            snapshot_external_id="snapshot-v1",
            asset_external_id="agent-policy",
            asset_kind="agent",
            revision_external_id="agent-policy@v1",
            critical=True,
            dependencies=(
                RevisionReference(
                    asset_external_id="index-policy",
                    revision_external_id="index-policy@v1",
                    dependency_type="consumes",
                ),
            ),
        ),
    )

    assert {
        event.asset_external_id: event.snapshot_external_id
        for event in events
    } == {
        "chunk-policy": "snapshot-v2",
        "index-policy": "snapshot-v1",
        "agent-policy": "snapshot-v1",
    }

    assert all(
        not hasattr(event, "historical_snapshot_external_ids")
        for event in events
    )
    assert all(
        not hasattr(event, "derived_from_snapshot_external_id")
        for event in events
    )

def test_projector_keeps_latest_materialized_revision_per_asset() -> None:
    projection = project_materialization_events(
        (
            MaterializationEvent(
                source_external_id="page-policy",
                snapshot_external_id="snapshot-v1",
                asset_external_id="chunk-policy",
                asset_kind="chunk",
                revision_external_id="chunk-policy@v1",
                sequence=1,
            ),
            MaterializationEvent(
                source_external_id="page-policy",
                snapshot_external_id="snapshot-v2",
                asset_external_id="chunk-policy",
                asset_kind="chunk",
                revision_external_id="chunk-policy@v2",
                sequence=2,
            ),
        ),
        current_snapshot_external_id="snapshot-v2",
    )

    assert tuple(
        (
            event.asset_external_id,
            event.revision_external_id,
        )
        for event in projection.current_asset_events
    ) == (
        ("chunk-policy", "chunk-policy@v2"),
    )
    assert projection.observed_snapshot_external_ids == (
        "snapshot-v1",
        "snapshot-v2",
    )

def test_event_stream_drives_partial_rebuild_invalidation() -> None:
    previous_events = (
        MaterializationEvent(
            source_external_id="page-policy",
            snapshot_external_id="snapshot-v1",
            asset_external_id="chunk-policy",
            asset_kind="chunk",
            revision_external_id="chunk-policy@v1",
            sequence=1,
        ),
        MaterializationEvent(
            source_external_id="page-policy",
            snapshot_external_id="snapshot-v1",
            asset_external_id="index-policy",
            asset_kind="index",
            revision_external_id="index-policy@v1",
            dependencies=(
                RevisionReference(
                    asset_external_id="chunk-policy",
                    revision_external_id="chunk-policy@v1",
                    dependency_type="contains",
                ),
            ),
            sequence=1,
        ),
        MaterializationEvent(
            source_external_id="page-policy",
            snapshot_external_id="snapshot-v1",
            asset_external_id="agent-policy",
            asset_kind="agent",
            revision_external_id="agent-policy@v1",
            critical=True,
            dependencies=(
                RevisionReference(
                    asset_external_id="index-policy",
                    revision_external_id="index-policy@v1",
                    dependency_type="consumes",
                ),
            ),
            sequence=1,
        ),
    )

    current_events = previous_events + (
        MaterializationEvent(
            source_external_id="page-policy",
            snapshot_external_id="snapshot-v2",
            asset_external_id="chunk-policy",
            asset_kind="chunk",
            revision_external_id="chunk-policy@v2",
            sequence=2,
        ),
    )

    previous = adapt_snapshot_lineage(
        build_lineage_from_projection(
            project_materialization_events(
                previous_events
            ),
            content_hash=OLD_HASH,
        )
    )
    current = adapt_snapshot_lineage(
        build_lineage_from_projection(
            project_materialization_events(
                current_events,
                current_snapshot_external_id="snapshot-v2",
            ),
            content_hash=NEW_HASH,
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

def test_duplicate_retry_does_not_change_projection() -> None:
    event = MaterializationEvent(
        source_external_id="page-policy",
        snapshot_external_id="snapshot-v1",
        asset_external_id="chunk-policy",
        asset_kind="chunk",
        revision_external_id="chunk-policy@v1",
        sequence=1,
    )

    projection = project_materialization_events(
        (event, event)
    )

    assert projection.current_asset_events == (event,)

def test_out_of_order_delivery_does_not_roll_back_asset() -> None:
    newer = MaterializationEvent(
        source_external_id="page-policy",
        snapshot_external_id="snapshot-v2",
        asset_external_id="chunk-policy",
        asset_kind="chunk",
        revision_external_id="chunk-policy@v2",
        sequence=2,
    )
    older = MaterializationEvent(
        source_external_id="page-policy",
        snapshot_external_id="snapshot-v1",
        asset_external_id="chunk-policy",
        asset_kind="chunk",
        revision_external_id="chunk-policy@v1",
        sequence=1,
    )

    projection = project_materialization_events(
        (newer, older),
        current_snapshot_external_id="snapshot-v2",
    )

    assert projection.current_asset_events == (newer,)
    assert (
        projection.current_snapshot_external_id
        == "snapshot-v2"
    )

def test_conflicting_retry_with_same_sequence_is_rejected() -> None:
    first = MaterializationEvent(
        source_external_id="page-policy",
        snapshot_external_id="snapshot-v1",
        asset_external_id="chunk-policy",
        asset_kind="chunk",
        revision_external_id="chunk-policy@v1",
        sequence=1,
    )
    conflicting = MaterializationEvent(
        source_external_id="page-policy",
        snapshot_external_id="snapshot-v2",
        asset_external_id="chunk-policy",
        asset_kind="chunk",
        revision_external_id="chunk-policy@v2",
        sequence=1,
    )

    with pytest.raises(
        ValueError,
        match=(
            "Conflicting materialization events share "
            "the same asset sequence"
        ),
    ):
        project_materialization_events(
            (first, conflicting)
        )

def test_source_head_is_independent_of_per_asset_sequence() -> None:
    projection = project_materialization_events(
        (
            MaterializationEvent(
                source_external_id="page-policy",
                snapshot_external_id="snapshot-v1",
                asset_external_id="agent-policy",
                asset_kind="agent",
                revision_external_id="agent-policy@v500",
                sequence=500,
                critical=True,
            ),
            MaterializationEvent(
                source_external_id="page-policy",
                snapshot_external_id="snapshot-v2",
                asset_external_id="chunk-policy",
                asset_kind="chunk",
                revision_external_id="chunk-policy@v2",
                sequence=2,
            ),
        ),
        current_snapshot_external_id="snapshot-v2",
    )

    assert (
        projection.current_snapshot_external_id
        == "snapshot-v2"
    )

def test_mixed_snapshot_events_require_authoritative_source_head() -> None:
    with pytest.raises(
        ValueError,
        match=(
            "Current source snapshot must be provided when "
            "materialization events reference multiple snapshots"
        ),
    ):
        project_materialization_events(
            (
                MaterializationEvent(
                    source_external_id="page-policy",
                    snapshot_external_id="snapshot-v1",
                    asset_external_id="agent-policy",
                    asset_kind="agent",
                    revision_external_id="agent-policy@v500",
                    sequence=500,
                ),
                MaterializationEvent(
                    source_external_id="page-policy",
                    snapshot_external_id="snapshot-v2",
                    asset_external_id="chunk-policy",
                    asset_kind="chunk",
                    revision_external_id="chunk-policy@v2",
                    sequence=2,
                ),
            )
        )