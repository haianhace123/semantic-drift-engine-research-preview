from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

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
class MaterializedRevisionDependency:
    asset_external_id: str
    revision_external_id: str
    dependency_type: str = "derived_from"


@dataclass(frozen=True)
class MaterializedAssetObservation:
    asset_external_id: str
    asset_kind: KnowledgeNodeKind
    revision_external_id: str
    derived_from_snapshot_external_id: str
    materialized: bool = True
    critical: bool = False
    dependencies: tuple[str, ...] | None = None
    revision_dependencies: tuple[
        MaterializedRevisionDependency,
        ...,
    ] = ()


def _normalize_required_identifier(
    value: str,
    *,
    field_name: str,
) -> str:
    normalized = value.strip()

    if not normalized:
        raise ValueError(
            f"{field_name} must not be empty."
        )

    return normalized


def build_snapshot_lineage_observation(
    *,
    source_external_id: str,
    snapshot_external_id: str,
    content_hash: str,
    materializations: Iterable[
        MaterializedAssetObservation
    ],
    historical_snapshot_external_ids: Iterable[str] = (),
) -> SnapshotLineageObservation:
    normalized_source_external_id = (
        _normalize_required_identifier(
            source_external_id,
            field_name="source_external_id",
        )
    )
    normalized_snapshot_external_id = (
        _normalize_required_identifier(
            snapshot_external_id,
            field_name="snapshot_external_id",
        )
    )

    materialization_values = tuple(
        materializations
    )

    assets: dict[str, LineageAsset] = {
        normalized_source_external_id: LineageAsset(
            external_id=normalized_source_external_id,
            kind="source",
        ),
        normalized_snapshot_external_id: LineageAsset(
            external_id=normalized_snapshot_external_id,
            kind="snapshot",
        ),
    }

    for historical_snapshot_value in (
        historical_snapshot_external_ids
    ):
        historical_snapshot_external_id = (
            _normalize_required_identifier(
                historical_snapshot_value,
                field_name=(
                    "historical_snapshot_external_id"
                ),
            )
        )

        if (
            historical_snapshot_external_id
            == normalized_snapshot_external_id
        ):
            raise ValueError(
                "Historical snapshot must differ from "
                "the snapshot being observed."
            )

        if historical_snapshot_external_id in assets:
            raise ValueError(
                "Duplicate historical snapshot_external_id: "
                f"{historical_snapshot_external_id!r}."
            )

        assets[historical_snapshot_external_id] = LineageAsset(
            external_id=historical_snapshot_external_id,
            kind="snapshot",
        )

    dependencies: dict[
        tuple[str, str, str],
        LineageDependency,
    ] = {}

    revisions: dict[
        str,
        LineageAssetRevision,
    ] = {}

    revision_dependencies: dict[
        tuple[str, str, str, str, str],
        LineageRevisionDependency,
    ] = {}

    source_snapshot_dependency = LineageDependency(
        upstream_external_id=(
            normalized_source_external_id
        ),
        downstream_external_id=(
            normalized_snapshot_external_id
        ),
        dependency_type="version_of",
    )
    dependencies[
        (
            source_snapshot_dependency.upstream_external_id,
            source_snapshot_dependency.downstream_external_id,
            source_snapshot_dependency.dependency_type,
        )
    ] = source_snapshot_dependency

    for materialization in materialization_values:
        asset_external_id = (
            _normalize_required_identifier(
                materialization.asset_external_id,
                field_name="asset_external_id",
            )
        )
        revision_external_id = (
            _normalize_required_identifier(
                materialization.revision_external_id,
                field_name="revision_external_id",
            )
        )
        derived_from_snapshot_external_id = (
            _normalize_required_identifier(
                materialization.derived_from_snapshot_external_id,
                field_name=(
                    "derived_from_snapshot_external_id"
                ),
            )
        )
        asset_kind = (
            _normalize_required_identifier(
                materialization.asset_kind,
                field_name="asset_kind",
            )
        )

        if asset_kind in (
            "source",
            "snapshot",
        ):
            raise ValueError(
                "Materialization observations may only "
                "describe derived assets."
            )

        if asset_external_id in assets:
            raise ValueError(
                "Duplicate materialized asset_external_id: "
                f"{asset_external_id!r}."
            )

        assets[asset_external_id] = LineageAsset(
            external_id=asset_external_id,
            kind=asset_kind,
            materialized=materialization.materialized,
            critical=materialization.critical,
        )

        revisions[asset_external_id] = (
            LineageAssetRevision(
                asset_external_id=asset_external_id,
                revision_external_id=(
                    revision_external_id
                ),
                derived_from_snapshot_external_id=(
                    derived_from_snapshot_external_id
                ),
            )
        )

    for materialization in materialization_values:
        downstream_external_id = (
            materialization.asset_external_id.strip()
        )

        upstream_external_ids = (
            materialization.dependencies
            if materialization.dependencies is not None
            else (
                normalized_snapshot_external_id,
            )
        )

        seen_upstream_external_ids: set[str] = set()

        for upstream_value in upstream_external_ids:
            upstream_external_id = (
                _normalize_required_identifier(
                    upstream_value,
                    field_name=(
                        "dependency upstream_external_id"
                    ),
                )
            )

            if (
                upstream_external_id
                in seen_upstream_external_ids
            ):
                raise ValueError(
                    "Duplicate materialization dependency: "
                    f"{upstream_external_id!r}."
                )

            seen_upstream_external_ids.add(
                upstream_external_id
            )

            if upstream_external_id not in assets:
                raise ValueError(
                    "Materialization dependency references "
                    "unknown asset: "
                    f"{upstream_external_id!r}."
                )

            dependency = LineageDependency(
                upstream_external_id=(
                    upstream_external_id
                ),
                downstream_external_id=(
                    downstream_external_id
                ),
                dependency_type="derived_from",
            )
            dependency_key = (
                dependency.upstream_external_id,
                dependency.downstream_external_id,
                dependency.dependency_type,
            )

            if dependency_key in dependencies:
                raise ValueError(
                    "Duplicate lineage dependency: "
                    f"{dependency_key!r}."
                )

            dependencies[
                dependency_key
            ] = dependency

        downstream_revision_external_id = (
            _normalize_required_identifier(
                materialization.revision_external_id,
                field_name="revision_external_id",
            )
        )

        for revision_dependency in (
            materialization.revision_dependencies
        ):
            upstream_asset_external_id = (
                _normalize_required_identifier(
                    revision_dependency.asset_external_id,
                    field_name=(
                        "revision dependency "
                        "asset_external_id"
                    ),
                )
            )
            upstream_revision_external_id = (
                _normalize_required_identifier(
                    revision_dependency.revision_external_id,
                    field_name=(
                        "revision dependency "
                        "revision_external_id"
                    ),
                )
            )
            dependency_type = (
                _normalize_required_identifier(
                    revision_dependency.dependency_type,
                    field_name=(
                        "revision dependency dependency_type"
                    ),
                )
            )

            if upstream_asset_external_id not in assets:
                raise ValueError(
                    "Revision dependency references "
                    "unknown asset: "
                    f"{upstream_asset_external_id!r}."
                )

            dependency = LineageRevisionDependency(
                upstream_asset_external_id=(
                    upstream_asset_external_id
                ),
                upstream_revision_external_id=(
                    upstream_revision_external_id
                ),
                downstream_asset_external_id=(
                    downstream_external_id
                ),
                downstream_revision_external_id=(
                    downstream_revision_external_id
                ),
                dependency_type=dependency_type,
            )
            dependency_key = (
                dependency.upstream_asset_external_id,
                dependency.upstream_revision_external_id,
                dependency.downstream_asset_external_id,
                dependency.downstream_revision_external_id,
                dependency.dependency_type,
            )

            if dependency_key in revision_dependencies:
                raise ValueError(
                    "Duplicate materialization revision "
                    "dependency: "
                    f"{dependency_key!r}."
                )

            revision_dependencies[
                dependency_key
            ] = dependency

    return SnapshotLineageObservation(
        source_external_id=(
            normalized_source_external_id
        ),
        snapshot_external_id=(
            normalized_snapshot_external_id
        ),
        content_hash=content_hash,
        assets=tuple(
            sorted(
                assets.values(),
                key=lambda asset: (
                    asset.external_id
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
                revisions.values(),
                key=lambda revision: (
                    revision.asset_external_id
                ),
            )
        ),
        revision_dependencies=tuple(
            sorted(
                revision_dependencies.values(),
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