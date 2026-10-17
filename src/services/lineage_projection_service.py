from __future__ import annotations

import uuid

from sqlalchemy import (
    and_,
    tuple_,
)
from sqlalchemy.orm import (
    Session,
    aliased,
    joinedload,
)

from src.domain.lineage_observation import (
    LineageObservation,
    LineageObservationKind,
    LineageRevisionReference,
)
from src.domain.lineage_revision import (
    LineageRevisionIdentity,
)
from src.models.lineage_observation import (
    LineageObservationDependencyRecord,
    LineageObservationRecord,
)


def load_current_lineage_observations(
    db: Session,
    *,
    organization_id: uuid.UUID,
    source_external_id: str | None = None,
) -> tuple[LineageObservation, ...]:
    newer = aliased(
        LineageObservationRecord
    )

    query = (
        db.query(
            LineageObservationRecord
        )
        .options(
            joinedload(
                LineageObservationRecord.dependencies
            )
        )
        .outerjoin(
            newer,
            and_(
                newer.organization_id
                == LineageObservationRecord.organization_id,
                newer.source_external_id
                == LineageObservationRecord.source_external_id,
                newer.asset_external_id
                == LineageObservationRecord.asset_external_id,
                newer.observation_kind
                == LineageObservationRecord.observation_kind,
                newer.sequence
                > LineageObservationRecord.sequence,
            ),
        )
        .filter(
            LineageObservationRecord.organization_id
            == organization_id,
            newer.id.is_(None),
        )
    )

    if source_external_id is not None:
        query = query.filter(
            LineageObservationRecord.source_external_id
            == source_external_id
        )

    records = (
        query.order_by(
            LineageObservationRecord.source_external_id.asc(),
            LineageObservationRecord.asset_external_id.asc(),
            LineageObservationRecord.observation_kind.asc(),
            LineageObservationRecord.sequence.asc(),
            LineageObservationRecord.id.asc(),
        )
        .all()
    )

    return tuple(
        _to_domain_observation(record)
        for record in records
    )


def load_current_lineage_observation(
    db: Session,
    *,
    organization_id: uuid.UUID,
    source_external_id: str,
    asset_external_id: str,
    observation_kind: LineageObservationKind,
) -> LineageObservation | None:
    newer = aliased(
        LineageObservationRecord
    )

    record = (
        db.query(
            LineageObservationRecord
        )
        .options(
            joinedload(
                LineageObservationRecord.dependencies
            )
        )
        .outerjoin(
            newer,
            and_(
                newer.organization_id
                == LineageObservationRecord.organization_id,
                newer.source_external_id
                == LineageObservationRecord.source_external_id,
                newer.asset_external_id
                == LineageObservationRecord.asset_external_id,
                newer.observation_kind
                == LineageObservationRecord.observation_kind,
                newer.sequence
                > LineageObservationRecord.sequence,
            ),
        )
        .filter(
            LineageObservationRecord.organization_id
            == organization_id,
            LineageObservationRecord.source_external_id
            == source_external_id,
            LineageObservationRecord.asset_external_id
            == asset_external_id,
            LineageObservationRecord.observation_kind
            == observation_kind,
            newer.id.is_(None),
        )
        .one_or_none()
    )

    if record is None:
        return None

    return _to_domain_observation(record)


def load_lineage_observation_by_sequence(
    db: Session,
    *,
    organization_id: uuid.UUID,
    source_external_id: str,
    asset_external_id: str,
    observation_kind: LineageObservationKind,
    sequence: int,
) -> LineageObservation | None:
    """Read one immutable observation by its tenant-scoped identity."""
    record = (
        db.query(LineageObservationRecord)
        .options(joinedload(LineageObservationRecord.dependencies))
        .filter(
            LineageObservationRecord.organization_id == organization_id,
            LineageObservationRecord.source_external_id == source_external_id,
            LineageObservationRecord.asset_external_id == asset_external_id,
            LineageObservationRecord.observation_kind == observation_kind,
            LineageObservationRecord.sequence == sequence,
        )
        .one_or_none()
    )
    return _to_domain_observation(record) if record is not None else None


def load_exact_revision_dependents(
    db: Session,
    *,
    organization_id: uuid.UUID,
    source_external_id: str,
    asset_external_id: str,
    revision_external_id: str,
    current_only: bool = True,
) -> tuple[LineageObservation, ...]:
    query = (
        db.query(
            LineageObservationRecord
        )
        .join(
            LineageObservationDependencyRecord,
            LineageObservationDependencyRecord.observation_id
            == LineageObservationRecord.id,
        )
        .options(
            joinedload(
                LineageObservationRecord.dependencies
            )
        )
        .filter(
            LineageObservationRecord.organization_id
            == organization_id,
            LineageObservationRecord.source_external_id
            == source_external_id,
            LineageObservationDependencyRecord.asset_external_id
            == asset_external_id,
            LineageObservationDependencyRecord.revision_external_id
            == revision_external_id,
        )
    )

    if current_only:
        newer = aliased(
            LineageObservationRecord
        )

        query = (
            query.outerjoin(
                newer,
                and_(
                    newer.organization_id
                    == LineageObservationRecord.organization_id,
                    newer.source_external_id
                    == LineageObservationRecord.source_external_id,
                    newer.asset_external_id
                    == LineageObservationRecord.asset_external_id,
                    newer.observation_kind
                    == LineageObservationRecord.observation_kind,
                    newer.sequence
                    > LineageObservationRecord.sequence,
                ),
            )
            .filter(
                newer.id.is_(None)
            )
        )

    records = (
        query.order_by(
            LineageObservationRecord.source_external_id.asc(),
            LineageObservationRecord.asset_external_id.asc(),
            LineageObservationRecord.observation_kind.asc(),
            LineageObservationRecord.sequence.asc(),
            LineageObservationRecord.id.asc(),
        )
        .all()
    )

    return tuple(
        _to_domain_observation(record)
        for record in records
    )


