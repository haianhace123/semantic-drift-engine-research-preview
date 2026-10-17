"""
Anomaly Detection v1 orchestration.

Connects tenant-scoped temporal evidence to the pure deterministic anomaly
detector.

This module is intentionally read-only:
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

from src.services.anomaly_detection_service import (
    DetectedAnomaly,
    detect_activity_burst,
)
from src.services.anomaly_evidence_service import (
    AnomalyEvidenceBundle,
    load_anomaly_evidence,
)


@dataclass(frozen=True)
class AnomalyDetectionResult:
    organization_id: uuid.UUID
    as_of: datetime
    baseline_start: datetime
    current_start: datetime
    baseline_days: int
    window_days: int
    evidence_historical_count: int
    evidence_current_count: int
    anomalies: tuple[
        DetectedAnomaly,
        ...
    ]


def _anomaly_sort_key(
    anomaly: DetectedAnomaly,
):
    return (
        anomaly.last_current_event_at,
        str(
            anomaly.entity_id
        ),
        str(
            anomaly.monitored_page_id
        ),
        anomaly.anomaly_type,
        str(
            anomaly.event_id
        ),
    )


def detect_organization_anomalies(
    db: Session,
    *,
    organization_id,
    as_of: datetime,
    baseline_days: int = 28,
    window_days: int = 7,
    minimum_historical_events: int = 4,
    minimum_current_events: int = 3,
    minimum_ratio: float = 2.0,
) -> AnomalyDetectionResult:
    """
    Detect deterministic Anomaly v1 candidates for one organization.

    Each monitored page is compared only with its own historical baseline.
    Temporal and tenant filtering are delegated to load_anomaly_evidence().
    """
    evidence: AnomalyEvidenceBundle = (
        load_anomaly_evidence(
            db,
            organization_id=organization_id,
            as_of=as_of,
            baseline_days=baseline_days,
            window_days=window_days,
        )
    )

    anomalies: list[
        DetectedAnomaly
    ] = []

    for page in evidence.pages:
        anomaly = detect_activity_burst(
            historical_events=(
                page.historical_events
            ),
            current_events=(
                page.current_events
            ),
            baseline_days=(
                evidence.baseline_days
            ),
            window_days=(
                evidence.window_days
            ),
            minimum_historical_events=(
                minimum_historical_events
            ),
            minimum_current_events=(
                minimum_current_events
            ),
            minimum_ratio=(
                minimum_ratio
            ),
        )

        if anomaly is not None:
            anomalies.append(
                anomaly
            )

    anomalies.sort(
        key=_anomaly_sort_key
    )

    return AnomalyDetectionResult(
        organization_id=(
            evidence.organization_id
        ),
        as_of=evidence.as_of,
        baseline_start=(
            evidence.baseline_start
        ),
        current_start=(
            evidence.current_start
        ),
        baseline_days=(
            evidence.baseline_days
        ),
        window_days=(
            evidence.window_days
        ),
        evidence_historical_count=(
            evidence.historical_event_count
        ),
        evidence_current_count=(
            evidence.current_event_count
        ),
        anomalies=tuple(
            anomalies
        ),
    )