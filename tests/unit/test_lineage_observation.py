import pytest

from src.domain.lineage_observation import (
    LineageObservation,
    LineageObservationKind,
    LineageRevisionReference,
    project_lineage_observations,
)


def _observation(
    *,
    observation_kind: LineageObservationKind = "materialization",
    source_external_id: str = "policy-source",
    snapshot_external_id: str = "snapshot-v1",
    asset_external_id: str = "chunk-policy",
    revision_external_id: str = "chunk-policy:revision-v1",
    sequence: int = 1,
    dependencies: tuple[LineageRevisionReference, ...] = (),
) -> LineageObservation:
    return LineageObservation(
        observation_kind=observation_kind,
        source_external_id=source_external_id,
        snapshot_external_id=snapshot_external_id,
        asset_external_id=asset_external_id,
        asset_kind="chunk",
        revision_external_id=revision_external_id,
        sequence=sequence,
        dependencies=dependencies,
    )


def test_identical_retry_is_idempotent() -> None:
    observation = _observation()

    projection = project_lineage_observations(
        (
            observation,
            observation,
        )
    )

    assert projection.current_observations == (observation,)
    assert projection.historical_observations == ()


def test_same_subject_sequence_with_different_payload_conflicts() -> None:
    first = _observation()
    conflicting = _observation(
        snapshot_external_id="snapshot-v2",
        revision_external_id="chunk-policy:revision-v2",
    )

    with pytest.raises(
        ValueError,
        match="same subject sequence",
    ):
        project_lineage_observations(
            (
                first,
                conflicting,
            )
        )


def test_out_of_order_observation_does_not_replace_current_state() -> None:
    newer = _observation(
        snapshot_external_id="snapshot-v2",
        revision_external_id="chunk-policy:revision-v2",
        sequence=2,
    )
    older = _observation(sequence=1)

    projection = project_lineage_observations(
        (
            newer,
            older,
        )
    )

    assert projection.current_observations == (newer,)
    assert projection.historical_observations == (older,)


def test_identical_historical_retry_is_idempotent() -> None:
    newer = _observation(
        snapshot_external_id="snapshot-v2",
        revision_external_id="chunk-policy:revision-v2",
        sequence=2,
    )
    older = _observation(sequence=1)

    projection = project_lineage_observations(
        (
            newer,
            older,
            older,
        )
    )

    assert projection.current_observations == (newer,)
    assert projection.historical_observations == (older,)


def test_conflicting_historical_retry_is_rejected() -> None:
    newer = _observation(
        snapshot_external_id="snapshot-v3",
        revision_external_id="chunk-policy:revision-v3",
        sequence=3,
    )
    historical = _observation(sequence=1)
    conflicting = _observation(
        snapshot_external_id="snapshot-conflict",
        revision_external_id="chunk-policy:conflict",
        sequence=1,
    )

    with pytest.raises(
        ValueError,
        match="same subject sequence",
    ):
        project_lineage_observations(
            (
                newer,
                historical,
                conflicting,
            )
        )


def test_intermediate_out_of_order_observation_is_preserved_as_history() -> None:
    revision_v1 = _observation(
        snapshot_external_id="snapshot-v1",
        revision_external_id="chunk-policy:revision-v1",
        sequence=1,
    )
    revision_v3 = _observation(
        snapshot_external_id="snapshot-v3",
        revision_external_id="chunk-policy:revision-v3",
        sequence=3,
    )
    revision_v2 = _observation(
        snapshot_external_id="snapshot-v2",
        revision_external_id="chunk-policy:revision-v2",
        sequence=2,
    )

    projection = project_lineage_observations(
        (
            revision_v1,
            revision_v3,
            revision_v2,
        )
    )

    assert projection.current_observations == (revision_v3,)
    assert projection.historical_observations == (
        revision_v1,
        revision_v2,
    )


def test_projection_is_stable_across_observation_arrival_order() -> None:
    revision_v1 = _observation(
        snapshot_external_id="snapshot-v1",
        revision_external_id="chunk-policy:revision-v1",
        sequence=1,
    )
    revision_v2 = _observation(
        snapshot_external_id="snapshot-v2",
        revision_external_id="chunk-policy:revision-v2",
        sequence=2,
    )
    revision_v3 = _observation(
        snapshot_external_id="snapshot-v3",
        revision_external_id="chunk-policy:revision-v3",
        sequence=3,
    )

    ordered = project_lineage_observations(
        (
            revision_v1,
            revision_v2,
            revision_v3,
        )
    )
    out_of_order = project_lineage_observations(
        (
            revision_v3,
            revision_v1,
            revision_v2,
        )
    )

    assert out_of_order == ordered


def test_newer_observation_moves_previous_current_to_history() -> None:
    older = _observation(sequence=1)
    newer = _observation(
        snapshot_external_id="snapshot-v2",
        revision_external_id="chunk-policy:revision-v2",
        sequence=2,
    )

    projection = project_lineage_observations(
        (
            older,
            newer,
        )
    )

    assert projection.current_observations == (newer,)
    assert projection.historical_observations == (older,)


