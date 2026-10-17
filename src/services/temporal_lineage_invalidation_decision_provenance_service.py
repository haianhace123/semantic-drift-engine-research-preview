from __future__ import annotations

from dataclasses import dataclass
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.models.temporal_lineage_invalidation_decision_provenance import (
    TemporalLineageInvalidationDecisionProvenanceRecord,
)


@dataclass(frozen=True)
class PersistedTemporalLineageInvalidationDecisionProvenance:
    record: TemporalLineageInvalidationDecisionProvenanceRecord
    created: bool


def persist_temporal_lineage_invalidation_decision_provenance(
    db: Session,
    *,
    organization_id: uuid.UUID,
    semantic_drift_event_id: uuid.UUID,
    decision_id: uuid.UUID,
) -> PersistedTemporalLineageInvalidationDecisionProvenance:
    existing = _load_provenance(
        db,
        organization_id=organization_id,
        semantic_drift_event_id=semantic_drift_event_id,
        decision_id=decision_id,
    )

    if existing is not None:
        return PersistedTemporalLineageInvalidationDecisionProvenance(
            record=existing,
            created=False,
        )

    record = TemporalLineageInvalidationDecisionProvenanceRecord(
        organization_id=organization_id,
        semantic_drift_event_id=semantic_drift_event_id,
        decision_id=decision_id,
    )

    try:
        with db.begin_nested():
            db.add(record)
            db.flush()
    except IntegrityError:
        existing = _load_provenance(
            db,
            organization_id=organization_id,
            semantic_drift_event_id=semantic_drift_event_id,
            decision_id=decision_id,
        )

        if existing is None:
            raise

        return PersistedTemporalLineageInvalidationDecisionProvenance(
            record=existing,
            created=False,
        )

    return PersistedTemporalLineageInvalidationDecisionProvenance(
        record=record,
        created=True,
    )


def _load_provenance(
    db: Session,
    *,
    organization_id: uuid.UUID,
    semantic_drift_event_id: uuid.UUID,
    decision_id: uuid.UUID,
) -> TemporalLineageInvalidationDecisionProvenanceRecord | None:
    return (
        db.query(
            TemporalLineageInvalidationDecisionProvenanceRecord
        )
        .filter(
            TemporalLineageInvalidationDecisionProvenanceRecord.organization_id
            == organization_id,
            TemporalLineageInvalidationDecisionProvenanceRecord.semantic_drift_event_id
            == semantic_drift_event_id,
            TemporalLineageInvalidationDecisionProvenanceRecord.decision_id
            == decision_id,
        )
        .one_or_none()
    )