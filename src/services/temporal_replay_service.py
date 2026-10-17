"""
Deterministic temporal replay read model.

Temporal Replay reconstructs what Semantic Drift Engine knew at a requested
knowledge-time cursor without mutating historical state.

Two time dimensions are intentionally kept separate:

effective time
    When the source fact applies to the monitored world:
    crawled_at / detected_at / observed_at.

knowledge time
    When SDE had persisted the derived fact:
    created_at / assessed_at.

This distinction prevents backfilled graph, topology, or risk evidence from
leaking into an earlier "what did the system know then?" replay.
"""

from __future__ import annotations

import uuid

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.models import (
    Entity,
    EntityGraphMetricSnapshot,
    MonitoredPage,
    PageSnapshot,
    RiskAssessment,
    SemanticDriftEvent,
    TopologyShiftSnapshot,
)


from src.services.risk_version import RISK_VERSION_V2


class TemporalReplayValidationError(ValueError):
    """Raised when a replay request is invalid."""


@dataclass(frozen=True)
class ReplaySnapshot:
    snapshot_id: uuid.UUID
    monitored_page_id: uuid.UUID
    entity_id: uuid.UUID
    content_hash: str
    crawled_at: datetime
    created_at: datetime | None


@dataclass(frozen=True)
class ReplayEvent:
    event_id: uuid.UUID
    monitored_page_id: uuid.UUID
    entity_id: uuid.UUID
    old_snapshot_id: uuid.UUID
    new_snapshot_id: uuid.UUID
    detected_at: datetime
    risk_level: str
    semantic_magnitude: float | None


@dataclass(frozen=True)
class ReplayGraphEntity:
    entity_id: uuid.UUID
    in_degree: int
    out_degree: int
    degree: int
    degree_centrality: float
    downstream_reach: int
    downstream_reach_ratio: float


@dataclass(frozen=True)
class ReplayGraphState:
    observed_at: datetime
    created_at: datetime
    semantic_drift_event_id: uuid.UUID | None
    entity_count: int
    active_edge_count: int
    entities: tuple[ReplayGraphEntity, ...]


@dataclass(frozen=True)
class ReplayTopologyEntity:
    entity_id: uuid.UUID
    classification: str
    severity: str
    shift_score: float
    degree_delta: int
    degree_centrality_delta: float
    downstream_reach_delta: int
    downstream_reach_ratio_delta: float


@dataclass(frozen=True)
class ReplayTopologyState:
    observed_at: datetime
    created_at: datetime
    semantic_drift_event_id: uuid.UUID | None
    previous_observed_at: datetime | None
    entities: tuple[ReplayTopologyEntity, ...]


@dataclass(frozen=True)
class ReplayRiskAssessment:
    assessment_id: uuid.UUID
    semantic_drift_event_id: uuid.UUID | None
    risk_version: str
    assessed_at: datetime
    created_at: datetime
    score: float
    topology_score: float
    topology_contribution: float
    has_topology_signal: bool
    has_previous_topology_state: bool


@dataclass(frozen=True)
class TemporalReplayState:
    organization_id: uuid.UUID
    cursor: datetime

    snapshots: tuple[ReplaySnapshot, ...]
    events: tuple[ReplayEvent, ...]

    graph: ReplayGraphState | None
    topology: ReplayTopologyState | None

    risk_assessments: tuple[
        ReplayRiskAssessment,
        ...
    ]


def _as_uuid(
    value,
    *,
    field_name: str,
) -> uuid.UUID:
    try:
        return (
            value
            if isinstance(value, uuid.UUID)
            else uuid.UUID(str(value))
        )
    except (
        TypeError,
        ValueError,
        AttributeError,
    ) as exc:
        raise TemporalReplayValidationError(
            f"{field_name} must be a valid UUID."
        ) from exc


def _normalize_cursor(
    value: datetime,
) -> datetime:
    if not isinstance(value, datetime):
        raise TemporalReplayValidationError(
            "cursor must be a datetime."
        )

    if value.tzinfo is None:
        return value.replace(
            tzinfo=timezone.utc
        )

    return value.astimezone(
        timezone.utc
    )


