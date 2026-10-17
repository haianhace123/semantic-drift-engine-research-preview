from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from src.models.entity_graph_metric_snapshot import (
    EntityGraphMetricSnapshot,
)
from src.models.semantic_drift_event import (
    SemanticDriftEvent,
)
from src.services.graph_metrics_service import (
    EntityGraphMetrics,
    OrganizationGraphMetrics,
)
from src.services.topology_shift_service import (
    OrganizationTopologyShift,
    compare_graph_metrics,
)


@dataclass(frozen=True)
class TemporalTopologyComparison:
    organization_id: uuid.UUID
    current_event_id: uuid.UUID
    current_observed_at: datetime

    previous_event_id: uuid.UUID | None
    previous_observed_at: datetime | None

    has_previous_state: bool
    shift: OrganizationTopologyShift


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
            "before temporal topology comparison."
        )

    if event.organization_id is None:
        raise ValueError(
            "Semantic drift event must belong to "
            "an organization."
        )

    if event.detected_at is None:
        raise ValueError(
            "Semantic drift event must have detected_at "
            "before temporal topology comparison."
        )

    return (
        _as_uuid(event.organization_id),
        _as_uuid(event.id),
        event.detected_at,
    )


def _snapshot_rows_to_graph_metrics(
    *,
    organization_id: uuid.UUID,
    rows: list[EntityGraphMetricSnapshot],
) -> OrganizationGraphMetrics:
    """
    Reconstruct one immutable organization graph metric state.

    Every row in one snapshot set must describe the same organization-wide
    graph state. entity_count and active_edge_count are therefore repeated
    intentionally on each entity snapshot.

    Inconsistent persisted rows are rejected instead of silently producing
    a mixed temporal state.
    """
    if not rows:
        return OrganizationGraphMetrics(
            organization_id=organization_id,
            entity_count=0,
            active_edge_count=0,
            entities=(),
        )

    expected_entity_count = rows[0].entity_count
    expected_active_edge_count = (
        rows[0].active_edge_count
    )

    seen_entity_ids: set[uuid.UUID] = set()
    entities: list[EntityGraphMetrics] = []

    for row in rows:
        row_organization_id = _as_uuid(
            row.organization_id
        )

        if row_organization_id != organization_id:
            raise RuntimeError(
                "Graph metric snapshot organization "
                "does not match the requested organization."
            )

        if (
            row.entity_count
            != expected_entity_count
        ):
            raise RuntimeError(
                "Graph metric snapshot set contains "
                "inconsistent entity_count values."
            )

        if (
            row.active_edge_count
            != expected_active_edge_count
        ):
            raise RuntimeError(
                "Graph metric snapshot set contains "
                "inconsistent active_edge_count values."
            )

        entity_id = _as_uuid(row.entity_id)

        if entity_id in seen_entity_ids:
            raise RuntimeError(
                "Graph metric snapshot set contains "
                "duplicate entity rows."
            )

        seen_entity_ids.add(entity_id)

        entities.append(
            EntityGraphMetrics(
                entity_id=entity_id,
                in_degree=row.in_degree,
                out_degree=row.out_degree,
                degree=row.degree,
                in_degree_centrality=(
                    row.in_degree_centrality
                ),
                out_degree_centrality=(
                    row.out_degree_centrality
                ),
                degree_centrality=(
                    row.degree_centrality
                ),
                downstream_reach=(
                    row.downstream_reach
                ),
                downstream_reach_ratio=(
                    row.downstream_reach_ratio
                ),
            )
        )

    if len(entities) != expected_entity_count:
        raise RuntimeError(
            "Graph metric snapshot set is incomplete: "
            "persisted entity row count does not match "
            "entity_count."
        )

    entities.sort(
        key=lambda item: str(item.entity_id)
    )

    return OrganizationGraphMetrics(
        organization_id=organization_id,
        entity_count=expected_entity_count,
        active_edge_count=(
            expected_active_edge_count
        ),
        entities=tuple(entities),
    )


