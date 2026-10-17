"""Tenant-scoped qualification of persisted Pattern risk evidence.

Historical Pattern rows remain immutable. A risk escalation pattern is eligible
for current decisions only if every source event has a qualified Risk v2
assessment that was materialized no later than the Pattern itself.

Other pattern types are unaffected: they do not claim a Risk v2 sequence.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlalchemy.orm import Session

from src.models.monitored_page import MonitoredPage
from src.models.pattern import Pattern
from src.models.risk_assessment import RiskAssessment
from src.models.semantic_drift_event import SemanticDriftEvent
from src.services.risk_version import RISK_VERSION_V2

_QUERY_EVENT_BATCH_SIZE = 400


def _event_ids(pattern: Pattern) -> frozenset[uuid.UUID] | None:
    raw_ids = pattern.event_ids
    if not isinstance(raw_ids, (list, tuple)) or not raw_ids:
        return None
    try:
        ids = tuple(uuid.UUID(str(value)) for value in raw_ids)
    except (TypeError, ValueError, AttributeError):
        return None
    if len(set(ids)) != len(ids):
        return None
    return frozenset(ids)


def qualified_pattern_ids(
    db: Session,
    *,
    organization_id: uuid.UUID,
    patterns: Iterable[Pattern],
) -> frozenset[uuid.UUID]:
    """Return IDs eligible for current use; do not mutate historical rows.

    Missing events, foreign-tenant evidence, invalid lifecycle states, missing
    Risk v2 assessments and malformed event lists fail closed for escalation.
    The materialization and effective-time fences prevent newer assessments
    from retroactively qualifying a pattern detected with older evidence.
    """
    patterns = tuple(patterns)
    allowed: set[uuid.UUID] = set()
    risk_events: dict[uuid.UUID, frozenset[uuid.UUID]] = {}
    cutoff_by_pattern: dict[uuid.UUID, tuple] = {}
    all_event_ids: set[uuid.UUID] = set()

    for pattern in patterns:
        if pattern.organization_id != organization_id:
            continue
        if pattern.pattern_type != 'risk_escalation':
            allowed.add(pattern.id)
            continue
        event_ids = _event_ids(pattern)
        if event_ids is None or pattern.created_at is None or pattern.detected_at is None:
            continue
        risk_events[pattern.id] = event_ids
        cutoff_by_pattern[pattern.id] = (pattern.created_at, pattern.detected_at)
        all_event_ids.update(event_ids)

    if not all_event_ids:
        return frozenset(allowed)

    # Use a bounded query, not one lookup per Pattern or an unbounded IN list.
    evidence: dict[uuid.UUID, list[tuple]] = {}
    ordered = sorted(all_event_ids, key=str)
    for start in range(0, len(ordered), _QUERY_EVENT_BATCH_SIZE):
        batch = ordered[start:start + _QUERY_EVENT_BATCH_SIZE]
        rows = (
            db.query(
                RiskAssessment.semantic_drift_event_id,
                RiskAssessment.created_at,
                RiskAssessment.assessed_at,
            )
            .join(
                SemanticDriftEvent,
                SemanticDriftEvent.id == RiskAssessment.semantic_drift_event_id,
            )
            .join(
                MonitoredPage,
                MonitoredPage.id == SemanticDriftEvent.monitored_page_id,
            )
            .filter(
                RiskAssessment.organization_id == organization_id,
                RiskAssessment.risk_version == RISK_VERSION_V2,
                RiskAssessment.semantic_drift_event_id.in_(batch),
                SemanticDriftEvent.organization_id == organization_id,
                SemanticDriftEvent.analysis_status == 'completed',
                SemanticDriftEvent.vector_analysis_status == 'completed',
                MonitoredPage.organization_id == organization_id,
            )
            .all()
        )
        for event_id, created_at, assessed_at in rows:
            evidence.setdefault(event_id, []).append((created_at, assessed_at))

    for pattern_id, event_ids in risk_events.items():
        created_cutoff, effective_cutoff = cutoff_by_pattern[pattern_id]
        if all(
            any(
                created is not None and assessed is not None
                and created <= created_cutoff and assessed <= effective_cutoff
                for created, assessed in evidence.get(event_id, ())
            )
            for event_id in event_ids
        ):
            allowed.add(pattern_id)

    return frozenset(allowed)
