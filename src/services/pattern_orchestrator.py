"""
Pattern Detection v1 orchestration.

This module connects tenant-scoped persisted temporal evidence to the
pure deterministic pattern detectors.

It is intentionally read-only:
- no persistence;
- no event mutation;
- no commit or flush;
- no LLM or external API calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import uuid

from sqlalchemy.orm import Session

from src.services.pattern_detection_service import (
    DetectedPattern,
    detect_repeated_drift_patterns,
    detect_risk_escalation_patterns,
    detect_topology_sequence_patterns,
)
from src.services.pattern_evidence_service import (
    PatternEvidenceBundle,
    load_pattern_evidence,
)


@dataclass(frozen=True)
class PatternDetectionResult:
    organization_id: uuid.UUID
    as_of: datetime
    window_start: datetime
    lookback_days: int
    evidence_event_count: int
    evidence_risk_count: int
    evidence_topology_count: int
    patterns: tuple[
        DetectedPattern,
        ...
    ]


def _pattern_sort_key(
    pattern: DetectedPattern,
):
    return (
        pattern.first_event_at,
        pattern.last_event_at,
        str(pattern.entity_id),
        pattern.pattern_type,
        tuple(
            str(event_id)
            for event_id in pattern.event_ids
        ),
    )


def detect_organization_patterns(
    db: Session,
    *,
    organization_id,
    as_of: datetime,
    knowledge_cutoff: datetime | None = None,
    knowledge_cutoff_record_id=None,
    lookback_days: int = 30,
    repeated_drift_minimum_events: int = 3,
    risk_escalation_minimum_events: int = 3,
    risk_escalation_minimum_step: float = 0.05,
    topology_sequence_minimum_events: int = 2,
) -> PatternDetectionResult:
    """
    Detect deterministic Pattern v1 candidates for one organization.

    Temporal and tenant filtering are delegated to
    load_pattern_evidence(). This function only orchestrates the pure
    detectors and returns a normalized deterministic result.
    """
    evidence: PatternEvidenceBundle = (
        load_pattern_evidence(
            db,
            organization_id=organization_id,
            as_of=as_of,
            knowledge_cutoff=knowledge_cutoff,
            knowledge_cutoff_record_id=(
                knowledge_cutoff_record_id
            ),
            lookback_days=lookback_days,
        )
    )

    patterns: list[
        DetectedPattern
    ] = []

    patterns.extend(
        detect_repeated_drift_patterns(
            list(
                evidence.events
            ),
            minimum_events=(
                repeated_drift_minimum_events
            ),
        )
    )

    patterns.extend(
        detect_risk_escalation_patterns(
            list(
                evidence.risk_assessments
            ),
            minimum_events=(
                risk_escalation_minimum_events
            ),
            minimum_step=(
                risk_escalation_minimum_step
            ),
        )
    )

    patterns.extend(
        detect_topology_sequence_patterns(
            list(
                evidence.topology_shifts
            ),
            minimum_events=(
                topology_sequence_minimum_events
            ),
        )
    )

    patterns.sort(
        key=_pattern_sort_key
    )

    return PatternDetectionResult(
        organization_id=(
            evidence.organization_id
        ),
        as_of=evidence.as_of,
        window_start=(
            evidence.window_start
        ),
        lookback_days=(
            evidence.lookback_days
        ),
        evidence_event_count=len(
            evidence.events
        ),
        evidence_risk_count=len(
            evidence.risk_assessments
        ),
        evidence_topology_count=len(
            evidence.topology_shifts
        ),
        patterns=tuple(
            patterns
        ),
    )