from __future__ import annotations

from dataclasses import dataclass
import uuid

from sqlalchemy.orm import Session

from src.domain.lineage_invalidation import (
    LineageInvalidationPolicyIdentity,
)
from src.domain.lineage_revision import (
    LineageRevisionIdentity,
)
from src.models.semantic_drift_event import (
    SemanticDriftEvent,
    VECTOR_ANALYSIS_STATUS_COMPLETED,
)
from src.services.lineage_invalidation_service import (
    PersistedLineageInvalidationAnalysis,
    analyze_and_persist_lineage_invalidation_decisions,
)
from src.services.temporal_lineage_correlation_service import (
    resolve_temporal_lineage_roots,
)
from src.services.temporal_lineage_invalidation_decision_provenance_service import (
    persist_temporal_lineage_invalidation_decision_provenance,
)


class TemporalLineageInvalidationEventNotFoundError(RuntimeError):
    """Raised when the drift event is outside the caller's tenant scope."""


class TemporalLineageInvalidationNotReadyError(RuntimeError):
    """Raised when required change evidence is not ready."""


class TemporalLineageInvalidationEvidenceConflictError(RuntimeError):
    """A completed semantic result has inconsistent materiality evidence."""


@dataclass(frozen=True)
class TemporalLineageInvalidationAnalysis:
    event_id: uuid.UUID
    old_snapshot_id: uuid.UUID
    new_snapshot_id: uuid.UUID
    roots: tuple[LineageRevisionIdentity, ...]
    analyses: tuple[
        PersistedLineageInvalidationAnalysis,
        ...,
    ]


def analyze_temporal_lineage_invalidation(
    db: Session,
    *,
    organization_id: uuid.UUID,
    event_id: uuid.UUID,
    policy_identity: LineageInvalidationPolicyIdentity,
) -> TemporalLineageInvalidationAnalysis:
    event = _load_event(
        db,
        organization_id=organization_id,
        event_id=event_id,
    )

    _require_ready_event(event)
    material_change = _require_coherent_materiality(event)

    # A semantic-equivalent edit is an observed change, not a causal
    # knowledge invalidation. Never use topology alone to block consumers.
    if not material_change:
        return TemporalLineageInvalidationAnalysis(
            event_id=event.id,
            old_snapshot_id=event.old_snapshot_id,
            new_snapshot_id=event.new_snapshot_id,
            roots=(),
            analyses=(),
        )

    roots = resolve_temporal_lineage_roots(
        db,
        organization_id=organization_id,
        page_snapshot_id=event.old_snapshot_id,
    )

    analyses = tuple(
        analyze_and_persist_lineage_invalidation_decisions(
            db,
            organization_id=organization_id,
            root=root,
            policy_identity=policy_identity,
        )
        for root in roots
    )

    for analysis in analyses:
        for persisted_decision in analysis.decisions:
            persist_temporal_lineage_invalidation_decision_provenance(
                db,
                organization_id=organization_id,
                semantic_drift_event_id=event.id,
                decision_id=persisted_decision.record.id,
            )

    return TemporalLineageInvalidationAnalysis(
        event_id=event.id,
        old_snapshot_id=event.old_snapshot_id,
        new_snapshot_id=event.new_snapshot_id,
        roots=roots,
        analyses=analyses,
    )


def _load_event(
    db: Session,
    *,
    organization_id: uuid.UUID,
    event_id: uuid.UUID,
) -> SemanticDriftEvent:
    event = (
        db.query(SemanticDriftEvent)
        .filter(
            SemanticDriftEvent.id == event_id,
            SemanticDriftEvent.organization_id
            == organization_id,
        )
        .one_or_none()
    )

    if event is None:
        raise TemporalLineageInvalidationEventNotFoundError(
            "Semantic drift event is not available in the organization."
        )

    return event


def _require_ready_event(
    event: SemanticDriftEvent,
) -> None:
    if event.analysis_status != "completed":
        raise TemporalLineageInvalidationNotReadyError(
            "Temporal lineage invalidation requires completed semantic analysis."
        )

    if (
        event.vector_analysis_status
        != VECTOR_ANALYSIS_STATUS_COMPLETED
    ):
        raise TemporalLineageInvalidationNotReadyError(
            "Temporal lineage invalidation requires completed vector analysis."
        )


def _require_coherent_materiality(event: SemanticDriftEvent) -> bool:
    """Use persisted semantic evidence; never infer materiality from a text hash.

    The detector stores validated provider changes under semantic_analysis.
    Historical event producers may store the same change list at the root.
    Either representation must agree exactly with changes_count.
    """
    count = event.changes_count
    delta = event.semantic_delta_json
    if type(count) is not int or count < 0 or not isinstance(delta, dict):
        raise TemporalLineageInvalidationEvidenceConflictError(
            "Completed semantic materiality evidence is invalid."
        )
    nested = delta.get("semantic_analysis")
    if nested is not None:
        if not isinstance(nested, dict):
            raise TemporalLineageInvalidationEvidenceConflictError(
                "Completed semantic materiality evidence is invalid."
            )
        changes = nested.get("changes")
    else:
        changes = delta.get("changes")
    if (
        not isinstance(changes, list)
        or len(changes) != count
        or any(not isinstance(item, dict) for item in changes)
    ):
        raise TemporalLineageInvalidationEvidenceConflictError(
            "Completed semantic materiality count does not match its evidence."
        )
    if count == 0 and event.risk_level != "low":
        raise TemporalLineageInvalidationEvidenceConflictError(
            "Equivalent semantic content requires low materiality risk."
        )
    return count > 0
