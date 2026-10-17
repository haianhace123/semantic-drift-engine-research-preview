from benchmarks.temporal_knowledge_integrity.materialization_event import (
    MaterializationEvent,
    RevisionReference,
)
from src.domain.lineage_observation import (
    LineageObservation,
    LineageObservationKind,
    LineageRevisionReference,
    project_lineage_observations,
)


def _adapt_materialization_event(
    event: MaterializationEvent,
    *,
    observation_kind: LineageObservationKind = "materialization",
) -> LineageObservation:
    return LineageObservation(
        observation_kind=observation_kind,
        source_external_id=event.source_external_id,
        snapshot_external_id=event.snapshot_external_id,
        asset_external_id=event.asset_external_id,
        asset_kind=event.asset_kind,
        revision_external_id=event.revision_external_id,
        sequence=event.sequence,
        dependencies=tuple(
            LineageRevisionReference(
                asset_external_id=dependency.asset_external_id,
                revision_external_id=dependency.revision_external_id,
                dependency_type=dependency.dependency_type,
            )
            for dependency in event.dependencies
        ),
    )


def test_materialization_event_maps_without_losing_causal_facts() -> None:
    event = MaterializationEvent(
        source_external_id="policy-source",
        snapshot_external_id="snapshot-v3",
        asset_external_id="support-index",
        asset_kind="index",
        revision_external_id="support-index:revision-v3",
        sequence=3,
        dependencies=(
            RevisionReference(
                asset_external_id="chunk-refunds",
                revision_external_id="chunk-refunds:revision-v2",
                dependency_type="contains",
            ),
            RevisionReference(
                asset_external_id="chunk-escalation",
                revision_external_id="chunk-escalation:revision-v1",
                dependency_type="contains",
            ),
        ),
    )

    observation = _adapt_materialization_event(event)

    assert observation.observation_kind == "materialization"
    assert observation.source_external_id == event.source_external_id
    assert observation.snapshot_external_id == event.snapshot_external_id
    assert observation.asset_external_id == event.asset_external_id
    assert observation.asset_kind == event.asset_kind
    assert observation.revision_external_id == event.revision_external_id
    assert observation.sequence == event.sequence
    assert tuple(
        (
            dependency.asset_external_id,
            dependency.revision_external_id,
            dependency.dependency_type,
        )
        for dependency in observation.dependencies
    ) == (
        (
            "chunk-refunds",
            "chunk-refunds:revision-v2",
            "contains",
        ),
        (
            "chunk-escalation",
            "chunk-escalation:revision-v1",
            "contains",
        ),
    )


def test_historical_consumption_maps_without_latest_inference() -> None:
    event = MaterializationEvent(
        source_external_id="policy-source",
        snapshot_external_id="snapshot-v3",
        asset_external_id="support-agent",
        asset_kind="agent",
        revision_external_id="support-agent:run-v3",
        sequence=3,
        dependencies=(
            RevisionReference(
                asset_external_id="chunk-policy",
                revision_external_id="chunk-policy:revision-v1",
                dependency_type="consumed",
            ),
        ),
    )

    observation = _adapt_materialization_event(
        event,
        observation_kind="consumption",
    )

    assert observation.dependencies == (
        LineageRevisionReference(
            asset_external_id="chunk-policy",
            revision_external_id="chunk-policy:revision-v1",
            dependency_type="consumed",
        ),
    )


def test_multiple_consumed_revisions_of_same_asset_survive_mapping() -> None:
    event = MaterializationEvent(
        source_external_id="policy-source",
        snapshot_external_id="snapshot-v3",
        asset_external_id="support-agent",
        asset_kind="agent",
        revision_external_id="support-agent:run-mixed",
        sequence=3,
        dependencies=(
            RevisionReference(
                asset_external_id="chunk-policy",
                revision_external_id="chunk-policy:revision-v1",
                dependency_type="consumed",
            ),
            RevisionReference(
                asset_external_id="chunk-policy",
                revision_external_id="chunk-policy:revision-v2",
                dependency_type="consumed",
            ),
        ),
    )

    observation = _adapt_materialization_event(
        event,
        observation_kind="consumption",
    )

    assert tuple(
        dependency.revision_external_id
        for dependency in observation.dependencies
    ) == (
        "chunk-policy:revision-v1",
        "chunk-policy:revision-v2",
    )


def test_mixed_snapshot_observations_preserve_exact_revision_history() -> None:
    revision_v1 = _adapt_materialization_event(
        MaterializationEvent(
            source_external_id="policy-source",
            snapshot_external_id="snapshot-v1",
            asset_external_id="chunk-policy",
            asset_kind="chunk",
            revision_external_id="chunk-policy:revision-v1",
            sequence=1,
        )
    )
    revision_v3 = _adapt_materialization_event(
        MaterializationEvent(
            source_external_id="policy-source",
            snapshot_external_id="snapshot-v3",
            asset_external_id="chunk-policy",
            asset_kind="chunk",
            revision_external_id="chunk-policy:revision-v3",
            sequence=3,
        )
    )

    projection = project_lineage_observations(
        (
            revision_v3,
            revision_v1,
        )
    )

    assert projection.current_observations == (revision_v3,)
    assert projection.historical_observations == (revision_v1,)


def test_consumption_and_materialization_mapping_remain_independent() -> None:
    materialization = _adapt_materialization_event(
        MaterializationEvent(
            source_external_id="policy-source",
            snapshot_external_id="snapshot-v2",
            asset_external_id="support-agent",
            asset_kind="agent",
            revision_external_id="support-agent:materialized-v2",
            sequence=2,
        )
    )
    consumption = _adapt_materialization_event(
        MaterializationEvent(
            source_external_id="policy-source",
            snapshot_external_id="snapshot-v2",
            asset_external_id="support-agent",
            asset_kind="agent",
            revision_external_id="support-agent:run-v2",
            sequence=2,
            dependencies=(
                RevisionReference(
                    asset_external_id="chunk-policy",
                    revision_external_id="chunk-policy:revision-v1",
                    dependency_type="consumed",
                ),
            ),
        ),
        observation_kind="consumption",
    )

    projection = project_lineage_observations(
        (
            materialization,
            consumption,
        )
    )

    assert set(
        observation.observation_kind
        for observation in projection.current_observations
    ) == {
        "materialization",
        "consumption",
    }


def test_framework_specific_dependency_type_survives_mapping() -> None:
    event = MaterializationEvent(
        source_external_id="policy-source",
        snapshot_external_id="snapshot-v4",
        asset_external_id="support-agent",
        asset_kind="agent",
        revision_external_id="support-agent:revision-v4",
        sequence=4,
        dependencies=(
            RevisionReference(
                asset_external_id="support-index",
                revision_external_id="support-index:revision-v2",
                dependency_type="retrieves_from",
            ),
        ),
    )

    observation = _adapt_materialization_event(event)

    assert observation.dependencies == (
        LineageRevisionReference(
            asset_external_id="support-index",
            revision_external_id="support-index:revision-v2",
            dependency_type="retrieves_from",
        ),
    )