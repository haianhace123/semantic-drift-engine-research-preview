from __future__ import annotations

from dataclasses import dataclass
import uuid

from sqlalchemy.orm import Session

from src.models.semantic_drift_event import SemanticDriftEvent
from src.models.temporal_lineage_invalidation_decision_provenance import (
    TemporalLineageInvalidationDecisionProvenanceRecord,
)
from src.services.lineage_invalidation_control_plane_projection_service import (
    LineageInvalidationControlPlaneView,
    load_lineage_invalidation_control_plane_view,
)
from src.services.temporal_lineage_invalidation_service import (
    TemporalLineageInvalidationEventNotFoundError,
)


@dataclass(frozen=True)
class TemporalLineageInvalidationResolution:
    event_id: uuid.UUID
    old_snapshot_id: uuid.UUID
    new_snapshot_id: uuid.UUID
    decisions: tuple[
        LineageInvalidationControlPlaneView,
        ...,
    ]


def load_temporal_lineage_invalidation_resolution(
    db: Session,
    *,
    organization_id: uuid.UUID,
    event_id: uuid.UUID,
) -> TemporalLineageInvalidationResolution:
    event = (
        db.query(SemanticDriftEvent)
        .filter(
            SemanticDriftEvent.id == event_id,
            SemanticDriftEvent.organization_id == organization_id,
        )
        .one_or_none()
    )

    if event is None:
        raise TemporalLineageInvalidationEventNotFoundError(
            "Semantic drift event is not available in the organization."
        )

    provenance_records = (
        db.query(
            TemporalLineageInvalidationDecisionProvenanceRecord
        )
        .filter(
            (
                TemporalLineageInvalidationDecisionProvenanceRecord.organization_id
                == organization_id
            ),
            (
                TemporalLineageInvalidationDecisionProvenanceRecord.semantic_drift_event_id
                == event_id
            ),
        )
        .order_by(
            TemporalLineageInvalidationDecisionProvenanceRecord
            .decision_id.asc()
        )
        .all()
    )

    decisions = []

    for provenance in provenance_records:
        view = load_lineage_invalidation_control_plane_view(
            db,
            organization_id=organization_id,
            decision_record_id=provenance.decision_id,
        )

        if view is None:
            raise RuntimeError(
                "Temporal invalidation provenance references a missing "
                "lineage invalidation decision."
            )

        decisions.append(view)

    return TemporalLineageInvalidationResolution(
        event_id=event.id,
        old_snapshot_id=event.old_snapshot_id,
        new_snapshot_id=event.new_snapshot_id,
        decisions=tuple(decisions),
    )