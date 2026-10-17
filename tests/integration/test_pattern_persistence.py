from __future__ import annotations

from datetime import datetime, timezone
import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from src.models.entity import Entity
from src.models.monitored_page import MonitoredPage
from src.models.organization import Organization
from src.models.page_snapshot import PageSnapshot
from src.models.pattern import Pattern
from src.models.semantic_drift_event import SemanticDriftEvent
from src.services.pattern_detection_service import DetectedPattern
from src.services.pattern_persistence_service import (
    PatternPersistenceValidationError,
    build_pattern_detection_key,
    persist_detected_pattern,
    persist_detected_patterns,
)
from src.core.database import SessionLocal

pytestmark = pytest.mark.skipif(
    not __import__("os").getenv(
        "RUN_DB_INTEGRATION_TESTS"
    ),
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)

def _dt(
    day: int,
    hour: int = 0,
) -> datetime:
    return datetime(
        2026,
        8,
        day,
        hour,
        tzinfo=timezone.utc,
    )


def _slug(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _create_organization(
    db,
    *,
    prefix: str,
) -> Organization:
    organization = Organization(
        id=uuid.uuid4(),
        name=f"{prefix} organization",
        slug=_slug(prefix),
        billing_email=(
            f"{uuid.uuid4().hex[:12]}"
            "@example.com"
        ),
    )

    db.add(
        organization
    )
    db.flush()

    return organization


def _create_entity(
    db,
    *,
    organization: Organization,
    suffix: str,
) -> Entity:
    entity = Entity(
        id=uuid.uuid4(),
        organization_id=organization.id,
        name=f"Entity {suffix}",
        domain=(
            f"{suffix.lower()}-"
            f"{uuid.uuid4().hex[:8]}.example.com"
        ),
        category="company",
    )

    db.add(
        entity
    )
    db.flush()

    return entity


def _create_page(
    db,
    *,
    organization: Organization,
    entity: Entity,
    suffix: str,
) -> MonitoredPage:
    page = MonitoredPage(
        id=uuid.uuid4(),
        organization_id=organization.id,
        entity_id=entity.id,
        url=(
            "https://"
            f"{entity.domain}/"
            f"{suffix.lower()}"
        ),
        page_type="docs",
        crawl_frequency="daily",
        is_active=True,
    )

    db.add(
        page
    )
    db.flush()

    return page


def _create_snapshot(
    db,
    *,
    organization: Organization,
    page: MonitoredPage,
    suffix: str,
    crawled_at: datetime,
) -> PageSnapshot:
    content = (
        f"pattern persistence snapshot {suffix}"
    )

    snapshot = PageSnapshot(
        id=uuid.uuid4(),
        organization_id=organization.id,
        monitored_page_id=page.id,
        content_hash=uuid.uuid4().hex,
        raw_content=content,
        content_size=len(content),
        crawled_at=crawled_at,
    )

    db.add(
        snapshot
    )
    db.flush()

    return snapshot


def _create_event(
    db,
    *,
    organization: Organization,
    page: MonitoredPage,
    day: int,
    suffix: str,
) -> SemanticDriftEvent:
    old_snapshot = _create_snapshot(
        db,
        organization=organization,
        page=page,
        suffix=f"{suffix}-old",
        crawled_at=_dt(
            day,
            8,
        ),
    )

    new_snapshot = _create_snapshot(
        db,
        organization=organization,
        page=page,
        suffix=f"{suffix}-new",
        crawled_at=_dt(
            day,
            9,
        ),
    )

    event = SemanticDriftEvent(
        id=uuid.uuid4(),
        organization_id=organization.id,
        monitored_page_id=page.id,
        old_snapshot_id=old_snapshot.id,
        new_snapshot_id=new_snapshot.id,
        semantic_delta_json={
            "summary": suffix,
        },
        impact_score=50,
        entities_affected=[],
        detected_at=_dt(
            day,
            10,
        ),
        is_pattern_part=False,
    )

    db.add(
        event
    )
    db.flush()

    return event


def _detected_pattern(
    *,
    entity: Entity,
    events: list[SemanticDriftEvent],
    pattern_type: str = "repeated_drift",
    detection_method: str = (
        "deterministic_repeated_drift_v1"
    ),
) -> DetectedPattern:
    return DetectedPattern(
        pattern_type=pattern_type,
        entity_id=entity.id,
        event_ids=tuple(
            event.id
            for event in events
        ),
        first_event_at=min(
            event.detected_at
            for event in events
        ),
        last_event_at=max(
            event.detected_at
            for event in events
        ),
        event_count=len(events),
        significance_score=0.77,
        detection_method=detection_method,
    )


def test_persist_pattern_creates_row_and_marks_events() -> None:
    db = SessionLocal()

    try:
        organization = _create_organization(
            db,
            prefix="pattern-persist",
        )

        entity = _create_entity(
            db,
            organization=organization,
            suffix="alpha",
        )

        page = _create_page(
            db,
            organization=organization,
            entity=entity,
            suffix="docs",
        )

        events = [
            _create_event(
                db,
                organization=organization,
                page=page,
                day=day,
                suffix=f"event-{day}",
            )
            for day in (
                1,
                2,
                3,
            )
        ]

        pattern = _detected_pattern(
            entity=entity,
            events=events,
        )

        result = persist_detected_pattern(
            db,
            organization_id=organization.id,
            pattern=pattern,
            detected_at=_dt(
                4,
            ),
        )

        db.flush()

        assert result.created is True
        assert result.organization_id == organization.id
        assert result.pattern_type == "repeated_drift"

        stored = (
            db.query(
                Pattern
            )
            .filter(
                Pattern.id
                == result.pattern_id
            )
            .one()
        )

        assert stored.organization_id == organization.id
        assert stored.pattern_type == "repeated_drift"
        assert stored.detection_key == (
            build_pattern_detection_key(
                pattern
            )
        )
        assert stored.detection_method == (
            pattern.detection_method
        )
        assert stored.event_count == 3
        assert stored.entity_count == 1
        assert stored.event_ids == [
            str(
                event.id
            )
            for event in events
        ]
        assert stored.entities == [
            str(
                entity.id
            )
        ]
        assert stored.is_active is True
        assert stored.is_archived is False
        assert stored.detected_at == _dt(
            4,
        )
        assert stored.created_at is not None
        assert (
            stored.created_at
            > stored.detected_at
        )

        persisted_flags = (
            db.query(
                SemanticDriftEvent.id,
                SemanticDriftEvent.is_pattern_part,
            )
            .filter(
                SemanticDriftEvent.id.in_(
                    [
                        event.id
                        for event in events
                    ]
                )
            )
            .all()
        )

        assert {
            row.id: row.is_pattern_part
            for row in persisted_flags
        } == {
            event.id: True
            for event in events
        }
    finally:
        db.rollback()
        db.close()


def test_retry_is_idempotent() -> None:
    db = SessionLocal()

    try:
        organization = _create_organization(
            db,
            prefix="pattern-retry",
        )

        entity = _create_entity(
            db,
            organization=organization,
            suffix="retry",
        )

        page = _create_page(
            db,
            organization=organization,
            entity=entity,
            suffix="docs",
        )

        events = [
            _create_event(
                db,
                organization=organization,
                page=page,
                day=day,
                suffix=f"retry-{day}",
            )
            for day in (
                4,
                5,
                6,
            )
        ]

        pattern = _detected_pattern(
            entity=entity,
            events=events,
        )

        first = persist_detected_pattern(
            db,
            organization_id=organization.id,
            pattern=pattern,
        )

        db.flush()
        db.expire_all()

        first_created_at = (
            db.query(
                Pattern.created_at
            )
            .filter(
                Pattern.id
                == first.pattern_id
            )
            .scalar()
        )

        second = persist_detected_pattern(
            db,
            organization_id=organization.id,
            pattern=pattern,
        )

        db.flush()
        db.expire_all()

        second_created_at = (
            db.query(
                Pattern.created_at
            )
            .filter(
                Pattern.id
                == second.pattern_id
            )
            .scalar()
        )

        assert first.created is True
        assert second.created is False
        assert second.pattern_id == first.pattern_id
        assert second.detection_key == first.detection_key
        assert first_created_at is not None
        assert second_created_at == first_created_at

        count = (
            db.query(
                Pattern
            )
            .filter(
                Pattern.organization_id
                == organization.id,
                Pattern.pattern_type
                == pattern.pattern_type,
                Pattern.detection_key
                == first.detection_key,
            )
            .count()
        )

        assert count == 1
    finally:
        db.rollback()
        db.close()


def test_same_evidence_different_method_creates_distinct_pattern() -> None:
    db = SessionLocal()

    try:
        organization = _create_organization(
            db,
            prefix="pattern-method",
        )

        entity = _create_entity(
            db,
            organization=organization,
            suffix="method",
        )

        page = _create_page(
            db,
            organization=organization,
            entity=entity,
            suffix="docs",
        )

        events = [
            _create_event(
                db,
                organization=organization,
                page=page,
                day=day,
                suffix=f"method-{day}",
            )
            for day in (
                7,
                8,
                9,
            )
        ]

        v1 = _detected_pattern(
            entity=entity,
            events=events,
            detection_method=(
                "deterministic_repeated_drift_v1"
            ),
        )

        v2 = _detected_pattern(
            entity=entity,
            events=events,
            detection_method=(
                "deterministic_repeated_drift_v2"
            ),
        )

        first = persist_detected_pattern(
            db,
            organization_id=organization.id,
            pattern=v1,
        )

        second = persist_detected_pattern(
            db,
            organization_id=organization.id,
            pattern=v2,
        )

        db.flush()

        assert first.created is True
        assert second.created is True
        assert first.pattern_id != second.pattern_id
        assert first.detection_key != second.detection_key

    finally:
        db.rollback()
        db.close()


def test_cross_tenant_event_evidence_is_rejected() -> None:
    db = SessionLocal()

    try:
        organization_a = _create_organization(
            db,
            prefix="pattern-tenant-a",
        )

        organization_b = _create_organization(
            db,
            prefix="pattern-tenant-b",
        )

        entity_a = _create_entity(
            db,
            organization=organization_a,
            suffix="tenant-a",
        )

        entity_b = _create_entity(
            db,
            organization=organization_b,
            suffix="tenant-b",
        )

        page_a = _create_page(
            db,
            organization=organization_a,
            entity=entity_a,
            suffix="docs",
        )

        page_b = _create_page(
            db,
            organization=organization_b,
            entity=entity_b,
            suffix="docs",
        )

        event_a = _create_event(
            db,
            organization=organization_a,
            page=page_a,
            day=10,
            suffix="tenant-a",
        )

        event_b = _create_event(
            db,
            organization=organization_b,
            page=page_b,
            day=11,
            suffix="tenant-b",
        )

        pattern = DetectedPattern(
            pattern_type="repeated_drift",
            entity_id=entity_a.id,
            event_ids=(
                event_a.id,
                event_b.id,
            ),
            first_event_at=event_a.detected_at,
            last_event_at=event_b.detected_at,
            event_count=2,
            significance_score=0.70,
            detection_method=(
                "deterministic_repeated_drift_v1"
            ),
        )

        with pytest.raises(
            PatternPersistenceValidationError,
            match="target organization",
        ):
            persist_detected_pattern(
                db,
                organization_id=organization_a.id,
                pattern=pattern,
            )

        db.rollback()

        persisted = (
            db.query(
                Pattern
            )
            .filter(
                Pattern.organization_id
                == organization_a.id
            )
            .count()
        )

        assert persisted == 0

    finally:
        db.rollback()
        db.close()


def test_validation_failure_rolls_back_insert_and_event_flags() -> None:
    db = SessionLocal()

    try:
        organization_a = _create_organization(
            db,
            prefix="pattern-rollback-a",
        )

        organization_b = _create_organization(
            db,
            prefix="pattern-rollback-b",
        )

        entity_a = _create_entity(
            db,
            organization=organization_a,
            suffix="rollback-a",
        )

        entity_b = _create_entity(
            db,
            organization=organization_b,
            suffix="rollback-b",
        )

        page_a = _create_page(
            db,
            organization=organization_a,
            entity=entity_a,
            suffix="docs",
        )

        page_b = _create_page(
            db,
            organization=organization_b,
            entity=entity_b,
            suffix="docs",
        )

        valid_event = _create_event(
            db,
            organization=organization_a,
            page=page_a,
            day=12,
            suffix="valid",
        )

        foreign_event = _create_event(
            db,
            organization=organization_b,
            page=page_b,
            day=13,
            suffix="foreign",
        )

        organization_a_id = organization_a.id
        valid_event_id = valid_event.id
        foreign_event_id = foreign_event.id

        pattern = DetectedPattern(
            pattern_type="repeated_drift",
            entity_id=entity_a.id,
            event_ids=(
                valid_event_id,
                foreign_event_id,
            ),
            first_event_at=valid_event.detected_at,
            last_event_at=foreign_event.detected_at,
            event_count=2,
            significance_score=0.71,
            detection_method=(
                "deterministic_repeated_drift_v1"
            ),
        )

        with pytest.raises(
            PatternPersistenceValidationError,
        ):
            persist_detected_pattern(
                db,
                organization_id=organization_a_id,
                pattern=pattern,
            )

        db.rollback()

        assert (
            db.query(
                Pattern
            )
            .filter(
                Pattern.organization_id
                == organization_a_id
            )
            .count()
            == 0
        )

        refreshed_valid = (
            db.query(
                SemanticDriftEvent
            )
            .filter(
                SemanticDriftEvent.id
                == valid_event_id
            )
            .one_or_none()
        )

        refreshed_foreign = (
            db.query(
                SemanticDriftEvent
            )
            .filter(
                SemanticDriftEvent.id
                == foreign_event_id
            )
            .one_or_none()
        )

        if refreshed_valid is not None:
            assert refreshed_valid.is_pattern_part is not True

        if refreshed_foreign is not None:
            assert refreshed_foreign.is_pattern_part is not True

    finally:
        db.rollback()
        db.close()


def test_persist_multiple_patterns() -> None:
    db = SessionLocal()

    try:
        organization = _create_organization(
            db,
            prefix="pattern-multiple",
        )

        entity = _create_entity(
            db,
            organization=organization,
            suffix="multiple",
        )

        page = _create_page(
            db,
            organization=organization,
            entity=entity,
            suffix="docs",
        )

        events = [
            _create_event(
                db,
                organization=organization,
                page=page,
                day=day,
                suffix=f"multiple-{day}",
            )
            for day in (
                14,
                15,
                16,
                17,
            )
        ]

        first_pattern = _detected_pattern(
            entity=entity,
            events=events[:3],
        )

        second_pattern = _detected_pattern(
            entity=entity,
            events=events[1:],
            pattern_type="risk_escalation",
            detection_method=(
                "deterministic_risk_escalation_v1"
            ),
        )

        results = persist_detected_patterns(
            db,
            organization_id=organization.id,
            patterns=[
                first_pattern,
                second_pattern,
            ],
        )

        db.flush()

        assert len(results) == 2
        assert all(
            result.created
            for result in results
        )

        assert (
            db.query(
                Pattern
            )
            .filter(
                Pattern.organization_id
                == organization.id
            )
            .count()
            == 2
        )

    finally:
        db.rollback()
        db.close()


def test_database_unique_index_blocks_duplicate_identity() -> None:
    db = SessionLocal()

    try:
        organization = _create_organization(
            db,
            prefix="pattern-db-unique",
        )

        entity = _create_entity(
            db,
            organization=organization,
            suffix="unique",
        )

        page = _create_page(
            db,
            organization=organization,
            entity=entity,
            suffix="docs",
        )

        events = [
            _create_event(
                db,
                organization=organization,
                page=page,
                day=day,
                suffix=f"unique-{day}",
            )
            for day in (
                18,
                19,
                20,
            )
        ]

        pattern = _detected_pattern(
            entity=entity,
            events=events,
        )

        key = build_pattern_detection_key(
            pattern
        )

        common_values = {
            "organization_id": organization.id,
            "name": "duplicate test",
            "description": "duplicate test",
            "pattern_type": pattern.pattern_type,
            "detection_key": key,
            "detection_method": pattern.detection_method,
            "event_ids": [
                str(
                    event.id
                )
                for event in events
            ],
            "entities": [
                str(
                    entity.id
                )
            ],
            "categories": [],
            "event_count": len(events),
            "entity_count": 1,
            "time_span_days": 2,
            "significance_score": 0.77,
            "first_event_at": events[0].detected_at,
            "last_event_at": events[-1].detected_at,
            "summary": "duplicate test",
            "is_active": True,
            "is_archived": False,
        }

        db.add(
            Pattern(
                id=uuid.uuid4(),
                **common_values,
            )
        )
        db.flush()

        with pytest.raises(
            IntegrityError
        ):
            with db.begin_nested():
                db.add(
                    Pattern(
                        id=uuid.uuid4(),
                        **common_values,
                    )
                )
                db.flush()

        assert (
            db.query(
                Pattern
            )
            .filter(
                Pattern.organization_id
                == organization.id,
                Pattern.pattern_type
                == pattern.pattern_type,
                Pattern.detection_key
                == key,
            )
            .count()
            == 1
        )

    finally:
        db.rollback()
        db.close()