def _load_snapshots(
    db: Session,
    *,
    organization_id: uuid.UUID,
    cursor: datetime,
) -> tuple[ReplaySnapshot, ...]:
    """
    Return the latest snapshot per monitored page known by cursor.

    Snapshot selection intentionally matches the existing temporal API:
    crawled_at DESC, id DESC per monitored page.

    created_at <= cursor additionally enforces knowledge-time replay.
    """

    rows = (
        db.query(
            PageSnapshot,
            MonitoredPage,
            Entity,
        )
        .join(
            MonitoredPage,
            MonitoredPage.id
            == PageSnapshot.monitored_page_id,
        )
        .join(
            Entity,
            Entity.id
            == MonitoredPage.entity_id,
        )
        .filter(
            PageSnapshot.organization_id
            == organization_id,
            MonitoredPage.organization_id
            == organization_id,
            Entity.organization_id
            == organization_id,
            PageSnapshot.crawled_at
            <= cursor,
            PageSnapshot.created_at
            <= cursor,
        )
        .order_by(
            MonitoredPage.id.asc(),
            PageSnapshot.crawled_at.desc(),
            PageSnapshot.id.desc(),
        )
        .all()
    )

    latest_by_page: dict[
        uuid.UUID,
        ReplaySnapshot,
    ] = {}

    for snapshot, page, entity in rows:
        if page.id in latest_by_page:
            continue

        latest_by_page[page.id] = ReplaySnapshot(
            snapshot_id=snapshot.id,
            monitored_page_id=page.id,
            entity_id=entity.id,
            content_hash=snapshot.content_hash,
            crawled_at=snapshot.crawled_at,
            created_at=snapshot.created_at,
        )

    return tuple(
        latest_by_page[page_id]
        for page_id in sorted(
            latest_by_page,
            key=str,
        )
    )


def _load_events(
    db: Session,
    *,
    organization_id: uuid.UUID,
    cursor: datetime,
) -> tuple[ReplayEvent, ...]:
    """
    Return semantic events effective at or before cursor.

    SemanticDriftEvent has no dedicated created_at column, so detected_at is
    currently the strongest persisted chronology available for the event
    itself. Derived graph/topology/risk state is separately knowledge-gated.
    """

    rows = (
        db.query(
            SemanticDriftEvent,
            MonitoredPage,
            Entity,
        )
        .join(
            MonitoredPage,
            MonitoredPage.id
            == SemanticDriftEvent.monitored_page_id,
        )
        .join(
            Entity,
            Entity.id
            == MonitoredPage.entity_id,
        )
        .filter(
            SemanticDriftEvent.organization_id
            == organization_id,
            MonitoredPage.organization_id
            == organization_id,
            Entity.organization_id
            == organization_id,
            SemanticDriftEvent.detected_at
            <= cursor,
        )
        .order_by(
            SemanticDriftEvent.detected_at.asc(),
            SemanticDriftEvent.id.asc(),
        )
        .all()
    )

    return tuple(
        ReplayEvent(
            event_id=event.id,
            monitored_page_id=page.id,
            entity_id=entity.id,
            old_snapshot_id=event.old_snapshot_id,
            new_snapshot_id=event.new_snapshot_id,
            detected_at=event.detected_at,
            risk_level=str(
                event.risk_level
                or "unknown"
            ),
            semantic_magnitude=(
                float(event.semantic_magnitude)
                if event.semantic_magnitude
                is not None
                else None
            ),
        )
        for event, page, entity in rows
    )


