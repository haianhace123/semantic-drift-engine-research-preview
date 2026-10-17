from __future__ import annotations

from dataclasses import dataclass

from benchmarks.temporal_knowledge_integrity.dependency_impact import (
    DependencyEdge,
    DependencyGraph,
    KnowledgeNode,
    KnowledgeNodeKind,
)


@dataclass(frozen=True)
class LineageAsset:
    external_id: str
    kind: KnowledgeNodeKind
    materialized: bool = False
    critical: bool = False

@dataclass(frozen=True)
class LineageAssetRevision:
    asset_external_id: str
    revision_external_id: str
    derived_from_snapshot_external_id: str

@dataclass(frozen=True)
class LineageDependency:
    upstream_external_id: str
    downstream_external_id: str
    dependency_type: str


@dataclass(frozen=True)
class LineageRevisionDependency:
    upstream_asset_external_id: str
    upstream_revision_external_id: str
    downstream_asset_external_id: str
    downstream_revision_external_id: str
    dependency_type: str


@dataclass(frozen=True)
class SnapshotLineageObservation:
    source_external_id: str
    snapshot_external_id: str
    content_hash: str
    assets: tuple[LineageAsset, ...]
    dependencies: tuple[LineageDependency, ...]
    asset_revisions: tuple[LineageAssetRevision, ...] = ()
    revision_dependencies: tuple[
        LineageRevisionDependency,
        ...,
    ] = ()


@dataclass(frozen=True)
class AdaptedAssetRevision:
    asset_node_id: str
    revision_external_id: str
    derived_from_snapshot_node_id: str


@dataclass(frozen=True)
class AdaptedRevisionDependency:
    upstream_asset_node_id: str
    upstream_revision_external_id: str
    downstream_asset_node_id: str
    downstream_revision_external_id: str
    dependency_type: str


