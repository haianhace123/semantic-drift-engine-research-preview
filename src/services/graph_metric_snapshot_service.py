from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.models.entity_graph_metric_snapshot import (
    EntityGraphMetricSnapshot,
)
from src.models.semantic_drift_event import (
    SemanticDriftEvent,
)
from src.services.graph_metrics_service import (
    OrganizationGraphMetrics,
    calculate_organization_graph_metrics_as_of,
)
from datetime import datetime


@dataclass(frozen=True)
class GraphMetricSnapshotPersistenceResult:
    organization_id: uuid.UUID
    event_id: uuid.UUID
    observed_at: datetime
    entity_count: int
    active_edge_count: int
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
]:
    if event.id is None:
        raise ValueError(
            "Semantic drift event must have an id "
            "before graph metric snapshot persistence."
        )

    if event.organization_id is None:
        raise ValueError(
            "Semantic drift event must belong to "
            "an organization."
        )

    if event.detected_at is None:
        raise ValueError(
            "Semantic drift event must have detected_at "
            "before graph metric snapshot persistence."
        )

    return (
        _as_uuid(event.organization_id),
        _as_uuid(event.id),
    )


def _persist_entity_snapshot(
    *,
    db: Session,
    organization_id: uuid.UUID,
    event_id: uuid.UUID,
    observed_at: datetime,
    graph_metrics: OrganizationGraphMetrics,
    entity_metrics,
) -> tuple[uuid.UUID, bool]:
    """
    Atomically persist one entity/event metric snapshot.

    PostgreSQL partial unique index
    uq_entity_graph_metric_snapshot_event is the idempotency gate.

    ON CONFLICT cannot target a partial unique index by constraint name,
    because a partial unique index is not a PostgreSQL UNIQUE constraint.
    Therefore we specify the indexed columns and matching predicate.
    """
    snapshot_id = uuid.uuid4()

    statement = (
        insert(EntityGraphMetricSnapshot)
        .values(
            id=snapshot_id,
            organization_id=organization_id,
            entity_id=entity_metrics.entity_id,
            semantic_drift_event_id=event_id,
            observed_at=observed_at,
            entity_count=graph_metrics.entity_count,
            active_edge_count=(
                graph_metrics.active_edge_count
            ),
            in_degree=entity_metrics.in_degree,
            out_degree=entity_metrics.out_degree,
            degree=entity_metrics.degree,
            in_degree_centrality=(
                entity_metrics.in_degree_centrality
            ),
            out_degree_centrality=(
                entity_metrics.out_degree_centrality
            ),
            degree_centrality=(
                entity_metrics.degree_centrality
            ),
            downstream_reach=(
                entity_metrics.downstream_reach
            ),
            downstream_reach_ratio=(
                entity_metrics.downstream_reach_ratio
            ),
        )
        .on_conflict_do_nothing(
            index_elements=[
                EntityGraphMetricSnapshot.organization_id,
                EntityGraphMetricSnapshot.entity_id,
                EntityGraphMetricSnapshot.semantic_drift_event_id,
            ],
            index_where=(
                EntityGraphMetricSnapshot.semantic_drift_event_id
                .is_not(None)
            ),
        )
        .returning(
            EntityGraphMetricSnapshot.id
        )
    )

    created_id = (
        db.execute(statement)
        .scalar_one_or_none()
    )

    if created_id is not None:
        return created_id, True

    existing_id = (
        db.query(
            EntityGraphMetricSnapshot.id
        )
        .filter(
            EntityGraphMetricSnapshot.organization_id
            == organization_id,
            EntityGraphMetricSnapshot.entity_id
            == entity_metrics.entity_id,
            EntityGraphMetricSnapshot.semantic_drift_event_id
            == event_id,
        )
        .scalar()
    )

    if existing_id is None:
        raise RuntimeError(
            "Graph metric snapshot conflict occurred "
            "but the existing snapshot could not be loaded."
        )

    return existing_id, False


def persist_graph_metric_snapshots_for_event(
    *,
    db: Session,
    event: SemanticDriftEvent,
) -> GraphMetricSnapshotPersistenceResult:
    """
    Persist one organization-wide graph metric snapshot set for an event.

    Guarantees:
    - organization isolation comes from graph metric calculation;
    - event.detected_at is the temporal observation timestamp;
    - one entity/event snapshot is idempotent;
    - concurrent same-event retries are conflict safe;
    - the caller owns the transaction;
    - no commit is performed here.

    The entire organization evidence graph as of event.detected_at is
    snapshotted, not only entities directly mentioned by the event.

    Historical edges are reconstructed from immutable relationship
    observations at or before the event timestamp. This prevents current or
    future graph state from leaking into backfilled temporal snapshots.
    """
    (
        organization_id,
        event_id,
    ) = _validate_event(event)

    graph_metrics = (
        calculate_organization_graph_metrics_as_of(
            db=db,
            organization_id=organization_id,
            observed_at=event.detected_at,
        )
    )

    if (
        graph_metrics.organization_id
        != organization_id
    ):
        raise RuntimeError(
            "Graph metrics organization does not match "
            "the semantic drift event organization."
        )

    created = 0
    existing = 0
    snapshot_ids: list[str] = []

    for entity_metrics in graph_metrics.entities:
        snapshot_id, was_created = (
            _persist_entity_snapshot(
                db=db,
                organization_id=organization_id,
                event_id=event_id,
                observed_at=event.detected_at,
                graph_metrics=graph_metrics,
                entity_metrics=entity_metrics,
            )
        )

        snapshot_ids.append(
            str(snapshot_id)
        )

        if was_created:
            created += 1
        else:
            existing += 1

    return GraphMetricSnapshotPersistenceResult(
        organization_id=organization_id,
        event_id=event_id,
        observed_at=event.detected_at,
        entity_count=graph_metrics.entity_count,
        active_edge_count=(
            graph_metrics.active_edge_count
        ),
        created=created,
        existing=existing,
        snapshot_ids=tuple(snapshot_ids),
    )