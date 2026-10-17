"""Read-only, event-level qualification of persisted Risk v2/v3 assessments.

This is an exposure/evidence audit, NOT a scored model or ground truth review.
One Risk v2 assessment is the cohort unit; V3 is paired by organization/event.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from uuid import UUID

from src.services.risk_classification_service import classify_risk_score
from src.services.risk_version import RISK_VERSION_V2, RISK_VERSION_V3

MAX_EVENTS = 5000
MAX_REVIEW_IDS = 50


@dataclass(frozen=True)
class QualificationAssessment:
    organization_id: UUID
    event_id: UUID | None
    risk_version: str
    score: float
    confidence_contribution: float
    has_topology_signal: bool
    has_previous_topology_state: bool
    topology_contribution: float


def _unit(value: float, name: str) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(value) or not (0 <= value <= 1):
        raise ValueError(f"{name} must be finite within [0,1]")
    return value


def _validate_sample(row: QualificationAssessment, *, organization_id: UUID, version: str) -> None:
    if row.organization_id != organization_id or row.risk_version != version:
        raise ValueError("cross-tenant or incorrect risk version in qualification cohort")
    score = _unit(row.score, "score")
    confidence = _unit(row.confidence_contribution, "confidence contribution")
    topology = _unit(row.topology_contribution, "topology contribution")
    if confidence > 0.100001 or confidence > score + 0.000001:
        raise ValueError("invalid persisted confidence contribution")
    if topology > 0.250001 or topology > score + 0.000001:
        raise ValueError("invalid persisted topology contribution")
    if type(row.has_topology_signal) is not bool or type(row.has_previous_topology_state) is not bool:
        raise ValueError("topology states must be booleans")
    if row.has_previous_topology_state and not row.has_topology_signal:
        raise ValueError("previous topology state without topology signal")
    if not row.has_previous_topology_state and topology > 0.000001:
        raise ValueError("topology contribution without previous topology state")
    if row.event_id is not None and not isinstance(row.event_id, UUID):
        raise ValueError("event id must be UUID")


def summarize_risk_qualification(
    baseline: list[QualificationAssessment],
    candidate: list[QualificationAssessment],
    *,
    organization_id: UUID,
    truncated: bool,
    include_review_event_ids: bool = False,
) -> dict[str, object]:
    """Produce defensible aggregate counts; never call these accuracy metrics."""
    if not isinstance(organization_id, UUID) or type(truncated) is not bool:
        raise ValueError("invalid audit scope")
    if type(include_review_event_ids) is not bool:
        raise ValueError("invalid review-id option")

    by_event: dict[UUID, QualificationAssessment] = {}
    unlinked = 0
    counts: Counter[str] = Counter()
    transitions: Counter[str] = Counter()
    review_ids: list[str] = []
    v2_labels: Counter[str] = Counter()
    paired_labels: Counter[str] = Counter()

    for row in baseline:
        _validate_sample(row, organization_id=organization_id, version=RISK_VERSION_V2)
        if row.event_id is None:
            unlinked += 1
        elif row.event_id in by_event:
            raise ValueError("duplicate Risk v2 assessment for the same event")
        else:
            by_event[row.event_id] = row

        v2_band = classify_risk_score(row.score)
        v2_labels[v2_band] += 1
        adjusted = classify_risk_score(max(0.0, row.score - row.confidence_contribution))
        if v2_band != adjusted:
            counts["confidence_band_flips"] += 1
            transitions[f"{v2_band}_to_{adjusted}"] += 1
            if include_review_event_ids and row.event_id is not None and len(review_ids) < MAX_REVIEW_IDS:
                review_ids.append(str(row.event_id))

        if row.has_previous_topology_state:
            counts["topology_comparable"] += 1
            if row.topology_contribution > 0:
                counts["topology_positive_contribution"] += 1
        elif row.has_topology_signal:
            counts["topology_baseline_only"] += 1
        else:
            counts["topology_unavailable"] += 1

    candidate_ids: set[UUID] = set()
    for row in candidate:
        _validate_sample(row, organization_id=organization_id, version=RISK_VERSION_V3)
        if row.event_id is None or row.event_id not in by_event:
            raise ValueError("unmatched Risk v3 row in qualification cohort")
        if row.event_id in candidate_ids:
            raise ValueError("duplicate Risk v3 assessment for the same event")
        candidate_ids.add(row.event_id)
        base = by_event[row.event_id]
        paired_labels[classify_risk_score(row.score)] += 1
        if classify_risk_score(row.score) != classify_risk_score(base.score):
            counts["paired_version_severity_disagreement"] += 1
        if abs(row.score - base.score) > 0.000001:
            counts["paired_version_score_difference"] += 1
        if (row.has_topology_signal != base.has_topology_signal or
                row.has_previous_topology_state != base.has_previous_topology_state):
            counts["paired_version_topology_state_disagreement"] += 1

    linked = len(by_event)
    n = len(baseline)
    if not n:
        qualification = "no_baseline_evidence"
    elif truncated:
        qualification = "bounded_sample_only"
    elif unlinked or len(candidate_ids) != linked:
        qualification = "incomplete_event_pairing"
    else:
        qualification = "paired_cohort_available_not_ground_truth"

    result: dict[str, object] = {
        "baseline_version": RISK_VERSION_V2,
        "shadow_version": RISK_VERSION_V3,
        "sample_scope": "latest_v2_assessments_by_assessed_at",
        "truncated": truncated,
        "qualification": qualification,
        "baseline_assessments": n,
        "linked_unique_baseline_events": linked,
        "unlinked_baseline_assessments": unlinked,
        "paired_shadow_events": len(candidate_ids),
        "missing_shadow_events": linked - len(candidate_ids),
        "pair_coverage_of_linked": round(len(candidate_ids) / linked, 6) if linked else None,
        "baseline_severity_counts": dict(sorted(v2_labels.items())),
        "shadow_paired_severity_counts": dict(sorted(paired_labels.items())),
        "topology_comparable": counts["topology_comparable"],
        "topology_baseline_only": counts["topology_baseline_only"],
        "topology_unavailable": counts["topology_unavailable"],
        "topology_positive_contribution": counts["topology_positive_contribution"],
        "confidence_band_flips": counts["confidence_band_flips"],
        "confidence_band_flip_rate": round(counts["confidence_band_flips"] / n, 6) if n else None,
        "confidence_transitions": dict(sorted(transitions.items())),
        "paired_version_severity_disagreement": counts["paired_version_severity_disagreement"],
        "paired_version_score_difference": counts["paired_version_score_difference"],
        "paired_version_topology_state_disagreement": counts["paired_version_topology_state_disagreement"],
        "independent_severity_labels": 0,
        "model_accuracy_evaluated": False,
    }
    if include_review_event_ids:
        result["review_event_ids"] = review_ids
        result["review_id_limit"] = MAX_REVIEW_IDS
    return result


def load_qualification_cohort(db: object, *, organization_id: UUID, limit: int) -> tuple[list[QualificationAssessment], list[QualificationAssessment], bool]:
    """Select latest v2 rows and only tenant-matched v3 counterparts."""
    if not isinstance(organization_id, UUID):
        raise ValueError("organization_id must be UUID")
    if type(limit) is not int or not 1 <= limit <= MAX_EVENTS:
        raise ValueError("limit must be between 1 and 5000")

    from sqlalchemy import select
    from src.models.risk_assessment import RiskAssessment
    from src.models.semantic_drift_event import SemanticDriftEvent

    fields = (
        RiskAssessment.organization_id,
        RiskAssessment.semantic_drift_event_id,
        RiskAssessment.risk_version,
        RiskAssessment.score,
        RiskAssessment.confidence_contribution,
        RiskAssessment.has_topology_signal,
        RiskAssessment.has_previous_topology_state,
        RiskAssessment.topology_contribution,
    )
    # Validate the referenced event's tenant; assessment.organization_id alone
    # is insufficient to prove that a referenced event belongs to this tenant.
    def scoped_rows(rows: list[tuple]) -> list[QualificationAssessment]:
        samples = []
        for row in rows:
            *assessment, event_org = row
            if assessment[1] is not None and event_org != organization_id:
                raise ValueError("risk assessment references missing or cross-tenant event")
            samples.append(QualificationAssessment(*assessment))
        return samples

    stmt = (select(*fields, SemanticDriftEvent.organization_id)
        .outerjoin(SemanticDriftEvent.__table__,
                   RiskAssessment.semantic_drift_event_id == SemanticDriftEvent.id)
        .where(RiskAssessment.organization_id == organization_id,
               RiskAssessment.risk_version == RISK_VERSION_V2)
        .order_by(RiskAssessment.assessed_at.desc(), RiskAssessment.id.desc())
        .limit(limit + 1))
    rows = db.execute(stmt).all()
    truncated = len(rows) > limit
    baseline = scoped_rows(rows[:limit])
    ids = [r.event_id for r in baseline if r.event_id is not None]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate Risk v2 event assessments; cannot qualify cohort")
    if not ids:
        return baseline, [], truncated
    shadow_stmt = (select(*fields, SemanticDriftEvent.organization_id)
        .outerjoin(SemanticDriftEvent.__table__,
                   RiskAssessment.semantic_drift_event_id == SemanticDriftEvent.id)
        .where(RiskAssessment.organization_id == organization_id,
               RiskAssessment.risk_version == RISK_VERSION_V3,
               RiskAssessment.semantic_drift_event_id.in_(ids))
        .order_by(RiskAssessment.semantic_drift_event_id, RiskAssessment.id))
    candidate = scoped_rows(db.execute(shadow_stmt).all())
    return baseline, candidate, truncated
