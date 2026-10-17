from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.models.entity_graph_metric_snapshot import (
    EntityGraphMetricSnapshot,
)
from src.models.risk_assessment import RiskAssessment
from src.models.semantic_drift_event import (
    SemanticDriftEvent,
)
from src.models.topology_shift_snapshot import (
    TopologyShiftSnapshot,
)
from src.services.event_evidence_lock import (
    acquire_event_evidence_lock,
)
from src.services.temporal_topology_service import (
    TemporalTopologyComparison,
    compare_event_topology,
)


@dataclass(frozen=True)
class TopologyShiftSnapshotPersistenceResult:
    organization_id: uuid.UUID
    event_id: uuid.UUID
    observed_at: datetime

    previous_event_id: uuid.UUID | None
    previous_observed_at: datetime | None

    entity_count_delta: int
    active_edge_count_delta: int

    created: int
    existing: int

    snapshot_ids: tuple[str, ...]


def _as_uuid(
    value: uuid.UUID | str,
) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value

    return uuid.UUID(str(value))


def _validate_event(
    event: SemanticDriftEvent,
) -> tuple[
    uuid.UUID,
    uuid.UUID,
    datetime,
]:
    if event.id is None:
        raise ValueError(
            "Semantic drift event must have an id "
            "before topology shift persistence."
        )

    if event.organization_id is None:
        raise ValueError(
            "Semantic drift event must belong to "
            "an organization."
        )

    if event.detected_at is None:
        raise ValueError(
            "Semantic drift event must have detected_at "
            "before topology shift persistence."
        )

    return (
        _as_uuid(event.organization_id),
        _as_uuid(event.id),
        event.detected_at,
    )


def _persist_entity_shift(
    *,
    db: Session,
    organization_id: uuid.UUID,
    event_id: uuid.UUID,
    comparison: TemporalTopologyComparison,
    entity_shift,
) -> tuple[
    uuid.UUID,
    bool,
]:
    """
    Atomically persist one entity/event topology shift.

    PostgreSQL partial unique index
    uq_topology_shift_snapshot_event_entity is the idempotency gate.

    The caller owns the surrounding transaction.
    """
    snapshot_id = uuid.uuid4()

    statement = (
        insert(TopologyShiftSnapshot)
        .values(
            id=snapshot_id,
            organization_id=organization_id,
            entity_id=entity_shift.entity_id,
            semantic_drift_event_id=event_id,
            previous_event_id=(
                comparison.previous_event_id
            ),
            observed_at=(
                comparison.current_observed_at
            ),
            previous_observed_at=(
                comparison.previous_observed_at
            ),
            entity_count_delta=(
                comparison.shift.entity_count_delta
            ),
            active_edge_count_delta=(
                comparison.shift
                .active_edge_count_delta
            ),
            in_degree_delta=(
                entity_shift.in_degree_delta
            ),
            out_degree_delta=(
                entity_shift.out_degree_delta
            ),
            degree_delta=(
                entity_shift.degree_delta
            ),
            degree_centrality_delta=(
                entity_shift.degree_centrality_delta
            ),
            downstream_reach_delta=(
                entity_shift.downstream_reach_delta
            ),
            downstream_reach_ratio_delta=(
                entity_shift
                .downstream_reach_ratio_delta
            ),
            classification=(
                entity_shift.classification
            ),
            severity=entity_shift.severity,
            shift_score=entity_shift.shift_score,
        )
        .on_conflict_do_nothing(
            index_elements=[
                TopologyShiftSnapshot.organization_id,
                TopologyShiftSnapshot.entity_id,
                TopologyShiftSnapshot.semantic_drift_event_id,
            ],
            index_where=(
                TopologyShiftSnapshot
                .semantic_drift_event_id
                .is_not(None)
            ),
        )
        .returning(
            TopologyShiftSnapshot.id
        )
    )

    created_id = (
        db.execute(statement)
        .scalar_one_or_none()
    )

    if created_id is not None:
        return (
            _as_uuid(created_id),
            True,
        )

    existing_id = (
        db.query(
            TopologyShiftSnapshot.id
        )
        .filter(
            TopologyShiftSnapshot.organization_id
            == organization_id,
            TopologyShiftSnapshot.entity_id
            == entity_shift.entity_id,
            TopologyShiftSnapshot.semantic_drift_event_id
            == event_id,
        )
        .scalar()
    )

    if existing_id is None:
        raise RuntimeError(
            "Topology shift snapshot conflict occurred "
            "but the existing snapshot could not be loaded."
        )

    return (
        _as_uuid(existing_id),
        False,
    )


