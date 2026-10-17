from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from src.domain.lineage_revision import (
    LineageRevisionIdentity,
)
from src.services.lineage_projection_service import (
    load_exact_revision_dependents_batch,
)


@dataclass(frozen=True)
class LineageImpactPath:
    root: LineageRevisionIdentity
    target: LineageRevisionIdentity
    revisions: tuple[LineageRevisionIdentity, ...]


@dataclass(frozen=True)
class LineageImpactAnalysis:
    root: LineageRevisionIdentity
    impacted_revisions: tuple[LineageRevisionIdentity, ...]
    paths: tuple[LineageImpactPath, ...]


def analyze_lineage_impact(
    db: Session,
    *,
    organization_id: uuid.UUID,
    root: LineageRevisionIdentity,
) -> LineageImpactAnalysis:
    # Traverse exact historical revision dependencies, but return only the
    # revisions still present in the latest observation for their subject.
    # A current consumer can still depend on a retired intermediate revision.
    paths_by_revision: dict[
        LineageRevisionIdentity,
        tuple[LineageRevisionIdentity, ...],
    ] = {}
    actionable_paths: dict[
        LineageRevisionIdentity,
        tuple[LineageRevisionIdentity, ...],
    ] = {}
    visited = {root}
    frontier = (root,)

    while frontier:
        historical = load_exact_revision_dependents_batch(
            db,
            organization_id=organization_id,
            revisions=frontier,
            current_only=False,
        )
        current = load_exact_revision_dependents_batch(
            db,
            organization_id=organization_id,
            revisions=frontier,
            current_only=True,
        )
        current_by_revision = {
            revision: {
                LineageRevisionIdentity(
                    source_external_id=dependent.source_external_id,
                    asset_external_id=dependent.asset_external_id,
                    revision_external_id=dependent.revision_external_id,
                )
                for dependent in dependents
            }
            for revision, dependents in current.items()
        }

        next_paths: dict[
            LineageRevisionIdentity,
            tuple[LineageRevisionIdentity, ...],
        ] = {}

        for current_revision in frontier:
            current_path = (
                (root,)
                if current_revision == root
                else paths_by_revision[current_revision]
            )
            current_dependents = current_by_revision[current_revision]

            next_revisions = sorted(
                {
                    LineageRevisionIdentity(
                        source_external_id=dependent.source_external_id,
                        asset_external_id=dependent.asset_external_id,
                        revision_external_id=dependent.revision_external_id,
                    )
                    for dependent in historical[current_revision]
                },
                key=_identity_sort_key,
            )

            for next_revision in next_revisions:
                if next_revision == root:
                    continue
                path = current_path + (next_revision,)
                if next_revision in current_dependents:
                    # Historical intermediates are evidence, not actionable
                    # targets. Keep the shortest deterministic causal path.
                    actionable_paths.setdefault(next_revision, path)
                if (
                    next_revision in visited
                    or next_revision in next_paths
                ):
                    continue
                next_paths[next_revision] = path

        frontier = tuple(sorted(next_paths, key=_identity_sort_key))
        for revision in frontier:
            visited.add(revision)
            paths_by_revision[revision] = next_paths[revision]

    impacted_revisions = tuple(
        sorted(actionable_paths, key=_identity_sort_key)
    )
    paths = tuple(
        LineageImpactPath(
            root=root,
            target=revision,
            revisions=actionable_paths[revision],
        )
        for revision in impacted_revisions
    )
    return LineageImpactAnalysis(
        root=root,
        impacted_revisions=impacted_revisions,
        paths=paths,
    )


def _identity_sort_key(
    identity: LineageRevisionIdentity,
) -> tuple[str, str, str]:
    return (
        identity.source_external_id,
        identity.asset_external_id,
        identity.revision_external_id,
    )