@dataclass(frozen=True)
class AdaptedSnapshotLineage:
    source_node_id: str
    snapshot_node_id: str
    content_hash: str
    graph: DependencyGraph
    asset_revisions: tuple[AdaptedAssetRevision, ...]
    revision_dependencies: tuple[
        AdaptedRevisionDependency,
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


def _normalize_content_hash(
    value: str,
) -> str:
    normalized = value.strip().lower()

    if len(normalized) != 64:
        raise ValueError(
            "content_hash must contain exactly 64 hexadecimal characters."
        )

    try:
        int(normalized, 16)
    except ValueError as exc:
        raise ValueError(
            "content_hash must contain exactly 64 hexadecimal characters."
        ) from exc

    return normalized


def _node_id(
    *,
    kind: KnowledgeNodeKind,
    external_id: str,
) -> str:
    return f"{kind}:{external_id}"


def adapt_snapshot_lineage(
    observation: SnapshotLineageObservation,
) -> AdaptedSnapshotLineage:
    source_external_id = _normalize_required_identifier(
        observation.source_external_id,
        field_name="source_external_id",
    )
    snapshot_external_id = _normalize_required_identifier(
        observation.snapshot_external_id,
        field_name="snapshot_external_id",
    )
    content_hash = _normalize_content_hash(
        observation.content_hash
    )

    assets_by_external_id: dict[
        str,
        LineageAsset,
    ] = {}

    for asset in observation.assets:
        external_id = _normalize_required_identifier(
            asset.external_id,
            field_name="asset external_id",
        )

        if external_id in assets_by_external_id:
            raise ValueError(
                "Duplicate lineage asset external_id: "
                f"{external_id!r}."
            )

        assets_by_external_id[external_id] = LineageAsset(
            external_id=external_id,
            kind=asset.kind,
            materialized=asset.materialized,
            critical=asset.critical,
        )

    source_asset = assets_by_external_id.get(
        source_external_id
    )
    if source_asset is None:
        raise ValueError(
            "source_external_id must reference a lineage asset."
        )

    if source_asset.kind != "source":
        raise ValueError(
            "source_external_id must reference a source asset."
        )

    snapshot_asset = assets_by_external_id.get(
        snapshot_external_id
    )
    if snapshot_asset is None:
        raise ValueError(
            "snapshot_external_id must reference a lineage asset."
        )

    if snapshot_asset.kind != "snapshot":
        raise ValueError(
            "snapshot_external_id must reference a snapshot asset."
        )

    node_ids_by_external_id = {
        external_id: _node_id(
            kind=asset.kind,
            external_id=external_id,
        )
        for external_id, asset
        in assets_by_external_id.items()
    }

    nodes = tuple(
        sorted(
            (
                KnowledgeNode(
                    id=node_ids_by_external_id[
                        external_id
                    ],
                    kind=asset.kind,
                    materialized=asset.materialized,
                    critical=asset.critical,
                )
                for external_id, asset
                in assets_by_external_id.items()
            ),
            key=lambda node: node.id,
        )
    )

    edges: list[DependencyEdge] = []
    seen_dependencies: set[
        tuple[str, str, str]
    ] = set()

    for dependency in observation.dependencies:
        upstream_external_id = (
            _normalize_required_identifier(
                dependency.upstream_external_id,
                field_name=(
                    "dependency upstream_external_id"
                ),
            )
        )
        downstream_external_id = (
            _normalize_required_identifier(
                dependency.downstream_external_id,
                field_name=(
                    "dependency downstream_external_id"
                ),
            )
        )
        dependency_type = (
            _normalize_required_identifier(
                dependency.dependency_type,
                field_name="dependency_type",
            )
        )

        if (
            upstream_external_id
            not in assets_by_external_id
        ):
            raise ValueError(
                "Lineage dependency references unknown "
                "upstream asset: "
                f"{upstream_external_id!r}."
            )

        if (
            downstream_external_id
            not in assets_by_external_id
        ):
            raise ValueError(
                "Lineage dependency references unknown "
                "downstream asset: "
                f"{downstream_external_id!r}."
            )

        dependency_key = (
            upstream_external_id,
            downstream_external_id,
            dependency_type,
        )

        if dependency_key in seen_dependencies:
            raise ValueError(
                "Duplicate lineage dependency: "
                f"{dependency_key!r}."
            )

        seen_dependencies.add(
            dependency_key
        )

        edges.append(
            DependencyEdge(
                upstream_node_id=(
                    node_ids_by_external_id[
                        upstream_external_id
                    ]
                ),
                downstream_node_id=(
                    node_ids_by_external_id[
                        downstream_external_id
                    ]
                ),
                dependency_type=dependency_type,
            )
        )

    graph = DependencyGraph(
        nodes=nodes,
        edges=tuple(
            sorted(
                edges,
                key=lambda edge: (
                    edge.upstream_node_id,
                    edge.downstream_node_id,
                    edge.dependency_type,
                ),
            )
        ),
    )

    adapted_revisions: list[
        AdaptedAssetRevision
    ] = []
    seen_revision_assets: set[str] = set()

    for revision in observation.asset_revisions:
        asset_external_id = (
            _normalize_required_identifier(
                revision.asset_external_id,
                field_name=(
                    "revision asset_external_id"
                ),
            )
        )
        revision_external_id = (
            _normalize_required_identifier(
                revision.revision_external_id,
                field_name=(
                    "revision revision_external_id"
                ),
            )
        )
        derived_from_snapshot_external_id = (
            _normalize_required_identifier(
                revision.derived_from_snapshot_external_id,
                field_name=(
                    "revision "
                    "derived_from_snapshot_external_id"
                ),
            )
        )

        asset = assets_by_external_id.get(
            asset_external_id
        )
        if asset is None:
            raise ValueError(
                "Asset revision references unknown "
                "asset: "
                f"{asset_external_id!r}."
            )

        if asset.kind in (
            "source",
            "snapshot",
        ):
            raise ValueError(
                "Asset revisions may only reference "
                "derived assets."
            )

        if (
            derived_from_snapshot_external_id
            not in assets_by_external_id
        ):
            raise ValueError(
                "Asset revision references unknown "
                "snapshot: "
                f"{derived_from_snapshot_external_id!r}."
            )

        derived_snapshot_asset = (
            assets_by_external_id[
                derived_from_snapshot_external_id
            ]
        )
        if derived_snapshot_asset.kind != "snapshot":
            raise ValueError(
                "Asset revision provenance must "
                "reference a snapshot asset."
            )

        if asset_external_id in seen_revision_assets:
            raise ValueError(
                "Duplicate asset revision binding "
                "for asset: "
                f"{asset_external_id!r}."
            )

        seen_revision_assets.add(
            asset_external_id
        )

        adapted_revisions.append(
            AdaptedAssetRevision(
                asset_node_id=(
                    node_ids_by_external_id[
                        asset_external_id
                    ]
                ),
                revision_external_id=(
                    revision_external_id
                ),
                derived_from_snapshot_node_id=(
                    node_ids_by_external_id[
                        derived_from_snapshot_external_id
                    ]
                ),
            )
        )

    asset_revisions = tuple(
        sorted(
            adapted_revisions,
            key=lambda revision: (
                revision.asset_node_id,
                revision.revision_external_id,
                revision.derived_from_snapshot_node_id,
            ),
        )
    )

    current_revision_ids_by_asset = {
        revision.asset_node_id: revision.revision_external_id
        for revision in asset_revisions
    }

    adapted_revision_dependencies: list[
        AdaptedRevisionDependency
    ] = []
    seen_revision_dependencies: set[
        tuple[str, str, str, str, str]
    ] = set()

    for dependency in observation.revision_dependencies:
        upstream_asset_external_id = (
            _normalize_required_identifier(
                dependency.upstream_asset_external_id,
                field_name=(
                    "revision dependency "
                    "upstream_asset_external_id"
                ),
            )
        )
        upstream_revision_external_id = (
            _normalize_required_identifier(
                dependency.upstream_revision_external_id,
                field_name=(
                    "revision dependency "
                    "upstream_revision_external_id"
                ),
            )
        )
        downstream_asset_external_id = (
            _normalize_required_identifier(
                dependency.downstream_asset_external_id,
                field_name=(
                    "revision dependency "
                    "downstream_asset_external_id"
                ),
            )
        )
        downstream_revision_external_id = (
            _normalize_required_identifier(
                dependency.downstream_revision_external_id,
                field_name=(
                    "revision dependency "
                    "downstream_revision_external_id"
                ),
            )
        )
        dependency_type = (
            _normalize_required_identifier(
                dependency.dependency_type,
                field_name=(
                    "revision dependency dependency_type"
                ),
            )
        )

        upstream_asset = assets_by_external_id.get(
            upstream_asset_external_id
        )
        if upstream_asset is None:
            raise ValueError(
                "Revision dependency references unknown "
                "upstream asset: "
                f"{upstream_asset_external_id!r}."
            )

        downstream_asset = assets_by_external_id.get(
            downstream_asset_external_id
        )
        if downstream_asset is None:
            raise ValueError(
                "Revision dependency references unknown "
                "downstream asset: "
                f"{downstream_asset_external_id!r}."
            )

        if upstream_asset.kind in (
            "source",
            "snapshot",
        ):
            raise ValueError(
                "Revision dependency upstream must "
                "reference a derived asset."
            )

        if downstream_asset.kind in (
            "source",
            "snapshot",
        ):
            raise ValueError(
                "Revision dependency downstream must "
                "reference a derived asset."
            )

        upstream_asset_node_id = (
            node_ids_by_external_id[
                upstream_asset_external_id
            ]
        )
        downstream_asset_node_id = (
            node_ids_by_external_id[
                downstream_asset_external_id
            ]
        )

        current_downstream_revision_id = (
            current_revision_ids_by_asset.get(
                downstream_asset_node_id
            )
        )
        if current_downstream_revision_id is None:
            raise ValueError(
                "Revision dependency downstream asset "
                "must have an observed revision: "
                f"{downstream_asset_external_id!r}."
            )

        if (
            downstream_revision_external_id
            != current_downstream_revision_id
        ):
            raise ValueError(
                "Revision dependency downstream revision "
                "must match the observed asset revision."
            )

        dependency_key = (
            upstream_asset_node_id,
            upstream_revision_external_id,
            downstream_asset_node_id,
            downstream_revision_external_id,
            dependency_type,
        )

        if dependency_key in seen_revision_dependencies:
            raise ValueError(
                "Duplicate revision dependency: "
                f"{dependency_key!r}."
            )

        seen_revision_dependencies.add(
            dependency_key
        )

        adapted_revision_dependencies.append(
            AdaptedRevisionDependency(
                upstream_asset_node_id=(
                    upstream_asset_node_id
                ),
                upstream_revision_external_id=(
                    upstream_revision_external_id
                ),
                downstream_asset_node_id=(
                    downstream_asset_node_id
                ),
                downstream_revision_external_id=(
                    downstream_revision_external_id
                ),
                dependency_type=dependency_type,
            )
        )

    revision_dependencies = tuple(
        sorted(
            adapted_revision_dependencies,
            key=lambda dependency: (
                dependency.upstream_asset_node_id,
                dependency.upstream_revision_external_id,
                dependency.downstream_asset_node_id,
                dependency.downstream_revision_external_id,
                dependency.dependency_type,
            ),
        )
    )

    return AdaptedSnapshotLineage(
        source_node_id=(
            node_ids_by_external_id[
                source_external_id
            ]
        ),
        snapshot_node_id=(
            node_ids_by_external_id[
                snapshot_external_id
            ]
        ),
        content_hash=content_hash,
        graph=graph,
        asset_revisions=asset_revisions,
        revision_dependencies=revision_dependencies,
    )