def load_exact_revision_dependents_batch(
    db: Session,
    *,
    organization_id: uuid.UUID,
    revisions: tuple[LineageRevisionIdentity, ...],
    current_only: bool = True,
) -> dict[
    LineageRevisionIdentity,
    tuple[LineageObservation, ...],
]:
    if not revisions:
        return {}

    normalized_revisions = tuple(
        sorted(
            set(revisions),
            key=_identity_sort_key,
        )
    )

    revision_keys = [
        (
            revision.source_external_id,
            revision.asset_external_id,
            revision.revision_external_id,
        )
        for revision in normalized_revisions
    ]

    query = (
        db.query(
            LineageObservationDependencyRecord.asset_external_id.label(
                "dependency_asset_external_id"
            ),
            LineageObservationDependencyRecord.revision_external_id.label(
                "dependency_revision_external_id"
            ),
            LineageObservationRecord,
        )
        .join(
            LineageObservationRecord,
            LineageObservationRecord.id
            == LineageObservationDependencyRecord.observation_id,
        )
        .options(
            joinedload(
                LineageObservationRecord.dependencies
            )
        )
        .filter(
            LineageObservationRecord.organization_id
            == organization_id,
            tuple_(
                LineageObservationRecord.source_external_id,
                LineageObservationDependencyRecord.asset_external_id,
                LineageObservationDependencyRecord.revision_external_id,
            ).in_(revision_keys),
        )
    )

    if current_only:
        newer = aliased(
            LineageObservationRecord
        )

        query = (
            query.outerjoin(
                newer,
                and_(
                    newer.organization_id
                    == LineageObservationRecord.organization_id,
                    newer.source_external_id
                    == LineageObservationRecord.source_external_id,
                    newer.asset_external_id
                    == LineageObservationRecord.asset_external_id,
                    newer.observation_kind
                    == LineageObservationRecord.observation_kind,
                    newer.sequence
                    > LineageObservationRecord.sequence,
                ),
            )
            .filter(
                newer.id.is_(None)
            )
        )

    rows = (
        query.order_by(
            LineageObservationRecord.source_external_id.asc(),
            LineageObservationDependencyRecord.asset_external_id.asc(),
            LineageObservationDependencyRecord.revision_external_id.asc(),
            LineageObservationRecord.asset_external_id.asc(),
            LineageObservationRecord.observation_kind.asc(),
            LineageObservationRecord.sequence.asc(),
            LineageObservationRecord.id.asc(),
        )
        .all()
    )

    dependents_by_revision: dict[
        LineageRevisionIdentity,
        list[LineageObservation],
    ] = {
        revision: []
        for revision in normalized_revisions
    }

    for (
        dependency_asset_external_id,
        dependency_revision_external_id,
        record,
    ) in rows:
        revision = LineageRevisionIdentity(
            source_external_id=record.source_external_id,
            asset_external_id=dependency_asset_external_id,
            revision_external_id=dependency_revision_external_id,
        )
        dependents_by_revision[revision].append(
            _to_domain_observation(record)
        )

    return {
        revision: tuple(dependents)
        for revision, dependents
        in dependents_by_revision.items()
    }


def load_lineage_revision(
    db: Session,
    *,
    organization_id: uuid.UUID,
    identity: LineageRevisionIdentity,
    observation_kind: LineageObservationKind | None = None,
) -> tuple[LineageObservation, ...]:
    query = (
        db.query(
            LineageObservationRecord
        )
        .options(
            joinedload(
                LineageObservationRecord.dependencies
            )
        )
        .filter(
            LineageObservationRecord.organization_id
            == organization_id,
            LineageObservationRecord.source_external_id
            == identity.source_external_id,
            LineageObservationRecord.asset_external_id
            == identity.asset_external_id,
            LineageObservationRecord.revision_external_id
            == identity.revision_external_id,
        )
    )

    if observation_kind is not None:
        query = query.filter(
            LineageObservationRecord.observation_kind
            == observation_kind
        )

    records = (
        query.order_by(
            LineageObservationRecord.observation_kind.asc(),
            LineageObservationRecord.sequence.asc(),
            LineageObservationRecord.id.asc(),
        )
        .all()
    )

    return tuple(
        _to_domain_observation(record)
        for record in records
    )


def _to_domain_observation(
    record: LineageObservationRecord,
) -> LineageObservation:
    dependencies = tuple(
        sorted(
            (
                LineageRevisionReference(
                    asset_external_id=(
                        dependency.asset_external_id
                    ),
                    revision_external_id=(
                        dependency.revision_external_id
                    ),
                    dependency_type=(
                        dependency.dependency_type
                    ),
                )
                for dependency in record.dependencies
            ),
            key=lambda dependency: (
                dependency.asset_external_id,
                dependency.revision_external_id,
                dependency.dependency_type,
            ),
        )
    )

    return LineageObservation(
        observation_kind=record.observation_kind,
        source_external_id=record.source_external_id,
        snapshot_external_id=record.snapshot_external_id,
        asset_external_id=record.asset_external_id,
        asset_kind=record.asset_kind,
        revision_external_id=record.revision_external_id,
        sequence=record.sequence,
        dependencies=dependencies,
    )


def _identity_sort_key(
    identity: LineageRevisionIdentity,
) -> tuple[str, str, str]:
    return (
        identity.source_external_id,
        identity.asset_external_id,
        identity.revision_external_id,
    )