def _load_graph_state(
    db: Session,
    *,
    organization_id: uuid.UUID,
    cursor: datetime,
) -> ReplayGraphState | None:
    """
    Return the latest complete persisted graph-metric state known by cursor.

    observed_at is effective chronology.
    created_at is knowledge chronology.

    Event ID is the preferred state identity. When an event has later been
    deleted and PostgreSQL has cleared the event FK, observed_at becomes the
    surviving temporal identity.
    """

    latest = (
        db.query(
            EntityGraphMetricSnapshot.observed_at,
            EntityGraphMetricSnapshot.semantic_drift_event_id,
        )
        .filter(
            EntityGraphMetricSnapshot.organization_id
            == organization_id,
            EntityGraphMetricSnapshot.observed_at
            <= cursor,
            EntityGraphMetricSnapshot.created_at
            <= cursor,
        )
        .order_by(
            EntityGraphMetricSnapshot.observed_at.desc(),
            EntityGraphMetricSnapshot.semantic_drift_event_id.desc(),
            EntityGraphMetricSnapshot.id.desc(),
        )
        .first()
    )

    if latest is None:
        return None

    observed_at, event_id = latest

    query = (
        db.query(
            EntityGraphMetricSnapshot
        )
        .filter(
            EntityGraphMetricSnapshot.organization_id
            == organization_id,
            EntityGraphMetricSnapshot.observed_at
            == observed_at,
            EntityGraphMetricSnapshot.created_at
            <= cursor,
        )
    )

    if event_id is None:
        query = query.filter(
            EntityGraphMetricSnapshot.semantic_drift_event_id
            .is_(None)
        )
    else:
        query = query.filter(
            EntityGraphMetricSnapshot.semantic_drift_event_id
            == event_id
        )

    rows = (
        query
        .order_by(
            EntityGraphMetricSnapshot.entity_id.asc()
        )
        .all()
    )

    if not rows:
        return None

    entity_counts = {
        row.entity_count
        for row in rows
    }

    edge_counts = {
        row.active_edge_count
        for row in rows
    }

    if (
        len(entity_counts) != 1
        or len(edge_counts) != 1
    ):
        raise TemporalReplayValidationError(
            "Persisted graph state is internally inconsistent."
        )

    expected_entity_count = next(
        iter(entity_counts)
    )

    if len(rows) != expected_entity_count:
        raise TemporalReplayValidationError(
            "Persisted graph state is incomplete."
        )

    state_created_at = max(
        row.created_at
        for row in rows
    )

    return ReplayGraphState(
        observed_at=observed_at,
        created_at=state_created_at,
        semantic_drift_event_id=event_id,
        entity_count=expected_entity_count,
        active_edge_count=next(
            iter(edge_counts)
        ),
        entities=tuple(
            ReplayGraphEntity(
                entity_id=row.entity_id,
                in_degree=row.in_degree,
                out_degree=row.out_degree,
                degree=row.degree,
                degree_centrality=float(
                    row.degree_centrality
                ),
                downstream_reach=(
                    row.downstream_reach
                ),
                downstream_reach_ratio=float(
                    row.downstream_reach_ratio
                ),
            )
            for row in rows
        ),
    )


def _load_topology_state(
    db: Session,
    *,
    organization_id: uuid.UUID,
    cursor: datetime,
) -> ReplayTopologyState | None:
    """
    Return the latest complete persisted topology-delta state known by cursor.

    Event ID is preferred as state identity. observed_at remains available
    after event deletion and therefore acts as the fallback identity.
    """

    latest = (
        db.query(
            TopologyShiftSnapshot.observed_at,
            TopologyShiftSnapshot.semantic_drift_event_id,
        )
        .filter(
            TopologyShiftSnapshot.organization_id
            == organization_id,
            TopologyShiftSnapshot.observed_at
            <= cursor,
            TopologyShiftSnapshot.created_at
            <= cursor,
        )
        .order_by(
            TopologyShiftSnapshot.observed_at.desc(),
            TopologyShiftSnapshot.semantic_drift_event_id.desc(),
            TopologyShiftSnapshot.id.desc(),
        )
        .first()
    )

    if latest is None:
        return None

    observed_at, event_id = latest

    query = (
        db.query(
            TopologyShiftSnapshot
        )
        .filter(
            TopologyShiftSnapshot.organization_id
            == organization_id,
            TopologyShiftSnapshot.observed_at
            == observed_at,
            TopologyShiftSnapshot.created_at
            <= cursor,
        )
    )

    if event_id is None:
        query = query.filter(
            TopologyShiftSnapshot.semantic_drift_event_id
            .is_(None)
        )
    else:
        query = query.filter(
            TopologyShiftSnapshot.semantic_drift_event_id
            == event_id
        )

    rows = (
        query
        .order_by(
            TopologyShiftSnapshot.entity_id.asc()
        )
        .all()
    )

    if not rows:
        return None

    previous_times = {
        row.previous_observed_at
        for row in rows
    }

    if len(previous_times) != 1:
        raise TemporalReplayValidationError(
            "Persisted topology state is internally inconsistent."
        )

    entity_ids = [
        row.entity_id
        for row in rows
    ]

    if len(entity_ids) != len(set(entity_ids)):
        raise TemporalReplayValidationError(
            "Persisted topology state contains duplicate entities."
        )

    state_created_at = max(
        row.created_at
        for row in rows
    )

    return ReplayTopologyState(
        observed_at=observed_at,
        created_at=state_created_at,
        semantic_drift_event_id=event_id,
        previous_observed_at=next(
            iter(previous_times)
        ),
        entities=tuple(
            ReplayTopologyEntity(
                entity_id=row.entity_id,
                classification=row.classification,
                severity=row.severity,
                shift_score=float(
                    row.shift_score
                ),
                degree_delta=row.degree_delta,
                degree_centrality_delta=float(
                    row.degree_centrality_delta
                ),
                downstream_reach_delta=(
                    row.downstream_reach_delta
                ),
                downstream_reach_ratio_delta=float(
                    row.downstream_reach_ratio_delta
                ),
            )
            for row in rows
        ),
    )


