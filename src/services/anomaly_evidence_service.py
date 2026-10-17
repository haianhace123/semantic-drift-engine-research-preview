"""
Tenant-scoped temporal evidence loading for Anomaly Detection v1.

This module is read-only. It loads SemanticDriftEvent evidence and partitions
it into historical baseline and current activity windows for each monitored
page.

No persistence, mutation, commit, flush, LLM, or external API calls occur here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy.orm import Session

from src.models.monitored_page import MonitoredPage
from src.models.semantic_drift_event import SemanticDriftEvent
from src.services.anomaly_detection_service import (
    AnomalyEventInput,
)


class AnomalyEvidenceValidationError(
    ValueError
):
    pass


@dataclass(frozen=True)
class PageAnomalyEvidence:
    monitored_page_id: uuid.UUID
    entity_id: uuid.UUID
    historical_events: tuple[
        AnomalyEventInput,
        ...
    ]
    current_events: tuple[
        AnomalyEventInput,
        ...
    ]


@dataclass(frozen=True)
class AnomalyEvidenceBundle:
    organization_id: uuid.UUID
    as_of: datetime
    baseline_start: datetime
    current_start: datetime
    baseline_days: int
    window_days: int
    pages: tuple[
        PageAnomalyEvidence,
        ...
    ]

    @property
    def historical_event_count(
        self,
    ) -> int:
        return sum(
            len(
                page.historical_events
            )
            for page in self.pages
        )

    @property
    def current_event_count(
        self,
    ) -> int:
        return sum(
            len(
                page.current_events
            )
            for page in self.pages
        )


def _as_uuid(
    value,
    *,
    field_name: str,
) -> uuid.UUID:
    if isinstance(
        value,
        uuid.UUID,
    ):
        return value

    try:
        return uuid.UUID(
            str(
                value
            )
        )
    except (
        TypeError,
        ValueError,
        AttributeError,
    ) as exc:
        raise AnomalyEvidenceValidationError(
            f"{field_name} must be a valid UUID."
        ) from exc


def _normalize_cursor(
    value: datetime,
) -> datetime:
    if not isinstance(
        value,
        datetime,
    ):
        raise AnomalyEvidenceValidationError(
            "as_of must be a datetime."
        )

    if value.tzinfo is None:
        return value.replace(
            tzinfo=timezone.utc
        )

    return value.astimezone(
        timezone.utc
    )


def _validate_days(
    value,
    *,
    field_name: str,
    maximum: int = 365,
) -> int:
    if (
        isinstance(
            value,
            bool,
        )
        or not isinstance(
            value,
            int,
        )
    ):
        raise AnomalyEvidenceValidationError(
            f"{field_name} must be an integer."
        )

    if value < 1:
        raise AnomalyEvidenceValidationError(
            f"{field_name} must be at least 1."
        )

    if value > maximum:
        raise AnomalyEvidenceValidationError(
            f"{field_name} must not exceed {maximum}."
        )

    return value


def load_anomaly_evidence(
    db: Session,
    *,
    organization_id,
    as_of: datetime,
    baseline_days: int = 28,
    window_days: int = 7,
) -> AnomalyEvidenceBundle:
    """
    Load activity evidence for deterministic Anomaly Detection v1.

    Historical baseline:
        [baseline_start, current_start)

    Current activity window:
        [current_start, as_of]

    Event source entity is resolved through MonitoredPage.entity_id because
    SemanticDriftEvent does not carry entity_id directly.
    """
    org_id = _as_uuid(
        organization_id,
        field_name="organization_id",
    )

    cursor = _normalize_cursor(
        as_of
    )

    normalized_baseline_days = (
        _validate_days(
            baseline_days,
            field_name="baseline_days",
        )
    )

    normalized_window_days = (
        _validate_days(
            window_days,
            field_name="window_days",
        )
    )

    current_start = (
        cursor
        - timedelta(
            days=normalized_window_days
        )
    )

    baseline_start = (
        current_start
        - timedelta(
            days=normalized_baseline_days
        )
    )

    rows = (
        db.query(
            SemanticDriftEvent.id,
            SemanticDriftEvent.monitored_page_id,
            MonitoredPage.entity_id,
            SemanticDriftEvent.detected_at,
        )
        .join(
            MonitoredPage,
            (
                SemanticDriftEvent.monitored_page_id
                == MonitoredPage.id
            ),
        )
        .filter(
            SemanticDriftEvent.organization_id
            == org_id,
            MonitoredPage.organization_id
            == org_id,
            SemanticDriftEvent.detected_at
            >= baseline_start,
            SemanticDriftEvent.detected_at
            <= cursor,
        )
        .order_by(
            SemanticDriftEvent.detected_at.asc(),
            SemanticDriftEvent.id.asc(),
        )
        .all()
    )

    grouped: dict[
        uuid.UUID,
        dict,
    ] = {}

    for (
        event_id,
        monitored_page_id,
        entity_id,
        occurred_at,
    ) in rows:
        page_id = _as_uuid(
            monitored_page_id,
            field_name="monitored_page_id",
        )

        resolved_entity_id = _as_uuid(
            entity_id,
            field_name="entity_id",
        )

        event = AnomalyEventInput(
            event_id=_as_uuid(
                event_id,
                field_name="event_id",
            ),
            entity_id=resolved_entity_id,
            monitored_page_id=page_id,
            occurred_at=occurred_at,
        )

        item = grouped.get(
            page_id
        )

        if item is None:
            item = {
                "entity_id":
                    resolved_entity_id,
                "historical_events":
                    [],
                "current_events":
                    [],
            }

            grouped[
                page_id
            ] = item

        elif (
            item["entity_id"]
            != resolved_entity_id
        ):
            raise AnomalyEvidenceValidationError(
                "one monitored page resolved "
                "to multiple entities."
            )

        if occurred_at < current_start:
            item[
                "historical_events"
            ].append(
                event
            )
        else:
            item[
                "current_events"
            ].append(
                event
            )

    pages = tuple(
        PageAnomalyEvidence(
            monitored_page_id=page_id,
            entity_id=item[
                "entity_id"
            ],
            historical_events=tuple(
                item[
                    "historical_events"
                ]
            ),
            current_events=tuple(
                item[
                    "current_events"
                ]
            ),
        )
        for (
            page_id,
            item,
        ) in sorted(
            grouped.items(),
            key=lambda pair: str(
                pair[0]
            ),
        )
    )

    return AnomalyEvidenceBundle(
        organization_id=org_id,
        as_of=cursor,
        baseline_start=baseline_start,
        current_start=current_start,
        baseline_days=(
            normalized_baseline_days
        ),
        window_days=(
            normalized_window_days
        ),
        pages=pages,
    )