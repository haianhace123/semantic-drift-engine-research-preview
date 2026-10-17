from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.domain.lineage_observation import (
    LineageObservation,
    LineageRevisionReference,
)
from src.models.lineage_observation import (
    LineageObservationDependencyRecord,
    LineageObservationRecord,
)
from src.services.lineage_authority_lock_service import (
    acquire_lineage_target_authority_lock,
)


class LineageObservationConflictError(RuntimeError):
    """Raised when one immutable observation identity has conflicting facts."""


@dataclass(frozen=True)
class PersistedLineageObservation:
    record: LineageObservationRecord
    created: bool


def persist_lineage_observation(
    db: Session,
    *,
    organization_id: uuid.UUID,
    observation: LineageObservation,
) -> PersistedLineageObservation:
    if observation.observation_kind == "materialization":
        acquire_lineage_target_authority_lock(
            db,
            organization_id=organization_id,
            source_external_id=observation.source_external_id,
            asset_external_id=observation.asset_external_id,
        )

    existing = _load_observation(
        db,
        organization_id=organization_id,
        observation=observation,
    )

    if existing is not None:
        _assert_same_payload(
            existing,
            observation,
        )
        return PersistedLineageObservation(
            record=existing,
            created=False,
        )

    record = _build_record(
        organization_id=organization_id,
        observation=observation,
    )

    try:
        with db.begin_nested():
            db.add(record)
            db.flush()
    except IntegrityError:
        existing = _load_observation(
            db,
            organization_id=organization_id,
            observation=observation,
        )

        if existing is None:
            raise

        _assert_same_payload(
            existing,
            observation,
        )
        return PersistedLineageObservation(
            record=existing,
            created=False,
        )

    return PersistedLineageObservation(
        record=record,
        created=True,
    )


def _load_observation(
    db: Session,
    *,
    organization_id: uuid.UUID,
    observation: LineageObservation,
) -> LineageObservationRecord | None:
    return (
        db.query(LineageObservationRecord)
        .filter(
            LineageObservationRecord.organization_id
            == organization_id,
            LineageObservationRecord.source_external_id
            == observation.source_external_id,
            LineageObservationRecord.asset_external_id
            == observation.asset_external_id,
            LineageObservationRecord.observation_kind
            == observation.observation_kind,
            LineageObservationRecord.sequence
            == observation.sequence,
        )
        .one_or_none()
    )


def _build_record(
    *,
    organization_id: uuid.UUID,
    observation: LineageObservation,
) -> LineageObservationRecord:
    record = LineageObservationRecord(
        organization_id=organization_id,
        observation_kind=observation.observation_kind,
        source_external_id=observation.source_external_id,
        snapshot_external_id=observation.snapshot_external_id,
        asset_external_id=observation.asset_external_id,
        asset_kind=observation.asset_kind,
        revision_external_id=observation.revision_external_id,
        sequence=observation.sequence,
    )

    record.dependencies = [
        LineageObservationDependencyRecord(
            asset_external_id=dependency.asset_external_id,
            revision_external_id=dependency.revision_external_id,
            dependency_type=dependency.dependency_type,
        )
        for dependency in _canonical_dependencies(
            observation.dependencies
        )
    ]

    return record


def _assert_same_payload(
    record: LineageObservationRecord,
    observation: LineageObservation,
) -> None:
    persisted_payload = (
        record.snapshot_external_id,
        record.asset_kind,
        record.revision_external_id,
        _canonical_persisted_dependencies(
            record.dependencies
        ),
    )
    incoming_payload = (
        observation.snapshot_external_id,
        observation.asset_kind,
        observation.revision_external_id,
        _canonical_dependencies(
            observation.dependencies
        ),
    )

    if persisted_payload != incoming_payload:
        raise LineageObservationConflictError(
            "Conflicting lineage observations share the same "
            "organization-scoped subject sequence."
        )


def _canonical_dependencies(
    dependencies: tuple[LineageRevisionReference, ...],
) -> tuple[LineageRevisionReference, ...]:
    return tuple(
        sorted(
            dependencies,
            key=lambda dependency: (
                dependency.asset_external_id,
                dependency.revision_external_id,
                dependency.dependency_type,
            ),
        )
    )


def _canonical_persisted_dependencies(
    dependencies: list[LineageObservationDependencyRecord],
) -> tuple[LineageRevisionReference, ...]:
    return _canonical_dependencies(
        tuple(
            LineageRevisionReference(
                asset_external_id=dependency.asset_external_id,
                revision_external_id=dependency.revision_external_id,
                dependency_type=dependency.dependency_type,
            )
            for dependency in dependencies
        )
    )