def _find_direct_successor_events(
    *,
    db: Session,
    organization_id: uuid.UUID,
    observed_at: datetime,
) -> tuple[SemanticDriftEvent, ...]:
    successor_time = (
        db.query(EntityGraphMetricSnapshot.observed_at)
        .filter(
            EntityGraphMetricSnapshot.organization_id == organization_id,
            EntityGraphMetricSnapshot.observed_at > observed_at,
            EntityGraphMetricSnapshot.semantic_drift_event_id.is_not(None),
        )
        .order_by(EntityGraphMetricSnapshot.observed_at.asc())
        .limit(1)
        .scalar()
    )
    if successor_time is None:
        return ()

    successor_ids = (
        db.query(EntityGraphMetricSnapshot.semantic_drift_event_id)
        .filter(
            EntityGraphMetricSnapshot.organization_id == organization_id,
            EntityGraphMetricSnapshot.observed_at == successor_time,
            EntityGraphMetricSnapshot.semantic_drift_event_id.is_not(None),
        )
        .distinct()
        .order_by(EntityGraphMetricSnapshot.semantic_drift_event_id.asc())
        .all()
    )
    event_ids = tuple(_as_uuid(row.semantic_drift_event_id) for row in successor_ids)
    if not event_ids:
        return ()

    events = (
        db.query(SemanticDriftEvent)
        .filter(
            SemanticDriftEvent.organization_id == organization_id,
            SemanticDriftEvent.id.in_(event_ids),
        )
        .order_by(SemanticDriftEvent.id.asc())
        .all()
    )
    if len(events) != len(event_ids):
        raise RuntimeError(
            "Direct successor graph state references a semantic "
            "drift event that could not be loaded."
        )
    return tuple(events)


def _load_successor_shift_rows_for_update(
    *,
    db: Session,
    organization_id: uuid.UUID,
    successor_event_id: uuid.UUID,
) -> list[TopologyShiftSnapshot]:
    return (
        db.query(TopologyShiftSnapshot)
        .filter(
            TopologyShiftSnapshot.organization_id == organization_id,
            TopologyShiftSnapshot.semantic_drift_event_id
            == successor_event_id,
        )
        .order_by(TopologyShiftSnapshot.entity_id.asc())
        .with_for_update()
        .all()
    )


def _successor_transition_is_current(
    *,
    rows: list[TopologyShiftSnapshot],
    predecessor_event_id: uuid.UUID,
    predecessor_observed_at: datetime,
) -> bool:
    return all(
        row.previous_event_id == predecessor_event_id
        and row.previous_observed_at == predecessor_observed_at
        for row in rows
    )


def _assert_successor_repair_allowed(
    *,
    db: Session,
    organization_id: uuid.UUID,
    successor_event_id: uuid.UUID,
) -> None:
    assessment_exists = (
        db.query(RiskAssessment.id)
        .filter(
            RiskAssessment.organization_id == organization_id,
            RiskAssessment.semantic_drift_event_id
            == successor_event_id,
        )
        .first()
        is not None
    )

    if assessment_exists:
        raise RuntimeError(
            "Cannot repair stale topology for a successor event "
            "with persisted risk assessment evidence."
        )