def _load_risk_assessments(
    db: Session,
    *,
    organization_id: uuid.UUID,
    cursor: datetime,
) -> tuple[
    ReplayRiskAssessment,
    ...
]:
    """
    Return immutable Risk v2 assessments actually known by cursor.

    assessed_at alone is not sufficient for knowledge-time replay because
    backfilled assessments may intentionally retain an earlier semantic
    assessment timestamp. created_at is therefore also gated by cursor.
    """

    rows = (
        db.query(
            RiskAssessment
        )
        .filter(
            RiskAssessment.organization_id
            == organization_id,
            RiskAssessment.risk_version
            == RISK_VERSION_V2,
            RiskAssessment.assessed_at
            <= cursor,
            RiskAssessment.created_at
            <= cursor,
        )
        .order_by(
            RiskAssessment.assessed_at.asc(),
            RiskAssessment.id.asc(),
        )
        .all()
    )

    return tuple(
        ReplayRiskAssessment(
            assessment_id=row.id,
            semantic_drift_event_id=(
                row.semantic_drift_event_id
            ),
            risk_version=row.risk_version,
            assessed_at=row.assessed_at,
            created_at=row.created_at,
            score=float(row.score),
            topology_score=float(
                row.topology_score
            ),
            topology_contribution=float(
                row.topology_contribution
            ),
            has_topology_signal=(
                row.has_topology_signal
            ),
            has_previous_topology_state=(
                row.has_previous_topology_state
            ),
        )
        for row in rows
    )


def replay_organization_at(
    db: Session,
    *,
    organization_id,
    cursor: datetime,
) -> TemporalReplayState:
    """
    Reconstruct the tenant's persisted knowledge state at cursor.

    This function is read-only. It never flushes, commits, or mutates
    persistence state.
    """

    normalized_organization_id = _as_uuid(
        organization_id,
        field_name="organization_id",
    )

    normalized_cursor = (
        _normalize_cursor(
            cursor
        )
    )

    snapshots = _load_snapshots(
        db,
        organization_id=(
            normalized_organization_id
        ),
        cursor=normalized_cursor,
    )

    events = _load_events(
        db,
        organization_id=(
            normalized_organization_id
        ),
        cursor=normalized_cursor,
    )

    graph = _load_graph_state(
        db,
        organization_id=(
            normalized_organization_id
        ),
        cursor=normalized_cursor,
    )

    topology = _load_topology_state(
        db,
        organization_id=(
            normalized_organization_id
        ),
        cursor=normalized_cursor,
    )

    risk_assessments = (
        _load_risk_assessments(
            db,
            organization_id=(
                normalized_organization_id
            ),
            cursor=normalized_cursor,
        )
    )

    return TemporalReplayState(
        organization_id=(
            normalized_organization_id
        ),
        cursor=normalized_cursor,
        snapshots=snapshots,
        events=events,
        graph=graph,
        topology=topology,
        risk_assessments=(
            risk_assessments
        ),
    )