from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from src.models.semantic_drift_event import SemanticDriftEvent
from src.services.graph_metric_snapshot_service import (
    persist_graph_metric_snapshots_for_event,
)
from src.services.topology_shift_snapshot_service import (
    persist_topology_shift_snapshots_for_event,
)


class TopologyPreparationError(RuntimeError):
    """Base error for topology preparation."""


class TopologyPreparationNotReadyError(
    TopologyPreparationError
):
    """
    Raised when an event is not ready for temporal topology
    preparation.
    """


@dataclass(frozen=True)
class TopologyPreparationResult:
    organization_id: uuid.UUID
    event_id: uuid.UUID

    graph_created: int
    graph_existing: int

    topology_created: int
    topology_existing: int


def _as_uuid(
    value: object,
    *,
    field_name: str,
) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value

    try:
        return uuid.UUID(str(value))
    except (
        TypeError,
        ValueError,
        AttributeError,
    ) as exc:
        raise TopologyPreparationError(
            f"{field_name} must be a valid UUID"
        ) from exc


def _validate_event(
    event: SemanticDriftEvent,
) -> tuple[
    uuid.UUID,
    uuid.UUID,
]:
    event_id = getattr(
        event,
        "id",
        None,
    )

    if event_id is None:
        raise TopologyPreparationError(
            "event.id is required"
        )

    organization_id = getattr(
        event,
        "organization_id",
        None,
    )

    if organization_id is None:
        raise TopologyPreparationError(
            "event.organization_id is required"
        )

    detected_at = getattr(
        event,
        "detected_at",
        None,
    )

    if detected_at is None:
        raise TopologyPreparationError(
            "event.detected_at is required"
        )

    if (
        getattr(
            event,
            "vector_analysis_status",
            None,
        )
        != "completed"
    ):
        raise TopologyPreparationNotReadyError(
            "topology preparation requires "
            "completed vector analysis"
        )

    if getattr(
        event,
        "semantic_magnitude",
        None,
    ) is None:
        raise TopologyPreparationNotReadyError(
            "topology preparation requires "
            "semantic_magnitude"
        )

    return (
        _as_uuid(
            organization_id,
            field_name="event.organization_id",
        ),
        _as_uuid(
            event_id,
            field_name="event.id",
        ),
    )


def prepare_topology_for_event(
    db: Session,
    *,
    event: SemanticDriftEvent,
) -> TopologyPreparationResult:
    """
    Ensure temporal graph metrics and topology shift evidence
    exist for one vector-ready SemanticDriftEvent.

    Both persistence services are idempotent.

    The caller owns the transaction. This function does not
    commit or rollback.
    """
    (
        organization_id,
        event_id,
    ) = _validate_event(event)

    graph_result = (
        persist_graph_metric_snapshots_for_event(
            db=db,
            event=event,
        )
    )

    topology_result = (
        persist_topology_shift_snapshots_for_event(
            db=db,
            event=event,
        )
    )

    return TopologyPreparationResult(
        organization_id=organization_id,
        event_id=event_id,
        graph_created=graph_result.created,
        graph_existing=graph_result.existing,
        topology_created=topology_result.created,
        topology_existing=topology_result.existing,
    )