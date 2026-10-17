from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from sqlalchemy import case, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.models.entity import Entity
from src.models.entity_relationship import (
    EVIDENCE_METHOD_DETERMINISTIC_V1,
    RELATIONSHIP_TYPE_MENTIONED,
    EntityRelationship,
)
from src.models.entity_relationship_observation import (
    EntityRelationshipObservation,
)
from src.models.semantic_drift_event import SemanticDriftEvent


@dataclass(frozen=True)
class GraphPersistenceResult:
    created: int
    updated: int
    skipped: int
    relationship_ids: tuple[str, ...]
    observations_created: int = 0
    observations_existing: int = 0


def _as_uuid(value: uuid.UUID | str) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value

    return uuid.UUID(str(value))


def _event_seen_at(event: SemanticDriftEvent) -> datetime:
    """
    Return the semantic observation time for a drift event.

    Graph chronology follows the event timestamp, not worker execution time.
    """
    if event.detected_at is None:
        raise ValueError(
            "Semantic drift event must have detected_at before "
            "graph persistence."
        )

    return event.detected_at


def _get_relationship(
    *,
    db: Session,
    organization_id: uuid.UUID,
    source_entity_id: uuid.UUID,
    target_entity_id: uuid.UUID,
    relationship_type: str,
) -> EntityRelationship:
    relationship = (
        db.query(EntityRelationship)
        .filter(
            EntityRelationship.organization_id == organization_id,
            EntityRelationship.source_entity_id == source_entity_id,
            EntityRelationship.target_entity_id == target_entity_id,
            EntityRelationship.relationship_type == relationship_type,
        )
        .one_or_none()
    )

    if relationship is None:
        raise RuntimeError(
            "Entity relationship could not be loaded after upsert."
        )

    return relationship


def _ensure_relationship(
    *,
    db: Session,
    organization_id: uuid.UUID,
    source_entity_id: uuid.UUID,
    target_entity_id: uuid.UUID,
    event_id: uuid.UUID,
    relationship_type: str,
    evidence_method: str,
    seen_at: datetime,
) -> tuple[EntityRelationship, bool]:
    """
    Atomically ensure the aggregate relationship exists.

    Returns:
        (relationship, created_now)

    PostgreSQL owns conflict arbitration through the unique edge
    constraint. This avoids the SELECT -> INSERT race.
    """
    statement = (
        insert(EntityRelationship)
        .values(
            id=uuid.uuid4(),
            organization_id=organization_id,
            source_entity_id=source_entity_id,
            target_entity_id=target_entity_id,
            first_event_id=event_id,
            last_event_id=event_id,
            relationship_type=relationship_type,
            evidence_method=evidence_method,
            evidence_count=1,
            strength=1.0,
            confidence=1.0,
            first_seen_at=seen_at,
            last_seen_at=seen_at,
            is_active=True,
        )
        .on_conflict_do_nothing(
            constraint="uq_entity_relationship_edge"
        )
        .returning(EntityRelationship.id)
    )

    created_id = db.execute(statement).scalar_one_or_none()

    relationship = _get_relationship(
        db=db,
        organization_id=organization_id,
        source_entity_id=source_entity_id,
        target_entity_id=target_entity_id,
        relationship_type=relationship_type,
    )

    return relationship, created_id is not None


def _create_observation_if_missing(
    *,
    db: Session,
    organization_id: uuid.UUID,
    relationship_id: uuid.UUID,
    event_id: uuid.UUID,
    source_entity_id: uuid.UUID,
    target_entity_id: uuid.UUID,
    relationship_type: str,
    evidence_method: str,
    seen_at: datetime,
) -> bool:
    """
    Atomically create immutable event-level relationship evidence.

    Returns True only when this event/edge observation was inserted.

    The database unique constraint makes concurrent retries idempotent.
    """
    statement = (
        insert(EntityRelationshipObservation)
        .values(
            id=uuid.uuid4(),
            organization_id=organization_id,
            relationship_id=relationship_id,
            semantic_drift_event_id=event_id,
            source_entity_id=source_entity_id,
            target_entity_id=target_entity_id,
            relationship_type=relationship_type,
            evidence_method=evidence_method,
            observed_at=seen_at,
        )
        .on_conflict_do_nothing(
            constraint="uq_entity_relationship_observation_event"
        )
        .returning(EntityRelationshipObservation.id)
    )

    observation_id = db.execute(statement).scalar_one_or_none()

    return observation_id is not None

