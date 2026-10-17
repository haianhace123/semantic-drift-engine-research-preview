"""
Production execution composition for deterministic Anomaly v1.

Flow:
    tenant-scoped temporal evidence
    -> deterministic anomaly detection
    -> PostgreSQL persistence

Transaction ownership remains with the caller:
- no commit;
- no rollback;
- no external API;
- no LLM.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import uuid

from sqlalchemy.orm import Session

from src.services.anomaly_orchestrator import (
    AnomalyDetectionResult,
    detect_organization_anomalies,
)
from src.services.anomaly_persistence_service import (
    PersistedAnomalyResult,
    persist_detected_anomalies,
)


@dataclass(frozen=True)
class AnomalyExecutionResult:
    organization_id: uuid.UUID
    as_of: datetime
    baseline_start: datetime
    current_start: datetime
    baseline_days: int
    window_days: int
    evidence_historical_count: int
    evidence_current_count: int
    detected_count: int
    created_count: int
    existing_count: int
    persisted: tuple[
        PersistedAnomalyResult,
        ...
    ]


def execute_anomaly_detection(
    db: Session,
    *,
    organization_id,
    as_of: datetime,
    baseline_days: int = 28,
    window_days: int = 7,
    minimum_historical_events: int = 4,
    minimum_current_events: int = 3,
    minimum_ratio: float = 2.0,
    detection_method: str | None = None,
) -> AnomalyExecutionResult:
    """
    Detect and persist deterministic anomalies for one organization.

    The caller owns the surrounding transaction.
    """
    detection: AnomalyDetectionResult = (
        detect_organization_anomalies(
            db,
            organization_id=organization_id,
            as_of=as_of,
            baseline_days=baseline_days,
            window_days=window_days,
            minimum_historical_events=(
                minimum_historical_events
            ),
            minimum_current_events=(
                minimum_current_events
            ),
            minimum_ratio=minimum_ratio,
        )
    )

    persisted = persist_detected_anomalies(
        db,
        organization_id=(
            detection.organization_id
        ),
        anomalies=detection.anomalies,
        detected_at=detection.as_of,
        detection_method=detection_method,
    )

    created_count = sum(
        1
        for item in persisted
        if item.created
    )

    detected_count = len(
        detection.anomalies
    )

    return AnomalyExecutionResult(
        organization_id=(
            detection.organization_id
        ),
        as_of=detection.as_of,
        baseline_start=(
            detection.baseline_start
        ),
        current_start=(
            detection.current_start
        ),
        baseline_days=(
            detection.baseline_days
        ),
        window_days=(
            detection.window_days
        ),
        evidence_historical_count=(
            detection.evidence_historical_count
        ),
        evidence_current_count=(
            detection.evidence_current_count
        ),
        detected_count=detected_count,
        created_count=created_count,
        existing_count=(
            detected_count
            - created_count
        ),
        persisted=persisted,
    )