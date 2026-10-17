from __future__ import annotations

from dataclasses import dataclass

from benchmarks.temporal_knowledge_integrity.dependency_impact import (
    KnowledgeNodeKind,
)
from benchmarks.temporal_knowledge_integrity.lineage_adapter import (
    LineageAsset,
    LineageAssetRevision,
    LineageDependency,
    LineageRevisionDependency,
    SnapshotLineageObservation,
)


@dataclass(frozen=True)
class RevisionReference:
    asset_external_id: str
    revision_external_id: str
    dependency_type: str = "derived_from"


@dataclass(frozen=True)
class MaterializationEvent:
    source_external_id: str
    snapshot_external_id: str
    asset_external_id: str
    asset_kind: KnowledgeNodeKind
    revision_external_id: str
    sequence: int = 0
    materialized: bool = True
    critical: bool = False
    dependencies: tuple[RevisionReference, ...] = ()

@dataclass(frozen=True)
class MaterializationProjection:
    source_external_id: str
    current_snapshot_external_id: str
    current_asset_events: tuple[MaterializationEvent, ...]
    observed_snapshot_external_ids: tuple[str, ...]

def project_materialization_events(
    events: tuple[MaterializationEvent, ...],
    *,
    current_snapshot_external_id: str | None = None,
) -> MaterializationProjection:
    if not events:
        raise ValueError(
            "At least one materialization event is required."
        )

    source_external_ids = {
        event.source_external_id
        for event in events
    }
    if len(source_external_ids) != 1:
        raise ValueError(
            "Materialization events must belong to one source."
        )

    current_events_by_asset: dict[
        str,
        MaterializationEvent,
    ] = {}

    for event in events:
        current_event = current_events_by_asset.get(
            event.asset_external_id
        )

        if (
            current_event is None
            or event.sequence > current_event.sequence
        ):
            current_events_by_asset[
                event.asset_external_id
            ] = event
            continue

        if (
            event.sequence == current_event.sequence
            and event != current_event
        ):
            raise ValueError(
                "Conflicting materialization events share "
                "the same asset sequence."
            )

    if current_snapshot_external_id is None:
        observed_snapshot_external_ids = {
            event.snapshot_external_id
            for event in events
        }
        if len(observed_snapshot_external_ids) != 1:
            raise ValueError(
                "Current source snapshot must be provided when "
                "materialization events reference multiple snapshots."
            )
        resolved_current_snapshot_external_id = next(
            iter(observed_snapshot_external_ids)
        )
    else:
        resolved_current_snapshot_external_id = (
            current_snapshot_external_id.strip()
        )
        if not resolved_current_snapshot_external_id:
            raise ValueError(
                "Current source snapshot must not be empty."
            )

    return MaterializationProjection(
        source_external_id=events[-1].source_external_id,
        current_snapshot_external_id=(
            resolved_current_snapshot_external_id
        ),
        current_asset_events=tuple(
            sorted(
                current_events_by_asset.values(),
                key=lambda event: (
                    event.asset_external_id,
                ),
            )
        ),
        observed_snapshot_external_ids=tuple(
            sorted(
                {
                    event.snapshot_external_id
                    for event in events
                }
            )
        ),
    )

def build_lineage_from_projection(
    projection: MaterializationProjection,
    *,
    content_hash: str,
) -> SnapshotLineageObservation:
    source_external_id = projection.source_external_id
    current_snapshot_external_id = (
        projection.current_snapshot_external_id
    )

    assets: dict[str, LineageAsset] = {
        source_external_id: LineageAsset(
            external_id=source_external_id,
            kind="source",
        ),
    }

    for snapshot_external_id in (
        projection.observed_snapshot_external_ids
    ):
        assets[snapshot_external_id] = LineageAsset(
            external_id=snapshot_external_id,
            kind="snapshot",
        )

    dependencies: dict[
        tuple[str, str, str],
        LineageDependency,
    ] = {}

    source_snapshot_dependency = LineageDependency(
        upstream_external_id=source_external_id,
        downstream_external_id=current_snapshot_external_id,
        dependency_type="version_of",
    )
    dependencies[
        (
            source_snapshot_dependency.upstream_external_id,
            source_snapshot_dependency.downstream_external_id,
            source_snapshot_dependency.dependency_type,
        )
    ] = source_snapshot_dependency

    revisions: list[LineageAssetRevision] = []
    revision_dependencies: list[
        LineageRevisionDependency
    ] = []

    current_asset_external_ids = {
        event.asset_external_id
        for event in projection.current_asset_events
    }

    for event in projection.current_asset_events:
        assets[event.asset_external_id] = LineageAsset(
            external_id=event.asset_external_id,
            kind=event.asset_kind,
            materialized=event.materialized,
            critical=event.critical,
        )

        revisions.append(
            LineageAssetRevision(
                asset_external_id=event.asset_external_id,
                revision_external_id=event.revision_external_id,
                derived_from_snapshot_external_id=(
                    event.snapshot_external_id
                ),
            )
        )

        if not event.dependencies:
            snapshot_dependency = LineageDependency(
                upstream_external_id=(
                    event.snapshot_external_id
                ),
                downstream_external_id=(
                    event.asset_external_id
                ),
                dependency_type="derived_from",
            )
            snapshot_dependency_key = (
                snapshot_dependency.upstream_external_id,
                snapshot_dependency.downstream_external_id,
                snapshot_dependency.dependency_type,
            )
            dependencies[
                snapshot_dependency_key
            ] = snapshot_dependency

        for upstream in event.dependencies:
            if (
                upstream.asset_external_id
                not in current_asset_external_ids
            ):
                raise ValueError(
                    "Materialization dependency references "
                    "an asset with no current materialized state: "
                    f"{upstream.asset_external_id!r}."
                )

            logical_dependency = LineageDependency(
                upstream_external_id=(
                    upstream.asset_external_id
                ),
                downstream_external_id=(
                    event.asset_external_id
                ),
                dependency_type=upstream.dependency_type,
            )
            logical_key = (
                logical_dependency.upstream_external_id,
                logical_dependency.downstream_external_id,
                logical_dependency.dependency_type,
            )
            dependencies[logical_key] = logical_dependency

            revision_dependencies.append(
                LineageRevisionDependency(
                    upstream_asset_external_id=(
                        upstream.asset_external_id
                    ),
                    upstream_revision_external_id=(
                        upstream.revision_external_id
                    ),
                    downstream_asset_external_id=(
                        event.asset_external_id
                    ),
                    downstream_revision_external_id=(
                        event.revision_external_id
                    ),
                    dependency_type=upstream.dependency_type,
                )
            )

    return SnapshotLineageObservation(
        source_external_id=source_external_id,
        snapshot_external_id=current_snapshot_external_id,
        content_hash=content_hash,
        assets=tuple(
            sorted(
                assets.values(),
                key=lambda asset: (
                    asset.kind,
                    asset.external_id,
                ),
            )
        ),
        dependencies=tuple(
            sorted(
                dependencies.values(),
                key=lambda dependency: (
                    dependency.upstream_external_id,
                    dependency.downstream_external_id,
                    dependency.dependency_type,
                ),
            )
        ),
        asset_revisions=tuple(
            sorted(
                revisions,
                key=lambda revision: (
                    revision.asset_external_id,
                ),
            )
        ),
        revision_dependencies=tuple(
            sorted(
                revision_dependencies,
                key=lambda dependency: (
                    dependency.upstream_asset_external_id,
                    dependency.upstream_revision_external_id,
                    dependency.downstream_asset_external_id,
                    dependency.downstream_revision_external_id,
                    dependency.dependency_type,
                ),
            )
        ),
    )