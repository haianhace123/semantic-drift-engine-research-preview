"""
Deterministic Anomaly v1 persistence.

Properties:
- organization scoped;
- deterministic SHA-256 identity;
- validates anchor event ownership and source identity;
- retry safe;
- concurrency safe through PostgreSQL ON CONFLICT;
- marks the anchor SemanticDriftEvent as anomalous;
- caller owns the transaction;
- no commit or rollback is performed here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import uuid

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.models.anomaly import Anomaly
from src.models.monitored_page import MonitoredPage
from src.models.semantic_drift_event import SemanticDriftEvent
from src.services.anomaly_detection_service import (
    DetectedAnomaly,
)


class AnomalyPersistenceValidationError(
    ValueError
):
    """Raised when a detected anomaly cannot be persisted safely."""


@dataclass(frozen=True)
class PersistedAnomalyResult:
    anomaly_id: uuid.UUID
    organization_id: uuid.UUID
    anomaly_type: str
    detection_key: str
    created: bool


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
        raise AnomalyPersistenceValidationError(
            f"{field_name} must be a valid UUID."
        ) from exc


def _resolve_detection_method(
    anomaly: DetectedAnomaly,
    detection_method: str | None,
) -> str:
    if detection_method is None:
        method = str(
            anomaly.detection_method
        ).strip()
    else:
        method = str(
            detection_method
        ).strip()

    if not method:
        raise AnomalyPersistenceValidationError(
            "detection_method must not be empty."
        )

    return method


def build_anomaly_detection_key(
    anomaly: DetectedAnomaly,
    *,
    detection_method: str | None = None,
) -> str:
    """
    Build stable persistence identity for one detected anomaly.

    Identity includes:
    - anomaly type;
    - source entity;
    - monitored page;
    - deterministic anchor event;
    - baseline/window configuration;
    - detector provenance.

    The analysis-window configuration is part of identity because changing it
    can change expected_value, deviation_score and significance even when the
    anchor event is unchanged.
    """
    event_id = _as_uuid(
        anomaly.event_id,
        field_name="anomaly.event_id",
    )

    entity_id = _as_uuid(
        anomaly.entity_id,
        field_name="anomaly.entity_id",
    )

    page_id = _as_uuid(
        anomaly.monitored_page_id,
        field_name="anomaly.monitored_page_id",
    )

    method = _resolve_detection_method(
        anomaly,
        detection_method,
    )

    baseline_days = int(
        anomaly.baseline_days
    )

    window_days = int(
        anomaly.window_days
    )

    if baseline_days < 1:
        raise AnomalyPersistenceValidationError(
            "anomaly.baseline_days must be at least 1."
        )

    if window_days < 1:
        raise AnomalyPersistenceValidationError(
            "anomaly.window_days must be at least 1."
        )

    material = "|".join(
        (
            str(
                anomaly.anomaly_type
            ),
            str(
                entity_id
            ),
            str(
                page_id
            ),
            str(
                event_id
            ),
            str(
                baseline_days
            ),
            str(
                window_days
            ),
            method,
        )
    )

    return hashlib.sha256(
        material.encode(
            "utf-8"
        )
    ).hexdigest()


def _description(
    anomaly: DetectedAnomaly,
) -> str:
    if (
        anomaly.anomaly_type
        == "activity_burst"
    ):
        return (
            "Activity increased materially above this "
            "monitored page's own historical baseline."
        )

    return (
        "A deterministic temporal anomaly was detected."
    )


def _validate_anchor_event(
    db: Session,
    *,
    organization_id: uuid.UUID,
    anomaly: DetectedAnomaly,
) -> uuid.UUID:
    event_id = _as_uuid(
        anomaly.event_id,
        field_name="anomaly.event_id",
    )

    page_id = _as_uuid(
        anomaly.monitored_page_id,
        field_name="anomaly.monitored_page_id",
    )

    entity_id = _as_uuid(
        anomaly.entity_id,
        field_name="anomaly.entity_id",
    )

    row = (
        db.query(
            SemanticDriftEvent.id,
            SemanticDriftEvent.monitored_page_id,
            MonitoredPage.entity_id,
        )
        .join(
            MonitoredPage,
            (
                SemanticDriftEvent.monitored_page_id
                == MonitoredPage.id
            ),
        )
        .filter(
            SemanticDriftEvent.id
            == event_id,
            SemanticDriftEvent.organization_id
            == organization_id,
            MonitoredPage.organization_id
            == organization_id,
        )
        .one_or_none()
    )

    if row is None:
        raise AnomalyPersistenceValidationError(
            "Anomaly anchor event must belong "
            "to the target organization."
        )

    if row[1] != page_id:
        raise AnomalyPersistenceValidationError(
            "Anomaly anchor event must belong "
            "to anomaly.monitored_page_id."
        )

    if row[2] != entity_id:
        raise AnomalyPersistenceValidationError(
            "Anomaly anchor event source entity must "
            "match anomaly.entity_id."
        )

    return event_id


def persist_detected_anomaly(
    db: Session,
    *,
    organization_id,
    anomaly: DetectedAnomaly,
    detected_at: datetime | None = None,
    detection_method: str | None = None,
) -> PersistedAnomalyResult:
    """
    Persist one deterministic anomaly.

    Caller owns commit/rollback.
    """
    organization_uuid = _as_uuid(
        organization_id,
        field_name="organization_id",
    )

    method = _resolve_detection_method(
        anomaly,
        detection_method,
    )

    event_id = _validate_anchor_event(
        db,
        organization_id=organization_uuid,
        anomaly=anomaly,
    )

    detection_key = (
        build_anomaly_detection_key(
            anomaly,
            detection_method=method,
        )
    )

    anomaly_id = uuid.uuid4()

    values = {
        "id": anomaly_id,
        "organization_id":
            organization_uuid,
        "event_id": event_id,
        "anomaly_type":
            anomaly.anomaly_type,
        "detection_key":
            detection_key,
        "description":
            _description(
                anomaly
            ),
        "expected_value":
            float(
                anomaly.expected_value
            ),
        "actual_value":
            float(
                anomaly.actual_value
            ),
        "deviation_score":
            float(
                anomaly.deviation_score
            ),
        "significance":
            float(
                anomaly.significance
            ),
        "baseline_days":
            int(
                anomaly.baseline_days
            ),
        "window_days":
            int(
                anomaly.window_days
            ),
        "detection_method":
            method,
        "is_confirmed":
            False,
        "is_dismissed":
            False,
        "is_archived":
            False,
    }

    if detected_at is not None:
        values[
            "detected_at"
        ] = detected_at

    statement = (
        insert(
            Anomaly.__table__
        )
        .values(
            **values
        )
        .on_conflict_do_nothing(
            index_elements=[
                Anomaly.__table__.c.organization_id,
                Anomaly.__table__.c.anomaly_type,
                Anomaly.__table__.c.detection_key,
            ],
            index_where=(
                Anomaly.__table__.c.detection_key.is_not(
                    None
                )
            ),
        )
        .returning(
            Anomaly.__table__.c.id
        )
    )

    inserted_id = db.execute(
        statement
    ).scalar_one_or_none()

    created = (
        inserted_id
        is not None
    )

    if created:
        persisted_id = (
            inserted_id
        )
    else:
        existing = (
            db.query(
                Anomaly.id
            )
            .filter(
                Anomaly.organization_id
                == organization_uuid,
                Anomaly.anomaly_type
                == anomaly.anomaly_type,
                Anomaly.detection_key
                == detection_key,
            )
            .one_or_none()
        )

        if existing is None:
            raise RuntimeError(
                "Anomaly conflict occurred but the persisted "
                "anomaly could not be loaded."
            )

        persisted_id = (
            existing[0]
        )

    db.execute(
        update(
            SemanticDriftEvent
        )
        .where(
            SemanticDriftEvent.id
            == event_id,
            SemanticDriftEvent.organization_id
            == organization_uuid,
        )
        .values(
            is_anomaly=True
        )
    )

    return PersistedAnomalyResult(
        anomaly_id=persisted_id,
        organization_id=organization_uuid,
        anomaly_type=(
            anomaly.anomaly_type
        ),
        detection_key=detection_key,
        created=created,
    )


def persist_detected_anomalies(
    db: Session,
    *,
    organization_id,
    anomalies: tuple[
        DetectedAnomaly,
        ...
    ]
    | list[
        DetectedAnomaly
    ],
    detected_at: datetime | None = None,
    detection_method: str | None = None,
) -> tuple[
    PersistedAnomalyResult,
    ...
]:
    """
    Persist multiple anomalies in deterministic input order.

    Caller owns the surrounding transaction.
    """
    return tuple(
        persist_detected_anomaly(
            db,
            organization_id=organization_id,
            anomaly=anomaly,
            detected_at=detected_at,
            detection_method=(
                detection_method
            ),
        )
        for anomaly in anomalies
    )