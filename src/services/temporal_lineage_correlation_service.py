from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.domain.lineage_revision import LineageRevisionIdentity
from src.models.lineage_observation import LineageObservationRecord
from src.models.page_snapshot import PageSnapshot
from src.models.temporal_lineage_correlation import (
    TemporalLineageCorrelationRecord,
)


class TemporalLineageCorrelationNotFoundError(RuntimeError):
    """Raised when correlation evidence is outside the caller's tenant scope."""


@dataclass(frozen=True)
class PersistedTemporalLineageCorrelation:
    record: TemporalLineageCorrelationRecord
    created: bool


def persist_temporal_lineage_correlation(
    db: Session,
    *,
    organization_id: uuid.UUID,
    page_snapshot_id: uuid.UUID,
    lineage_observation_id: uuid.UUID,
) -> PersistedTemporalLineageCorrelation:
    _require_snapshot(
        db,
        organization_id=organization_id,
        page_snapshot_id=page_snapshot_id,
    )
    _require_observation(
        db,
        organization_id=organization_id,
        lineage_observation_id=lineage_observation_id,
    )

    existing = _load_correlation(
        db,
        page_snapshot_id=page_snapshot_id,
        lineage_observation_id=lineage_observation_id,
    )

    if existing is not None:
        return PersistedTemporalLineageCorrelation(
            record=existing,
            created=False,
        )

    record = TemporalLineageCorrelationRecord(
        page_snapshot_id=page_snapshot_id,
        lineage_observation_id=lineage_observation_id,
    )

    try:
        with db.begin_nested():
            db.add(record)
            db.flush()
    except IntegrityError:
        existing = _load_correlation(
            db,
            page_snapshot_id=page_snapshot_id,
            lineage_observation_id=lineage_observation_id,
        )

        if existing is None:
            raise

        return PersistedTemporalLineageCorrelation(
            record=existing,
            created=False,
        )

    return PersistedTemporalLineageCorrelation(
        record=record,
        created=True,
    )


def resolve_temporal_lineage_roots(
    db: Session,
    *,
    organization_id: uuid.UUID,
    page_snapshot_id: uuid.UUID,
) -> tuple[LineageRevisionIdentity, ...]:
    _require_snapshot(
        db,
        organization_id=organization_id,
        page_snapshot_id=page_snapshot_id,
    )

    rows = (
        db.query(LineageObservationRecord)
        .join(
            TemporalLineageCorrelationRecord,
            (
                TemporalLineageCorrelationRecord.lineage_observation_id
                == LineageObservationRecord.id
            ),
        )
        .filter(
            TemporalLineageCorrelationRecord.page_snapshot_id
            == page_snapshot_id,
            LineageObservationRecord.organization_id
            == organization_id,
        )
        .order_by(
            LineageObservationRecord.source_external_id.asc(),
            LineageObservationRecord.asset_external_id.asc(),
            LineageObservationRecord.revision_external_id.asc(),
            LineageObservationRecord.id.asc(),
        )
        .all()
    )

    roots: list[LineageRevisionIdentity] = []
    seen: set[tuple[str, str, str]] = set()

    for row in rows:
        key = (
            row.source_external_id,
            row.asset_external_id,
            row.revision_external_id,
        )

        if key in seen:
            continue

        seen.add(key)
        roots.append(
            LineageRevisionIdentity(
                source_external_id=row.source_external_id,
                asset_external_id=row.asset_external_id,
                revision_external_id=row.revision_external_id,
            )
        )

    return tuple(roots)


def _require_snapshot(
    db: Session,
    *,
    organization_id: uuid.UUID,
    page_snapshot_id: uuid.UUID,
) -> PageSnapshot:
    snapshot = (
        db.query(PageSnapshot)
        .filter(
            PageSnapshot.id == page_snapshot_id,
            PageSnapshot.organization_id == organization_id,
        )
        .one_or_none()
    )

    if snapshot is None:
        raise TemporalLineageCorrelationNotFoundError(
            "Page snapshot is not available in the organization."
        )

    return snapshot


def _require_observation(
    db: Session,
    *,
    organization_id: uuid.UUID,
    lineage_observation_id: uuid.UUID,
) -> LineageObservationRecord:
    observation = (
        db.query(LineageObservationRecord)
        .filter(
            LineageObservationRecord.id == lineage_observation_id,
            LineageObservationRecord.organization_id
            == organization_id,
        )
        .one_or_none()
    )

    if observation is None:
        raise TemporalLineageCorrelationNotFoundError(
            "Lineage observation is not available in the organization."
        )

    return observation


def _load_correlation(
    db: Session,
    *,
    page_snapshot_id: uuid.UUID,
    lineage_observation_id: uuid.UUID,
) -> TemporalLineageCorrelationRecord | None:
    return (
        db.query(TemporalLineageCorrelationRecord)
        .filter(
            TemporalLineageCorrelationRecord.page_snapshot_id
            == page_snapshot_id,
            TemporalLineageCorrelationRecord.lineage_observation_id
            == lineage_observation_id,
        )
        .one_or_none()
    )