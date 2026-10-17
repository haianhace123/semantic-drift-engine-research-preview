from __future__ import annotations

import math
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from src.models.semantic_drift_event import SemanticDriftEvent
from src.models.topology_shift_snapshot import TopologyShiftSnapshot


@dataclass(frozen=True)
class EventTopologyRiskV3Evidence:
    entity_id: uuid.UUID
    shift_score: float
    degree_centrality_delta: float
    downstream_reach_ratio_delta: float


@dataclass(frozen=True)
class EventTopologyRiskV3Signal:
    organization_id: uuid.UUID
    event_id: uuid.UUID
    entity_count: int
    representative_entity_id: uuid.UUID

    shift_score: float
    degree_centrality_delta: float
    downstream_reach_ratio_delta: float

    has_previous_state: bool


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


def _require_unit_interval(
    value: object,
    *,
    name: str,
) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{name} must be numeric."
        ) from exc

    if not math.isfinite(result):
        raise ValueError(
            f"{name} must be finite."
        )

    if result < 0.0 or result > 1.0:
        raise ValueError(
            f"{name} must be between 0 and 1."
        )

    return result


def _require_signed_unit_interval(
    value: object,
    *,
    name: str,
) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{name} must be numeric."
        ) from exc

    if not math.isfinite(result):
        raise ValueError(
            f"{name} must be finite."
        )

    if result < -1.0 or result > 1.0:
        raise ValueError(
            f"{name} must be between -1 and 1."
        )

    return result


def reduce_event_topology_risk_signal_v3(
    *,
    organization_id: uuid.UUID,
    event_id: uuid.UUID,
    evidence: tuple[EventTopologyRiskV3Evidence, ...],
    has_previous_state: bool,
) -> EventTopologyRiskV3Signal:
    if not evidence:
        raise ValueError(
            "topology risk evidence must not be empty"
        )

    validated = []

    for item in evidence:
        entity_id = _as_uuid(
            item.entity_id,
            field_name="topology.entity_id",
        )
        shift_score = _require_unit_interval(
            item.shift_score,
            name="topology.shift_score",
        )
        degree_centrality_delta = (
            _require_signed_unit_interval(
                item.degree_centrality_delta,
                name="topology.degree_centrality_delta",
            )
        )
        downstream_reach_ratio_delta = (
            _require_signed_unit_interval(
                item.downstream_reach_ratio_delta,
                name=(
                    "topology."
                    "downstream_reach_ratio_delta"
                ),
            )
        )

        validated.append(
            (
                entity_id,
                shift_score,
                degree_centrality_delta,
                downstream_reach_ratio_delta,
            )
        )

    representative = min(
        validated,
        key=lambda item: (
            -item[1],
            item[0].int,
        ),
    )

    (
        representative_entity_id,
        shift_score,
        degree_centrality_delta,
        downstream_reach_ratio_delta,
    ) = representative

    return EventTopologyRiskV3Signal(
        organization_id=organization_id,
        event_id=event_id,
        entity_count=len(evidence),
        representative_entity_id=(
            representative_entity_id
        ),
        shift_score=round(shift_score, 6),
        degree_centrality_delta=round(
            degree_centrality_delta,
            6,
        ),
        downstream_reach_ratio_delta=round(
            downstream_reach_ratio_delta,
            6,
        ),
        has_previous_state=has_previous_state,
    )


def load_event_topology_risk_signal_v3(
    db: Session,
    event: SemanticDriftEvent,
) -> EventTopologyRiskV3Signal | None:
    """
    Load persisted topology-shift evidence for one event and reduce
    the entity-level rows into one coherent event-level signal.

    Returns None when topology shift snapshots have not yet been
    persisted for the event.
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
        EventTopologyRiskV3Evidence(
            entity_id=_as_uuid(
                row.entity_id,
                field_name="topology.entity_id",
            ),
            shift_score=row.shift_score,
            degree_centrality_delta=(
                row.degree_centrality_delta
            ),
            downstream_reach_ratio_delta=(
                row.downstream_reach_ratio_delta
            ),
        )
        for row in rows
    )

    return reduce_event_topology_risk_signal_v3(
        organization_id=organization_id,
        event_id=event_id,
        evidence=evidence,
        has_previous_state=(
            previous_observed_at is not None
        ),
    )