def _load_event_graph_metrics(
    *,
    db: Session,
    organization_id: uuid.UUID,
    event_id: uuid.UUID,
) -> OrganizationGraphMetrics:
    rows = (
        db.query(EntityGraphMetricSnapshot)
        .filter(
            EntityGraphMetricSnapshot.organization_id
            == organization_id,
            EntityGraphMetricSnapshot.semantic_drift_event_id
            == event_id,
        )
        .order_by(
            EntityGraphMetricSnapshot.entity_id.asc()
        )
        .all()
    )

    if not rows:
        raise ValueError(
            "No graph metric snapshots exist for "
            f"semantic drift event {event_id}."
        )

    return _snapshot_rows_to_graph_metrics(
        organization_id=organization_id,
        rows=rows,
    )


def _find_previous_snapshot_event(
    *,
    db: Session,
    organization_id: uuid.UUID,
    current_event_id: uuid.UUID,
    current_observed_at: datetime,
) -> tuple[
    uuid.UUID,
    datetime,
] | None:
    """
    Find the nearest strictly-earlier persisted graph metric state.

    Snapshot rows are grouped into event-level temporal states before
    ordering. One event may have many entity snapshot rows, but it still
    represents exactly one graph state.

    Strict '<' is intentional.

    Events sharing the exact same semantic timestamp have no trustworthy
    temporal ordering. UUID order and snapshot created_at represent storage
    order, not semantic chronology, so they must not be used to invent one.

    The current event is excluded explicitly for defensive safety.
    """
    row = (
        db.query(
            EntityGraphMetricSnapshot.semantic_drift_event_id,
            EntityGraphMetricSnapshot.observed_at,
        )
        .filter(
            EntityGraphMetricSnapshot.organization_id
            == organization_id,
            EntityGraphMetricSnapshot.semantic_drift_event_id
            .is_not(None),
            EntityGraphMetricSnapshot.semantic_drift_event_id
            != current_event_id,
            EntityGraphMetricSnapshot.observed_at
            < current_observed_at,
        )
        .group_by(
            EntityGraphMetricSnapshot.semantic_drift_event_id,
            EntityGraphMetricSnapshot.observed_at,
        )
        .order_by(
            EntityGraphMetricSnapshot.observed_at.desc(),
            EntityGraphMetricSnapshot.semantic_drift_event_id.asc(),
        )
        .first()
    )

    if row is None:
        return None

    previous_event_id = row[0]
    previous_observed_at = row[1]

    if previous_event_id is None:
        return None

    return (
        _as_uuid(previous_event_id),
        previous_observed_at,
    )


def compare_event_topology(
    *,
    db: Session,
    event: SemanticDriftEvent,
) -> TemporalTopologyComparison:
    """
    Compare one event's persisted graph metrics with the nearest
    strictly-earlier persisted temporal graph state.

    Important properties:
    - operates on immutable graph metric snapshots;
    - never recalculates historical graph state from current aggregates;
    - tenant scoped;
    - previous state is selected by semantic observed_at chronology;
    - created_at / worker execution order is ignored;
    - same-timestamp events do not create artificial chronology;
    - first known temporal state compares against an empty baseline.

    The function is read-only and never commits.
    """
    (
        organization_id,
        current_event_id,
        current_observed_at,
    ) = _validate_event(event)

    current_metrics = _load_event_graph_metrics(
        db=db,
        organization_id=organization_id,
        event_id=current_event_id,
    )

    previous_reference = (
        _find_previous_snapshot_event(
            db=db,
            organization_id=organization_id,
            current_event_id=current_event_id,
            current_observed_at=current_observed_at,
        )
    )

    if previous_reference is None:
        previous_metrics = (
            OrganizationGraphMetrics(
                organization_id=organization_id,
                entity_count=0,
                active_edge_count=0,
                entities=(),
            )
        )

        previous_event_id = None
        previous_observed_at = None
        has_previous_state = False

    else:
        (
            previous_event_id,
            previous_observed_at,
        ) = previous_reference

        previous_metrics = (
            _load_event_graph_metrics(
                db=db,
                organization_id=organization_id,
                event_id=previous_event_id,
            )
        )

        has_previous_state = True

    shift = compare_graph_metrics(
        previous=previous_metrics,
        current=current_metrics,
    )

    return TemporalTopologyComparison(
        organization_id=organization_id,
        current_event_id=current_event_id,
        current_observed_at=current_observed_at,
        previous_event_id=previous_event_id,
        previous_observed_at=previous_observed_at,
        has_previous_state=has_previous_state,
        shift=shift,
    )