def test_same_external_asset_id_is_scoped_by_source() -> None:
    source_a = _observation(
        source_external_id="source-a",
    )
    source_b = _observation(
        source_external_id="source-b",
    )

    projection = project_lineage_observations(
        (
            source_a,
            source_b,
        )
    )

    assert projection.current_observations == (
        source_a,
        source_b,
    )


def test_materialization_and_consumption_have_independent_streams() -> None:
    materialization = _observation()
    consumption = _observation(
        observation_kind="consumption",
        snapshot_external_id="snapshot-v2",
        revision_external_id="consumer-run-v1",
        dependencies=(
            LineageRevisionReference(
                asset_external_id="chunk-policy",
                revision_external_id="chunk-policy:revision-v1",
                dependency_type="consumed",
            ),
        ),
    )

    projection = project_lineage_observations(
        (
            materialization,
            consumption,
        )
    )

    assert projection.current_observations == (
        consumption,
        materialization,
    )


def test_consumption_preserves_historical_exact_revision() -> None:
    dependency = LineageRevisionReference(
        asset_external_id="chunk-policy",
        revision_external_id="chunk-policy:revision-v1",
        dependency_type="consumed",
    )
    consumption = _observation(
        observation_kind="consumption",
        snapshot_external_id="snapshot-v2",
        asset_external_id="support-agent",
        revision_external_id="support-agent:run-v2",
        sequence=2,
        dependencies=(dependency,),
    )

    projection = project_lineage_observations(
        (consumption,)
    )

    assert (
        projection.current_observations[0].dependencies
        == (dependency,)
    )


def test_consumption_preserves_multiple_revisions_of_same_asset() -> None:
    revision_v1 = LineageRevisionReference(
        asset_external_id="chunk-policy",
        revision_external_id="chunk-policy:revision-v1",
        dependency_type="consumed",
    )
    revision_v2 = LineageRevisionReference(
        asset_external_id="chunk-policy",
        revision_external_id="chunk-policy:revision-v2",
        dependency_type="consumed",
    )

    consumption = _observation(
        observation_kind="consumption",
        asset_external_id="support-agent",
        revision_external_id="support-agent:run-mixed",
        dependencies=(
            revision_v1,
            revision_v2,
        ),
    )

    assert consumption.dependencies == (
        revision_v1,
        revision_v2,
    )


def test_consumption_without_exact_dependency_fails_closed() -> None:
    with pytest.raises(
        ValueError,
        match="exact revision dependency",
    ):
        _observation(
            observation_kind="consumption",
            asset_external_id="support-agent",
            revision_external_id="support-agent:run-v1",
        )


@pytest.mark.parametrize(
    ("field_name", "overrides"),
    (
        (
            "source_external_id",
            {"source_external_id": "   "},
        ),
        (
            "snapshot_external_id",
            {"snapshot_external_id": ""},
        ),
        (
            "asset_external_id",
            {"asset_external_id": "\t"},
        ),
        (
            "revision_external_id",
            {"revision_external_id": "\n"},
        ),
    ),
)
def test_required_observation_identity_fails_closed(
    field_name: str,
    overrides: dict[str, str],
) -> None:
    with pytest.raises(
        ValueError,
        match=field_name,
    ):
        _observation(**overrides)


@pytest.mark.parametrize(
    ("field_name", "kwargs"),
    (
        (
            "dependency asset_external_id",
            {
                "asset_external_id": " ",
                "revision_external_id": "revision-v1",
            },
        ),
        (
            "dependency revision_external_id",
            {
                "asset_external_id": "chunk-policy",
                "revision_external_id": " ",
            },
        ),
    ),
)
def test_required_dependency_identity_fails_closed(
    field_name: str,
    kwargs: dict[str, str],
) -> None:
    with pytest.raises(
        ValueError,
        match=field_name,
    ):
        LineageRevisionReference(**kwargs)


def test_negative_sequence_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="sequence must not be negative",
    ):
        _observation(sequence=-1)


def test_dependency_type_accepts_open_relationship_vocabulary() -> None:
    dependency = LineageRevisionReference(
        asset_external_id="index-policy",
        revision_external_id="index-policy:revision-v7",
        dependency_type="retrieves_from",
    )

    assert dependency.dependency_type == "retrieves_from"


def test_empty_dependency_type_is_rejected() -> None:
    with pytest.raises(
        ValueError,
        match="dependency_type",
    ):
        LineageRevisionReference(
            asset_external_id="chunk-policy",
            revision_external_id="chunk-policy:revision-v1",
            dependency_type="   ",
        )

def test_duplicate_exact_dependency_is_rejected() -> None:
    dependency = LineageRevisionReference(
        asset_external_id="index-policy",
        revision_external_id="index-policy:revision-v4",
        dependency_type="retrieves_from",
    )

    with pytest.raises(
        ValueError,
        match="dependencies must be unique",
    ):
        LineageObservation(
            observation_kind="consumption",
            source_external_id="policy-source",
            snapshot_external_id="snapshot-v4",
            asset_external_id="support-agent",
            asset_kind="agent",
            revision_external_id="support-agent:revision-v4",
            sequence=4,
            dependencies=(
                dependency,
                dependency,
            ),
        )