"""Read-only, tenant-scoped evidence for a persisted semantic drift event.

This is an observability surface, not a readiness probe or a remediation
trigger.  Only persisted facts are reported; a stage marked configured in the
Operations API does not make it complete here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from src.services.risk_version import RISK_VERSION_V2


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        # Historical rows may carry timezone-naive UTC timestamps.
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _elapsed_ms(start: datetime | None, end: datetime | None) -> int | None:
    if start is None or end is None:
        return None
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    seconds = (end - start).total_seconds()
    return round(seconds * 1000) if seconds >= 0 else None


def _stage_from_persisted_status(status: str | None, complete: bool) -> str:
    if status == "completed":
        return "complete" if complete else "inconsistent"
    if status == "failed":
        return "failed"
    if status in ("pending", "processing"):
        return status
    return "unknown"


def summarize_pipeline_event(
    *,
    event: Any,
    old_snapshot: Any | None,
    new_snapshot: Any | None,
    risk_v2_assessment: Any | None,
    topology_shift_count: int,
    lineage_decision_count: int,
) -> dict[str, Any]:
    """Summarize only event-linked records, without querying or mutating data."""
    event_org = str(event.organization_id)
    event_page = str(event.monitored_page_id)

    def matching_snapshot(snapshot: Any | None, expected_id: Any) -> bool:
        return (
            snapshot is not None
            and str(snapshot.id) == str(expected_id)
            and str(snapshot.organization_id) == event_org
            and str(snapshot.monitored_page_id) == event_page
        )

    pair_valid = (
        str(event.old_snapshot_id) != str(event.new_snapshot_id)
        and matching_snapshot(old_snapshot, event.old_snapshot_id)
        and matching_snapshot(new_snapshot, event.new_snapshot_id)
        and old_snapshot.content_hash != new_snapshot.content_hash
    )
    embedded = pair_valid and all(
        snapshot.embedding_status == "completed"
        and snapshot.embedding is not None
        and bool(snapshot.embedding_model)
        and snapshot.embedding_dimensions is not None
        and len(snapshot.embedding) == snapshot.embedding_dimensions
        and snapshot.embedded_at is not None
        for snapshot in (old_snapshot, new_snapshot)
    )
    embedding_failed = any(
        snapshot is not None and snapshot.embedding_status == "failed"
        for snapshot in (old_snapshot, new_snapshot)
    )
    if embedded:
        embeddings_status = "complete"
    elif embedding_failed:
        embeddings_status = "failed"
    elif pair_valid and any(
        snapshot.embedding_status == "completed" and (
            snapshot.embedding is None
            or not snapshot.embedding_model
            or snapshot.embedding_dimensions is None
            or len(snapshot.embedding) != snapshot.embedding_dimensions
            or snapshot.embedded_at is None
        ) for snapshot in (old_snapshot, new_snapshot)
    ):
        embeddings_status = "inconsistent"
    elif pair_valid and any(
        snapshot.embedding_status == "processing"
        for snapshot in (old_snapshot, new_snapshot)
    ):
        embeddings_status = "processing"
    else:
        embeddings_status = "pending"
    vector_status = _stage_from_persisted_status(
        event.vector_analysis_status,
        event.semantic_similarity is not None
        and event.semantic_distance is not None
        and event.semantic_magnitude is not None
        and event.vector_analyzed_at is not None,
    )
    risk_status = _stage_from_persisted_status(
        event.risk_scoring_status,
        risk_v2_assessment is not None
        and str(risk_v2_assessment.organization_id) == event_org
        and str(risk_v2_assessment.semantic_drift_event_id) == str(event.id)
        and risk_v2_assessment.risk_version == RISK_VERSION_V2
        and event.risk_score is not None
        and abs(float(event.risk_score) - float(risk_v2_assessment.score) * 100.0) <= 0.02
        and event.risk_scored_at is not None,
    )
    analysis_status = _stage_from_persisted_status(
        event.analysis_status,
        bool(event.semantic_delta_json) and event.detected_at is not None,
    )
    stages = {
        "snapshot_pair": "complete" if pair_valid else "inconsistent",
        "drift_event_analysis": analysis_status,
        "embeddings": embeddings_status,
        "vector_analysis": vector_status,
        "risk_v2": risk_status,
        # An event need not have a topology shift, or a causal dependency.
        # Absence alone is not a pipeline failure or an invalidation decision.
        "topology_shift": "observed" if topology_shift_count > 0 else "not_observed",
        "causal_decisions": "observed" if lineage_decision_count > 0 else "not_observed",
    }
    vector_risk_complete = all(
        stages[k] == "complete"
        for k in (
            "snapshot_pair", "drift_event_analysis", "embeddings",
            "vector_analysis", "risk_v2",
        )
    )
    return {
        "schema_version": 1,
        "basis": "persisted_event_evidence",
        "organization_id": event_org,
        "event_id": str(event.id),
        "page_id": event_page,
        "old_snapshot_id": str(event.old_snapshot_id),
        "new_snapshot_id": str(event.new_snapshot_id),
        "stages": stages,
        "measurements": {
            "semantic_similarity": event.semantic_similarity,
            "semantic_distance": event.semantic_distance,
            "semantic_magnitude": event.semantic_magnitude,
            "risk_v2_score": (
                risk_v2_assessment.score if risk_v2_assessment is not None else None
            ),
            "topology_shift_records": topology_shift_count,
            "causal_decision_records": lineage_decision_count,
        },
        "timestamps": {
            "detected_at": _timestamp(event.detected_at),
            "old_embedded_at": _timestamp(
                getattr(old_snapshot, "embedded_at", None)
            ),
            "new_embedded_at": _timestamp(
                getattr(new_snapshot, "embedded_at", None)
            ),
            "vector_analyzed_at": _timestamp(event.vector_analyzed_at),
            "risk_scored_at": _timestamp(event.risk_scored_at),
        },
        "latency_ms": {
            "detection_to_vector": _elapsed_ms(
                event.detected_at, event.vector_analyzed_at
            ),
            "vector_to_risk": _elapsed_ms(
                event.vector_analyzed_at, event.risk_scored_at
            ),
        },
        "gates": {
            "vector_risk_complete": vector_risk_complete,
            "causal_decisions_observed": lineage_decision_count > 0,
        },
        "limitations": [
            "Embedding status does not verify external provider availability.",
            "An event record does not prove accuracy of semantic classification.",
            "A recorded decision does not authorize physical execution.",
            "Missing topology or lineage evidence may be legitimately inapplicable.",
        ],
    }


def read_pipeline_event_evidence(
    db: Any, *, organization_id: UUID, event_id: UUID
) -> dict[str, Any]:
    """Fetch a bounded event evidence set under an explicit tenant scope."""
    from src.models.page_snapshot import PageSnapshot
    from src.models.semantic_drift_event import SemanticDriftEvent
    from src.models.risk_assessment import RiskAssessment
    from src.models.topology_shift_snapshot import TopologyShiftSnapshot
    from src.models.temporal_lineage_invalidation_decision_provenance import (
        TemporalLineageInvalidationDecisionProvenanceRecord,
    )

    event = (
        db.query(SemanticDriftEvent)
        .filter(
            SemanticDriftEvent.id == event_id,
            SemanticDriftEvent.organization_id == organization_id,
        )
        .one_or_none()
    )
    if event is None:
        raise LookupError("No drift event found for the specified organization.")

    snapshots = (
        db.query(PageSnapshot)
        .filter(
            PageSnapshot.organization_id == organization_id,
            PageSnapshot.id.in_([event.old_snapshot_id, event.new_snapshot_id]),
        )
        .all()
    )
    by_id = {snapshot.id: snapshot for snapshot in snapshots}
    assessment = (
        db.query(RiskAssessment)
        .filter(
            RiskAssessment.organization_id == organization_id,
            RiskAssessment.semantic_drift_event_id == event_id,
            RiskAssessment.risk_version == RISK_VERSION_V2,
        )
        .order_by(RiskAssessment.assessed_at.desc())
        .first()
    )
    topology_count = (
        db.query(TopologyShiftSnapshot)
        .filter(
            TopologyShiftSnapshot.organization_id == organization_id,
            TopologyShiftSnapshot.semantic_drift_event_id == event_id,
        )
        .count()
    )
    decision_count = (
        db.query(TemporalLineageInvalidationDecisionProvenanceRecord)
        .filter(
            TemporalLineageInvalidationDecisionProvenanceRecord.organization_id
            == organization_id,
            TemporalLineageInvalidationDecisionProvenanceRecord.semantic_drift_event_id
            == event_id,
        )
        .count()
    )
    return summarize_pipeline_event(
        event=event,
        old_snapshot=by_id.get(event.old_snapshot_id),
        new_snapshot=by_id.get(event.new_snapshot_id),
        risk_v2_assessment=assessment,
        topology_shift_count=topology_count,
        lineage_decision_count=decision_count,
    )
