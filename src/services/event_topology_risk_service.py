from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from src.models.semantic_drift_event import SemanticDriftEvent
from src.models.topology_shift_snapshot import (
    TopologyShiftSnapshot,
)
from src.services.risk_scoring_v2_service import (
    TopologyRiskInput,
)


@dataclass(frozen=True)
class EventTopologyRiskEvidence:
    shift_score: float
    degree_centrality_delta: float
    downstream_reach_ratio_delta: float


@dataclass(frozen=True)
class EventTopologyRiskSignal:
    organization_id: uuid.UUID
    event_id: uuid.UUID
    entity_count: int

    shift_score: float
    degree_centrality_delta: float
    downstream_reach_ratio_delta: float

    has_previous_state: bool

    risk_input: TopologyRiskInput


def _as_uuid(
    value: object,
    *,
    field_name: str,
) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value

    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(
            f"{field_name} must be a valid UUID"
        ) from exc


def _validate_event(
    event: SemanticDriftEvent,
) -> tuple[uuid.UUID, uuid.UUID]:
    event_id = getattr(event, "id", None)

    if event_id is None:
        raise ValueError(
            "event.id is required"
        )

    organization_id = getattr(
        event,
        "organization_id",
        None,
    )

    if organization_id is None:
        raise ValueError(
            "event.organization_id is required"
        )

    return (
        _as_uuid(
            event_id,
            field_name="event.id",
        ),
        _as_uuid(
            organization_id,
            field_name="event.organization_id",
        ),
    )


def reduce_event_topology_risk_signal(
    *,
    organization_id: uuid.UUID,
    event_id: uuid.UUID,
    evidence: tuple[EventTopologyRiskEvidence, ...],
    has_previous_state: bool,
) -> EventTopologyRiskSignal:
    if not evidence:
        raise ValueError(
            "topology risk evidence must not be empty"
        )

    shift_score = round(
        max(
            max(
                0.0,
                min(
                    1.0,
                    float(item.shift_score),
                ),
            )
            for item in evidence
        ),
        6,
    )

    degree_centrality_delta = round(
        min(
            1.0,
            max(
                abs(
                    float(
                        item.degree_centrality_delta
                    )
                )
                for item in evidence
            ),
        ),
        6,
    )

    downstream_reach_ratio_delta = round(
        min(
            1.0,
            max(
                abs(
                    float(
                        item.downstream_reach_ratio_delta
                    )
                )
                for item in evidence
            ),
        ),
        6,
    )

    risk_input = TopologyRiskInput(
        shift_score=shift_score,
        degree_centrality_delta=(
            degree_centrality_delta
        ),
        downstream_reach_ratio_delta=(
            downstream_reach_ratio_delta
        ),
        has_previous_state=has_previous_state,
    )

    return EventTopologyRiskSignal(
        organization_id=organization_id,
        event_id=event_id,
        entity_count=len(evidence),
        shift_score=shift_score,
        degree_centrality_delta=(
            degree_centrality_delta
        ),
        downstream_reach_ratio_delta=(
            downstream_reach_ratio_delta
        ),
        has_previous_state=has_previous_state,
        risk_input=risk_input,
    )


def load_event_topology_risk_signal(
    db: Session,
    event: SemanticDriftEvent,
) -> EventTopologyRiskSignal | None:
    """
    Load persisted topology-shift evidence for one event and reduce
    the entity-level rows into one deterministic event-level signal.

    Returns None when topology shift snapshots have not yet been
    persisted for the event.

    Aggregation intentionally uses independent maxima.
    """
    (
        event_id,
        organization_id,
    ) = _validate_event(event)

    rows = (
        db.query(TopologyShiftSnapshot)
        .filter(
            TopologyShiftSnapshot.organization_id
            == organization_id,
            TopologyShiftSnapshot.semantic_drift_event_id
            == event_id,
        )
        .order_by(
            TopologyShiftSnapshot.entity_id.asc()
        )
        .all()
    )

    if not rows:
        return None

    if any(
        row.organization_id != organization_id
        for row in rows
    ):
        raise RuntimeError(
            "topology shift snapshot tenant mismatch"
        )

    observed_at_values = {
        row.observed_at
        for row in rows
    }

    if len(observed_at_values) != 1:
        raise RuntimeError(
            "topology shift snapshot set has inconsistent "
            "observed_at values"
        )

    previous_observed_at_values = {
        row.previous_observed_at
        for row in rows
    }

    if len(previous_observed_at_values) != 1:
        raise RuntimeError(
            "topology shift snapshot set has inconsistent "
            "previous_observed_at values"
        )

    previous_observed_at = next(
        iter(previous_observed_at_values)
    )

    evidence = tuple(
        EventTopologyRiskEvidence(
            shift_score=float(row.shift_score),
            degree_centrality_delta=float(
                row.degree_centrality_delta
            ),
            downstream_reach_ratio_delta=float(
                row.downstream_reach_ratio_delta
            ),
        )
        for row in rows
    )

    return reduce_event_topology_risk_signal(
        organization_id=organization_id,
        event_id=event_id,
        evidence=evidence,
        has_previous_state=(
            previous_observed_at is not None
        ),
    )
