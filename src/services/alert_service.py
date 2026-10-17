"""
Deterministic Alert persistence.

This service materializes production Alert rows from persisted
RiskAssessment, Pattern and Anomaly intelligence sources.

Properties:
- organization scoped;
- deterministic SHA-256 identity;
- retry safe;
- concurrency safe through PostgreSQL ON CONFLICT;
- validates source ownership;
- validates event ownership when event context exists;
- no external delivery;
- caller owns the transaction;
- no commit or rollback is performed here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import uuid

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.models.alert import (
    ALERT_SOURCE_ANOMALY,
    ALERT_SOURCE_PATTERN,
    ALERT_SOURCE_RISK,
    ALERT_STATUS_PENDING,
    Alert,
)
from src.models.anomaly import Anomaly
from src.models.pattern import Pattern
from src.models.risk_assessment import RiskAssessment
from src.models.semantic_drift_event import (
    SemanticDriftEvent,
)


ALERT_TYPE_RISK = "risk"
ALERT_TYPE_PATTERN = "pattern"
ALERT_TYPE_ANOMALY = "anomaly"


class AlertPersistenceValidationError(
    ValueError
):
    """Raised when an Alert cannot be persisted safely."""


@dataclass(frozen=True)
class AlertCandidate:
    organization_id: uuid.UUID
    source_type: str
    source_id: uuid.UUID
    alert_type: str
    title: str
    occurred_at: datetime

    event_id: uuid.UUID | None = None
    message: str | None = None
    severity: str | None = None
    recommendation: str | None = None
    affected_entities: tuple[str, ...] = ()


@dataclass(frozen=True)
class PersistedAlertResult:
    alert_id: uuid.UUID
    organization_id: uuid.UUID
    source_type: str
    source_id: uuid.UUID
    alert_type: str
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
        raise AlertPersistenceValidationError(
            f"{field_name} must be a valid UUID."
        ) from exc


def _normalized_required_text(
    value,
    *,
    field_name: str,
) -> str:
    normalized = str(
        value
    ).strip()

    if not normalized:
        raise AlertPersistenceValidationError(
            f"{field_name} must not be empty."
        )

    return normalized


def _normalize_source_type(
    source_type: str,
) -> str:
    normalized = (
        _normalized_required_text(
            source_type,
            field_name="source_type",
        )
        .lower()
    )

    supported = {
        ALERT_SOURCE_RISK,
        ALERT_SOURCE_PATTERN,
        ALERT_SOURCE_ANOMALY,
    }

    if normalized not in supported:
        raise AlertPersistenceValidationError(
            "source_type must be one of: "
            "risk, pattern, anomaly."
        )

    return normalized


def build_alert_detection_key(
    *,
    source_type: str,
    source_id,
    alert_type: str,
) -> str:
    """
    Build the stable persistence identity for one Alert.

    Alert identity deliberately references the persisted upstream
    intelligence row rather than mutable presentation fields such
    as title, message, severity or recommendation.

    One source row may produce different alert types without
    colliding.
    """
    normalized_source_type = (
        _normalize_source_type(
            source_type
        )
    )

    source_uuid = _as_uuid(
        source_id,
        field_name="source_id",
    )

    normalized_alert_type = (
        _normalized_required_text(
            alert_type,
            field_name="alert_type",
        )
        .lower()
    )

    material = "|".join(
        (
            normalized_source_type,
            str(
                source_uuid
            ),
            normalized_alert_type,
        )
    )

    return hashlib.sha256(
        material.encode(
            "utf-8"
        )
    ).hexdigest()


def _validate_event_ownership(
    db: Session,
    *,
    organization_id: uuid.UUID,
    event_id,
) -> uuid.UUID:
    event_uuid = _as_uuid(
        event_id,
        field_name="event_id",
    )

    existing = (
        db.query(
            SemanticDriftEvent.id
        )
        .filter(
            SemanticDriftEvent.id
            == event_uuid,
            SemanticDriftEvent.organization_id
            == organization_id,
        )
        .one_or_none()
    )

    if existing is None:
        raise AlertPersistenceValidationError(
            "Alert event must belong to the "
            "target organization."
        )

    return event_uuid


def _validate_risk_source(
    db: Session,
    *,
    organization_id: uuid.UUID,
    source_id: uuid.UUID,
    event_id: uuid.UUID | None,
) -> None:
    source = (
        db.query(
            RiskAssessment.id,
            RiskAssessment.semantic_drift_event_id,
        )
        .filter(
            RiskAssessment.id
            == source_id,
            RiskAssessment.organization_id
            == organization_id,
        )
        .one_or_none()
    )

    if source is None:
        raise AlertPersistenceValidationError(
            "RiskAssessment source must belong "
            "to the target organization."
        )

    source_event_id = source[1]

    if (
        event_id is not None
        and source_event_id != event_id
    ):
        raise AlertPersistenceValidationError(
            "Alert event must match the "
            "RiskAssessment source event."
        )


def _validate_pattern_source(
    db: Session,
    *,
    organization_id: uuid.UUID,
    source_id: uuid.UUID,
) -> None:
    source = (
        db.query(
            Pattern.id
        )
        .filter(
            Pattern.id
            == source_id,
            Pattern.organization_id
            == organization_id,
        )
        .one_or_none()
    )

    if source is None:
        raise AlertPersistenceValidationError(
            "Pattern source must belong to the "
            "target organization."
        )


def _validate_anomaly_source(
    db: Session,
    *,
    organization_id: uuid.UUID,
    source_id: uuid.UUID,
    event_id: uuid.UUID | None,
) -> None:
    source = (
        db.query(
            Anomaly.id,
            Anomaly.event_id,
        )
        .filter(
            Anomaly.id
            == source_id,
            Anomaly.organization_id
            == organization_id,
        )
        .one_or_none()
    )

    if source is None:
        raise AlertPersistenceValidationError(
            "Anomaly source must belong to the "
            "target organization."
        )

    source_event_id = source[1]

    if event_id is None:
        raise AlertPersistenceValidationError(
            "Anomaly Alert must reference its "
            "anchor event."
        )

    if source_event_id != event_id:
        raise AlertPersistenceValidationError(
            "Alert event must match the "
            "Anomaly source event."
        )


def _validate_source(
    db: Session,
    *,
    organization_id: uuid.UUID,
    source_type: str,
    source_id: uuid.UUID,
    event_id: uuid.UUID | None,
) -> None:
    if source_type == ALERT_SOURCE_RISK:
        _validate_risk_source(
            db,
            organization_id=organization_id,
            source_id=source_id,
            event_id=event_id,
        )
        return

    if source_type == ALERT_SOURCE_PATTERN:
        _validate_pattern_source(
            db,
            organization_id=organization_id,
            source_id=source_id,
        )
        return

    if source_type == ALERT_SOURCE_ANOMALY:
        _validate_anomaly_source(
            db,
            organization_id=organization_id,
            source_id=source_id,
            event_id=event_id,
        )
        return

    raise AlertPersistenceValidationError(
        "Unsupported Alert source_type."
    )


def persist_alert_candidate(
    db: Session,
    *,
    candidate: AlertCandidate,
) -> PersistedAlertResult:
    """
    Persist one deterministic Alert candidate.

    The caller owns commit/rollback.

    PostgreSQL's partial unique index on
    (organization_id, alert_type, detection_key)
    makes this safe across retries and concurrent workers.
    """
    organization_id = _as_uuid(
        candidate.organization_id,
        field_name="organization_id",
    )

    source_type = (
        _normalize_source_type(
            candidate.source_type
        )
    )

    source_id = _as_uuid(
        candidate.source_id,
        field_name="source_id",
    )

    alert_type = (
        _normalized_required_text(
            candidate.alert_type,
            field_name="alert_type",
        )
        .lower()
    )

    title = _normalized_required_text(
        candidate.title,
        field_name="title",
    )

    if candidate.occurred_at is None:
        raise AlertPersistenceValidationError(
            "occurred_at must not be None."
        )

    event_id = None

    if candidate.event_id is not None:
        event_id = (
            _validate_event_ownership(
                db,
                organization_id=organization_id,
                event_id=candidate.event_id,
            )
        )

    _validate_source(
        db,
        organization_id=organization_id,
        source_type=source_type,
        source_id=source_id,
        event_id=event_id,
    )

    detection_key = (
        build_alert_detection_key(
            source_type=source_type,
            source_id=source_id,
            alert_type=alert_type,
        )
    )

    alert_id = uuid.uuid4()

    values = {
        "id":
            alert_id,
        "organization_id":
            organization_id,
        "event_id":
            event_id,
        "source_type":
            source_type,
        "source_id":
            source_id,
        "detection_key":
            detection_key,
        "occurred_at":
            candidate.occurred_at,
        "alert_type":
            alert_type,
        "title":
            title,
        "message":
            candidate.message,
        "severity":
            candidate.severity,
        "recommendation":
            candidate.recommendation,
        "affected_entities":
            list(
                candidate.affected_entities
            ),
        "sent_to":
            [],
        "status":
            ALERT_STATUS_PENDING,
    }

    statement = (
        insert(
            Alert.__table__
        )
        .values(
            **values
        )
        .on_conflict_do_nothing(
            index_elements=[
                Alert.__table__.c.organization_id,
                Alert.__table__.c.alert_type,
                Alert.__table__.c.detection_key,
            ],
            index_where=(
                Alert.__table__.c.detection_key.is_not(
                    None
                )
            ),
        )
        .returning(
            Alert.__table__.c.id
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
                Alert.id
            )
            .filter(
                Alert.organization_id
                == organization_id,
                Alert.alert_type
                == alert_type,
                Alert.detection_key
                == detection_key,
            )
            .one_or_none()
        )

        if existing is None:
            raise RuntimeError(
                "Alert conflict occurred but the "
                "persisted Alert could not be loaded."
            )

        persisted_id = (
            existing[0]
        )

    return PersistedAlertResult(
        alert_id=persisted_id,
        organization_id=organization_id,
        source_type=source_type,
        source_id=source_id,
        alert_type=alert_type,
        detection_key=detection_key,
        created=created,
    )


def persist_alert_candidates(
    db: Session,
    *,
    candidates: tuple[
        AlertCandidate,
        ...
    ]
    | list[
        AlertCandidate
    ],
) -> tuple[
    PersistedAlertResult,
    ...
]:
    """
    Persist multiple Alert candidates in deterministic input order.

    Caller owns the surrounding transaction.
    """
    return tuple(
        persist_alert_candidate(
            db,
            candidate=candidate,
        )
        for candidate in candidates
    )