"""
Deterministic Pattern v1 persistence.

This service persists pure DetectedPattern results into PostgreSQL.

Properties:
- organization scoped;
- deterministic SHA-256 identity;
- retry safe;
- concurrency safe through PostgreSQL ON CONFLICT;
- participating drift events are marked as pattern members;
- caller owns the transaction;
- no commit is performed here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import uuid

from sqlalchemy import update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.models.pattern import Pattern
from src.models.semantic_drift_event import SemanticDriftEvent
from src.services.pattern_detection_service import DetectedPattern


class PatternPersistenceValidationError(ValueError):
    """Raised when a detected pattern cannot be persisted safely."""


@dataclass(frozen=True)
class PersistedPatternResult:
    pattern_id: uuid.UUID
    organization_id: uuid.UUID
    pattern_type: str
    detection_key: str
    created: bool


def _as_uuid(
    value,
    *,
    field_name: str,
) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value

    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise PatternPersistenceValidationError(
            f"{field_name} must be a valid UUID."
        ) from exc


def build_pattern_detection_key(
    pattern: DetectedPattern,
    *,
    detection_method: str | None = None,
) -> str:
    """
    Build the stable persistence identity for one detected pattern.

    DetectedPattern already provides event_ids in deterministic temporal
    order. That order is deliberately preserved because the evidence
    sequence is part of the pattern identity.
    """
    method = _resolve_detection_method(
        pattern,
        detection_method,
    )

    entity_id = _as_uuid(
        pattern.entity_id,
        field_name="pattern.entity_id",
    )

    if not pattern.event_ids:
        raise PatternPersistenceValidationError(
            "pattern.event_ids must not be empty."
        )

    event_ids = tuple(
        _as_uuid(
            event_id,
            field_name="pattern.event_ids",
        )
        for event_id in pattern.event_ids
    )

    material = "|".join(
        (
            str(pattern.pattern_type),
            str(entity_id),
            ",".join(
                str(event_id)
                for event_id in event_ids
            ),
            method,
        )
    )

    return hashlib.sha256(
        material.encode("utf-8")
    ).hexdigest()


def _pattern_name(
    pattern: DetectedPattern,
) -> str:
    labels = {
        "repeated_drift": "Repeated semantic drift",
        "risk_escalation": "Risk escalation",
        "topology_sequence": "Topology shift sequence",
    }

    return labels.get(
        pattern.pattern_type,
        "Detected temporal pattern",
    )


def _pattern_description(
    pattern: DetectedPattern,
) -> str:
    descriptions = {
        "repeated_drift": (
            "Repeated semantic drift was detected for the same entity "
            "within the analysis window."
        ),
        "risk_escalation": (
            "A consecutive increase in deterministic Risk v2 assessments "
            "was detected for the same entity."
        ),
        "topology_sequence": (
            "A consecutive same-direction topology shift sequence was "
            "detected for the same entity."
        ),
    }

    return descriptions.get(
        pattern.pattern_type,
        "A deterministic temporal pattern was detected.",
    )


def _time_span_days(
    pattern: DetectedPattern,
) -> int:
    delta = (
        pattern.last_event_at
        - pattern.first_event_at
    )

    return max(
        0,
        delta.days,
    )


def persist_detected_pattern(
    db: Session,
    *,
    organization_id,
    pattern: DetectedPattern,
    detected_at: datetime | None = None,
    detection_method: str | None = None,
) -> PersistedPatternResult:
    """
    Persist one deterministic pattern.

    The caller owns commit/rollback.

    PostgreSQL's partial unique index on
    (organization_id, pattern_type, detection_key)
    makes this safe across retries and concurrent workers.
    """
    organization_uuid = _as_uuid(
        organization_id,
        field_name="organization_id",
    )

    entity_uuid = _as_uuid(
        pattern.entity_id,
        field_name="pattern.entity_id",
    )

    event_ids = tuple(
        _as_uuid(
            event_id,
            field_name="pattern.event_ids",
        )
        for event_id in pattern.event_ids
    )

    if not event_ids:
        raise PatternPersistenceValidationError(
            "pattern.event_ids must not be empty."
        )

    if pattern.event_count != len(event_ids):
        raise PatternPersistenceValidationError(
            "pattern.event_count must match pattern.event_ids."
        )

    if pattern.last_event_at < pattern.first_event_at:
        raise PatternPersistenceValidationError(
            "pattern.last_event_at must not precede first_event_at."
        )

    method = _resolve_detection_method(
        pattern,
        detection_method,
    )

    detection_key = build_pattern_detection_key(
        pattern,
        detection_method=method,
    )

    pattern_id = uuid.uuid4()

    values = {
        "id": pattern_id,
        "organization_id": organization_uuid,
        "name": _pattern_name(pattern),
        "description": _pattern_description(pattern),
        "pattern_type": pattern.pattern_type,
        "detection_key": detection_key,
        "detection_method": method,
        "event_ids": [
            str(event_id)
            for event_id in event_ids
        ],
        "entities": [
            str(entity_uuid)
        ],
        "categories": [],
        "event_count": len(event_ids),
        "entity_count": 1,
        "time_span_days": _time_span_days(pattern),
        "significance_score": float(
            pattern.significance_score
        ),
        "first_event_at": pattern.first_event_at,
        "last_event_at": pattern.last_event_at,
        "summary": _pattern_description(pattern),
        "is_active": True,
        "is_archived": False,
    }

    if detected_at is not None:
        values["detected_at"] = detected_at

    statement = (
        insert(Pattern.__table__)
        .values(**values)
        .on_conflict_do_nothing(
            index_elements=[
                Pattern.__table__.c.organization_id,
                Pattern.__table__.c.pattern_type,
                Pattern.__table__.c.detection_key,
            ],
            index_where=(
                Pattern.__table__.c.detection_key.is_not(
                    None
                )
            ),
        )
        .returning(
            Pattern.__table__.c.id
        )
    )

    inserted_id = db.execute(
        statement
    ).scalar_one_or_none()

    created = inserted_id is not None

    if created:
        persisted_id = inserted_id
    else:
        existing = (
            db.query(
                Pattern.id
            )
            .filter(
                Pattern.organization_id
                == organization_uuid,
                Pattern.pattern_type
                == pattern.pattern_type,
                Pattern.detection_key
                == detection_key,
            )
            .one_or_none()
        )

        if existing is None:
            raise RuntimeError(
                "Pattern conflict occurred but the persisted "
                "pattern could not be loaded."
            )

        persisted_id = existing[0]

    matched_event_ids = {
        row[0]
        for row in (
            db.query(
                SemanticDriftEvent.id
            )
            .filter(
                SemanticDriftEvent.organization_id
                == organization_uuid,
                SemanticDriftEvent.id.in_(
                    event_ids
                ),
            )
            .all()
        )
    }

    if matched_event_ids != set(event_ids):
        raise PatternPersistenceValidationError(
            "All pattern events must belong to the target organization."
        )

    db.execute(
        update(
            SemanticDriftEvent
        )
        .where(
            SemanticDriftEvent.organization_id
            == organization_uuid,
            SemanticDriftEvent.id.in_(
                event_ids
            ),
        )
        .values(
            is_pattern_part=True
        )
    )

    return PersistedPatternResult(
        pattern_id=persisted_id,
        organization_id=organization_uuid,
        pattern_type=pattern.pattern_type,
        detection_key=detection_key,
        created=created,
    )


def persist_detected_patterns(
    db: Session,
    *,
    organization_id,
    patterns: tuple[DetectedPattern, ...]
    | list[DetectedPattern],
    detected_at: datetime | None = None,
    detection_method: str | None = None,
) -> tuple[PersistedPatternResult, ...]:
    """
    Persist multiple patterns in deterministic input order.

    Caller owns the surrounding transaction.
    """
    return tuple(
        persist_detected_pattern(
            db,
            organization_id=organization_id,
            pattern=pattern,
            detected_at=detected_at,
            detection_method=detection_method,
        )
        for pattern in patterns
    )

def _resolve_detection_method(
    pattern: DetectedPattern,
    detection_method: str | None,
) -> str:
    if detection_method is None:
        method = str(
            pattern.detection_method
        ).strip()
    else:
        method = str(
            detection_method
        ).strip()

    if not method:
        raise PatternPersistenceValidationError(
            "detection_method must not be empty."
        )

    return method