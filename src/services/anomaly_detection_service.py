"""
Pure deterministic Anomaly Detection v1.

The first production anomaly type is activity_burst. Each monitored page is
compared only with its own historical activity baseline.

This module performs no database access, persistence, transaction handling,
LLM calls, or external API calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import uuid


ANOMALY_TYPE_ACTIVITY_BURST = "activity_burst"
ACTIVITY_BURST_METHOD_V1 = (
    "deterministic_activity_burst_v1"
)


class AnomalyDetectionValidationError(
    ValueError
):
    pass


@dataclass(frozen=True)
class AnomalyEventInput:
    event_id: uuid.UUID
    entity_id: uuid.UUID
    monitored_page_id: uuid.UUID
    occurred_at: datetime


@dataclass(frozen=True)
class DetectedAnomaly:
    anomaly_type: str
    event_id: uuid.UUID
    entity_id: uuid.UUID
    monitored_page_id: uuid.UUID
    expected_value: float
    actual_value: float
    deviation_score: float
    significance: float
    baseline_days: int
    window_days: int
    historical_event_count: int
    current_event_count: int
    first_current_event_at: datetime
    last_current_event_at: datetime
    detection_method: str


def _clamp01(
    value: float,
) -> float:
    return max(
        0.0,
        min(
            1.0,
            float(value),
        ),
    )


def _ordered_events(
    events: list[AnomalyEventInput]
    | tuple[AnomalyEventInput, ...],
) -> tuple[AnomalyEventInput, ...]:
    return tuple(
        sorted(
            events,
            key=lambda event: (
                event.occurred_at,
                str(event.event_id),
            ),
        )
    )


def detect_activity_burst(
    *,
    historical_events: list[AnomalyEventInput]
    | tuple[AnomalyEventInput, ...],
    current_events: list[AnomalyEventInput]
    | tuple[AnomalyEventInput, ...],
    baseline_days: int = 28,
    window_days: int = 7,
    minimum_historical_events: int = 4,
    minimum_current_events: int = 3,
    minimum_ratio: float = 2.0,
) -> DetectedAnomaly | None:
    """
    Detect an activity burst for one monitored page.

    Inputs must already be scoped to one monitored page. This function
    validates that invariant so tenant/evidence adapters cannot accidentally
    mix unrelated page activity.

    The historical baseline is normalized to the size of the current window:

        expected = historical_count / baseline_days * window_days

    A burst requires both minimum evidence and a multiplicative increase over
    the page's own historical activity.
    """
    if baseline_days < 1:
        raise AnomalyDetectionValidationError(
            "baseline_days must be at least 1."
        )

    if window_days < 1:
        raise AnomalyDetectionValidationError(
            "window_days must be at least 1."
        )

    if minimum_historical_events < 1:
        raise AnomalyDetectionValidationError(
            "minimum_historical_events must be at least 1."
        )

    if minimum_current_events < 1:
        raise AnomalyDetectionValidationError(
            "minimum_current_events must be at least 1."
        )

    if minimum_ratio <= 1.0:
        raise AnomalyDetectionValidationError(
            "minimum_ratio must be greater than 1."
        )

    historical = _ordered_events(
        historical_events
    )
    current = _ordered_events(
        current_events
    )

    all_events = (
        historical
        + current
    )

    if not all_events:
        return None

    page_ids = {
        event.monitored_page_id
        for event in all_events
    }

    if len(page_ids) != 1:
        raise AnomalyDetectionValidationError(
            "activity burst evidence must belong "
            "to exactly one monitored page."
        )

    entity_ids = {
        event.entity_id
        for event in all_events
    }

    if len(entity_ids) != 1:
        raise AnomalyDetectionValidationError(
            "activity burst evidence must belong "
            "to exactly one entity."
        )

    historical_count = len(
        historical
    )

    current_count = len(
        current
    )

    if (
        historical_count
        < minimum_historical_events
    ):
        return None

    if (
        current_count
        < minimum_current_events
    ):
        return None

    historical_daily_rate = (
        historical_count
        / float(
            baseline_days
        )
    )

    expected_count = (
        historical_daily_rate
        * float(
            window_days
        )
    )

    ratio = (
        current_count
        / max(
            expected_count,
            1.0,
        )
    )

    if ratio < minimum_ratio:
        return None

    deviation_score = _clamp01(
        (
            ratio
            - 1.0
        )
        / 3.0
    )

    volume_score = _clamp01(
        current_count
        / 7.0
    )

    significance = _clamp01(
        (
            deviation_score
            * 0.60
        )
        + (
            volume_score
            * 0.40
        )
    )

    latest_event = current[-1]

    return DetectedAnomaly(
        anomaly_type=(
            ANOMALY_TYPE_ACTIVITY_BURST
        ),
        event_id=latest_event.event_id,
        entity_id=latest_event.entity_id,
        monitored_page_id=(
            latest_event.monitored_page_id
        ),
        expected_value=round(
            expected_count,
            6,
        ),
        actual_value=float(
            current_count
        ),
        deviation_score=round(
            deviation_score,
            6,
        ),
        significance=round(
            significance,
            6,
        ),
        baseline_days=baseline_days,
        window_days=window_days,
        historical_event_count=(
            historical_count
        ),
        current_event_count=(
            current_count
        ),
        first_current_event_at=(
            current[0].occurred_at
        ),
        last_current_event_at=(
            latest_event.occurred_at
        ),
        detection_method=(
            ACTIVITY_BURST_METHOD_V1
        ),
    )