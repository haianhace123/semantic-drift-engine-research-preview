"""
Production execution path for deterministic Pattern v1 detection.

This module composes the read-only pattern orchestrator with deterministic
PostgreSQL persistence.

Transaction ownership remains with the caller:
- no commit;
- no rollback;
- persistence and event-flag updates remain in the caller transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import uuid

from sqlalchemy.orm import Session

from src.services.pattern_orchestrator import (
    PatternDetectionResult,
    detect_organization_patterns,
)
from src.services.pattern_persistence_service import (
    PersistedPatternResult,
    persist_detected_patterns,
)


@dataclass(frozen=True)
class PatternExecutionResult:
    organization_id: uuid.UUID
    as_of: datetime
    detection: PatternDetectionResult
    persisted_patterns: tuple[PersistedPatternResult, ...]

    @property
    def detected_count(self) -> int:
        return len(
            self.detection.patterns
        )

    @property
    def created_count(self) -> int:
        return sum(
            1
            for result in self.persisted_patterns
            if result.created
        )

    @property
    def existing_count(self) -> int:
        return sum(
            1
            for result in self.persisted_patterns
            if not result.created
        )


def execute_pattern_detection(
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
) -> PatternExecutionResult:
    """
    Detect and persist deterministic Pattern v1 results.

    The exact `as_of` cursor is used as the persistence detection timestamp,
    keeping the execution reproducible and avoiding wall-clock timestamps
    inside this orchestration layer.

    The caller owns commit/rollback.
    """
    detection = detect_organization_patterns(
        db,
        organization_id=organization_id,
        as_of=as_of,
        knowledge_cutoff=knowledge_cutoff,
        knowledge_cutoff_record_id=(
            knowledge_cutoff_record_id
        ),
        lookback_days=lookback_days,
        repeated_drift_minimum_events=(
            repeated_drift_minimum_events
        ),
        risk_escalation_minimum_events=(
            risk_escalation_minimum_events
        ),
        risk_escalation_minimum_step=(
            risk_escalation_minimum_step
        ),
        topology_sequence_minimum_events=(
            topology_sequence_minimum_events
        ),
    )

    persisted_patterns = persist_detected_patterns(
        db,
        organization_id=detection.organization_id,
        patterns=detection.patterns,
        detected_at=detection.as_of,
    )

    return PatternExecutionResult(
        organization_id=detection.organization_id,
        as_of=detection.as_of,
        detection=detection,
        persisted_patterns=persisted_patterns,
    )