def _repair_direct_successors_if_stale(
    *,
    db: Session,
    organization_id: uuid.UUID,
    predecessor_event_id: uuid.UUID,
    predecessor_observed_at: datetime,
) -> None:
    successors = _find_direct_successor_events(
        db=db,
        organization_id=organization_id,
        observed_at=predecessor_observed_at,
    )

    for successor in successors:
        successor_event_id = _as_uuid(successor.id)
        acquire_event_evidence_lock(
            db,
            organization_id=organization_id,
            event_id=successor_event_id,
        )

        rows = _load_successor_shift_rows_for_update(
            db=db,
            organization_id=organization_id,
            successor_event_id=successor_event_id,
        )
        if not rows:
            continue
        if _successor_transition_is_current(
            rows=rows,
            predecessor_event_id=predecessor_event_id,
            predecessor_observed_at=predecessor_observed_at,
        ):
            continue

        _assert_successor_repair_allowed(
            db=db,
            organization_id=organization_id,
            successor_event_id=successor_event_id,
        )
        comparison = compare_event_topology(db=db, event=successor)

        if comparison.organization_id != organization_id:
            raise RuntimeError(
                "Successor temporal topology organization does not "
                "match the repair organization."
            )
        if comparison.current_event_id != successor_event_id:
            raise RuntimeError(
                "Successor temporal topology current event does not "
                "match the successor event."
            )
        if comparison.previous_event_id != predecessor_event_id:
            raise RuntimeError(
                "Direct successor topology no longer resolves to the "
                "backfilled event as its predecessor."
            )
        if comparison.previous_observed_at != predecessor_observed_at:
            raise RuntimeError(
                "Direct successor topology predecessor time changed "
                "during repair."
            )

        db.execute(
            delete(TopologyShiftSnapshot).where(
                TopologyShiftSnapshot.organization_id == organization_id,
                TopologyShiftSnapshot.semantic_drift_event_id == successor_event_id,
            )
        )
        for entity_shift in comparison.shift.entities:
            _snapshot_id, was_created = _persist_entity_shift(
                db=db,
                organization_id=organization_id,
                event_id=successor_event_id,
                comparison=comparison,
                entity_shift=entity_shift,
            )
            if not was_created:
                raise RuntimeError(
                    "Successor topology repair encountered an "
                    "unexpected persistence conflict."
                )


def persist_topology_shift_snapshots_for_event(
    *,
    db: Session,
    event: SemanticDriftEvent,
) -> TopologyShiftSnapshotPersistenceResult:
    """
    Persist deterministic per-entity topology deltas for one event.

    Source of truth:
        persisted EntityGraphMetricSnapshot states
            ->
        compare_event_topology()
            ->
        immutable TopologyShiftSnapshot rows

    Guarantees:
    - tenant scoped;
    - historical comparison uses semantic chronology;
    - same-timestamp events do not create fake chronology;
    - first temporal state compares with the explicit empty baseline;
    - one entity/event topology row is idempotent;
    - concurrent same-event retries use PostgreSQL conflict handling;
    - caller owns the transaction;
    - no commit is performed here.
    """
    (
        organization_id,
        event_id,
        observed_at,
    ) = _validate_event(event)

    comparison = compare_event_topology(
        db=db,
        event=event,
    )

    if (
        comparison.organization_id
        != organization_id
    ):
        raise RuntimeError(
            "Temporal topology organization does not "
            "match the semantic drift event organization."
        )

    if comparison.current_event_id != event_id:
        raise RuntimeError(
            "Temporal topology current event does not "
            "match the semantic drift event."
        )

    if comparison.current_observed_at != observed_at:
        raise RuntimeError(
            "Temporal topology observation time does not "
            "match the semantic drift event detected_at."
        )

    created = 0
    existing = 0
    snapshot_ids: list[str] = []

    for entity_shift in comparison.shift.entities:
        snapshot_id, was_created = (
            _persist_entity_shift(
                db=db,
                organization_id=organization_id,
                event_id=event_id,
                comparison=comparison,
                entity_shift=entity_shift,
            )
        )

        snapshot_ids.append(
            str(snapshot_id)
        )

        if was_created:
            created += 1
        else:
            existing += 1

    _repair_direct_successors_if_stale(
        db=db,
        organization_id=organization_id,
        predecessor_event_id=event_id,
        predecessor_observed_at=observed_at,
    )

    return TopologyShiftSnapshotPersistenceResult(
        organization_id=organization_id,
        event_id=event_id,
        observed_at=observed_at,
        previous_event_id=(
            comparison.previous_event_id
        ),
        previous_observed_at=(
            comparison.previous_observed_at
        ),
        entity_count_delta=(
            comparison.shift.entity_count_delta
        ),
        active_edge_count_delta=(
            comparison.shift
            .active_edge_count_delta
        ),
        created=created,
        existing=existing,
        snapshot_ids=tuple(snapshot_ids),
    )