def _update_existing_relationship_from_observation(
    *,
    db: Session,
    relationship_id: uuid.UUID,
    event_id: uuid.UUID,
    evidence_method: str,
    seen_at: datetime,
) -> None:
    """
    Atomically apply one newly inserted observation to an existing edge.

    Observation insertion is the idempotency gate. This update is called
    only when the current event/edge observation was inserted successfully.

    All aggregate mutations happen in one PostgreSQL UPDATE so concurrent
    events cannot lose evidence_count increments through ORM read-modify-write.
    """
    statement = (
        update(EntityRelationship)
        .where(
            EntityRelationship.id == relationship_id
        )
        .values(
            evidence_count=EntityRelationship.evidence_count + 1,
            evidence_method=evidence_method,
            is_active=True,
            first_seen_at=case(
                (
                    seen_at < EntityRelationship.first_seen_at,
                    seen_at,
                ),
                else_=EntityRelationship.first_seen_at,
            ),
            first_event_id=case(
                (
                    seen_at < EntityRelationship.first_seen_at,
                    event_id,
                ),
                else_=EntityRelationship.first_event_id,
            ),
            last_seen_at=case(
                (
                    seen_at >= EntityRelationship.last_seen_at,
                    seen_at,
                ),
                else_=EntityRelationship.last_seen_at,
            ),
            last_event_id=case(
                (
                    seen_at >= EntityRelationship.last_seen_at,
                    event_id,
                ),
                else_=EntityRelationship.last_event_id,
            ),
        )
    )

    result = db.execute(statement)

    if result.rowcount != 1:
        raise RuntimeError(
            "Existing entity relationship could not be atomically updated."
        )

    # The relationship may already exist in this Session's identity map.
    # Expire it so subsequent reads observe values written by the atomic UPDATE.
    db.expire_all()

def persist_event_relationships(
    *,
    db: Session,
    event: SemanticDriftEvent,
    source_entity_id: uuid.UUID | str,
    target_entity_ids: Iterable[uuid.UUID | str] | None = None,
    relationship_type: str = RELATIONSHIP_TYPE_MENTIONED,
    evidence_method: str = EVIDENCE_METHOD_DETERMINISTIC_V1,
) -> GraphPersistenceResult:
    """
    Persist directed graph evidence from one semantic drift event.

    Guarantees:
    - organization isolation;
    - no self edges;
    - invalid/stale/cross-tenant targets are ignored;
    - duplicate targets inside one event are deduplicated;
    - same-event retries are idempotent;
    - concurrent edge creation is conflict safe;
    - concurrent observation creation is conflict safe;
    - evidence_count changes only for newly inserted observations;
    - first/last graph chronology follows event.detected_at;
    - transaction ownership remains with the caller.

    This function flushes SQL through the supplied session but never commits.
    """
    if event.id is None:
        raise ValueError(
            "Semantic drift event must have an id before graph persistence."
        )

    organization_id = _as_uuid(event.organization_id)
    event_id = _as_uuid(event.id)
    source_id = _as_uuid(source_entity_id)

    source_entity = (
        db.query(Entity)
        .filter(
            Entity.id == source_id,
            Entity.organization_id == organization_id,
        )
        .one_or_none()
    )

    if source_entity is None:
        raise ValueError(
            "Source entity does not belong to the event organization."
        )

    raw_target_ids = (
        target_entity_ids
        if target_entity_ids is not None
        else event.entities_affected
    )

    normalized_target_ids: set[uuid.UUID] = set()
    skipped = 0

    for raw_target_id in raw_target_ids or []:
        try:
            target_id = _as_uuid(raw_target_id)
        except (TypeError, ValueError, AttributeError):
            skipped += 1
            continue

        if target_id == source_id:
            skipped += 1
            continue

        normalized_target_ids.add(target_id)

    if not normalized_target_ids:
        return GraphPersistenceResult(
            created=0,
            updated=0,
            skipped=skipped,
            relationship_ids=(),
            observations_created=0,
            observations_existing=0,
        )

    valid_target_ids = {
        row[0]
        for row in (
            db.query(Entity.id)
            .filter(
                Entity.organization_id == organization_id,
                Entity.id.in_(normalized_target_ids),
            )
            .all()
        )
    }

    skipped += (
        len(normalized_target_ids)
        - len(valid_target_ids)
    )

    if not valid_target_ids:
        return GraphPersistenceResult(
            created=0,
            updated=0,
            skipped=skipped,
            relationship_ids=(),
            observations_created=0,
            observations_existing=0,
        )

    seen_at = _event_seen_at(event)

    created = 0
    updated = 0
    observations_created = 0
    observations_existing = 0
    relationship_ids: list[str] = []

    for target_id in sorted(valid_target_ids, key=str):
        relationship, relationship_created = _ensure_relationship(
            db=db,
            organization_id=organization_id,
            source_entity_id=source_id,
            target_entity_id=target_id,
            event_id=event_id,
            relationship_type=relationship_type,
            evidence_method=evidence_method,
            seen_at=seen_at,
        )

        observation_created = _create_observation_if_missing(
            db=db,
            organization_id=organization_id,
            relationship_id=relationship.id,
            event_id=event_id,
            source_entity_id=source_id,
            target_entity_id=target_id,
            relationship_type=relationship_type,
            evidence_method=evidence_method,
            seen_at=seen_at,
        )

        if not observation_created:
            observations_existing += 1
            relationship_ids.append(str(relationship.id))
            continue

        observations_created += 1

        if relationship_created:
            # A newly created edge already represents this observation:
            # evidence_count starts at 1 and first/last chronology was populated
            # by _ensure_relationship().
            created += 1

        else:
            _update_existing_relationship_from_observation(
                db=db,
                relationship_id=relationship.id,
                event_id=event_id,
                evidence_method=evidence_method,
                seen_at=seen_at,
            )
            updated += 1

        relationship_ids.append(str(relationship.id))

    return GraphPersistenceResult(
        created=created,
        updated=updated,
        skipped=skipped,
        relationship_ids=tuple(relationship_ids),
        observations_created=observations_created,
        observations_existing=observations_existing,
    )