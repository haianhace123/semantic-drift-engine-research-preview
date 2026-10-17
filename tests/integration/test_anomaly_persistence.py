from __future__ import annotations

from dataclasses import replace
from datetime import (
    datetime,
    timedelta,
    timezone,
)
import uuid

import pytest

from src.core.database import SessionLocal
from src.models.anomaly import Anomaly
from src.models.semantic_drift_event import SemanticDriftEvent
from src.services.anomaly_detection_service import (
    ACTIVITY_BURST_METHOD_V1,
    DetectedAnomaly,
)
from src.services.anomaly_persistence_service import (
    AnomalyPersistenceValidationError,
    build_anomaly_detection_key,
    persist_detected_anomaly,
)

from tests.integration.test_anomaly_evidence import (
    _create_entity_and_page,
    _create_event,
    _create_organization,
)


pytestmark = pytest.mark.skipif(
    not __import__("os").getenv(
        "RUN_DB_INTEGRATION_TESTS"
    ),
    reason=(
        "RUN_DB_INTEGRATION_TESTS is not enabled"
    ),
)


BASE = datetime(
    2026,
    9,
    1,
    12,
    0,
    tzinfo=timezone.utc,
)


def _detected_anomaly(
    *,
    event,
    entity,
    page,
    baseline_days: int = 28,
    window_days: int = 7,
    detection_method: str = (
        ACTIVITY_BURST_METHOD_V1
    ),
) -> DetectedAnomaly:
    return DetectedAnomaly(
        anomaly_type="activity_burst",
        event_id=event.id,
        entity_id=entity.id,
        monitored_page_id=page.id,
        expected_value=1.0,
        actual_value=4.0,
        deviation_score=1.0,
        significance=0.83,
        baseline_days=baseline_days,
        window_days=window_days,
        historical_event_count=4,
        current_event_count=4,
        first_current_event_at=(
            BASE
            - timedelta(
                days=3
            )
        ),
        last_current_event_at=(
            event.detected_at
        ),
        detection_method=(
            detection_method
        ),
    )


def test_persist_detected_anomaly_creates_row_and_marks_event():
    db = SessionLocal()

    try:
        organization = _create_organization(
            db,
            prefix="anomaly-persist",
        )

        entity, page = (
            _create_entity_and_page(
                db,
                organization=organization,
                suffix="Persist",
            )
        )

        event = _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=BASE,
            suffix="persist",
        )

        anomaly = _detected_anomaly(
            event=event,
            entity=entity,
            page=page,
        )

        result = persist_detected_anomaly(
            db,
            organization_id=organization.id,
            anomaly=anomaly,
            detected_at=BASE,
        )

        db.flush()
        db.expire_all()

        assert result.created is True
        assert (
            result.organization_id
            == organization.id
        )
        assert (
            result.anomaly_type
            == "activity_burst"
        )

        persisted = (
            db.query(
                Anomaly
            )
            .filter(
                Anomaly.id
                == result.anomaly_id
            )
            .one()
        )

        assert (
            persisted.organization_id
            == organization.id
        )
        assert (
            persisted.event_id
            == event.id
        )
        assert (
            persisted.anomaly_type
            == "activity_burst"
        )
        assert (
            persisted.detection_key
            == result.detection_key
        )
        assert (
            persisted.detection_method
            == ACTIVITY_BURST_METHOD_V1
        )
        assert (
            persisted.expected_value
            == pytest.approx(
                1.0
            )
        )
        assert (
            persisted.actual_value
            == pytest.approx(
                4.0
            )
        )
        assert (
            persisted.deviation_score
            == pytest.approx(
                1.0
            )
        )
        assert (
            persisted.significance
            == pytest.approx(
                0.83
            )
        )
        assert (
            persisted.baseline_days
            == 28
        )
        assert (
            persisted.window_days
            == 7
        )
        assert (
            persisted.detected_at
            == BASE
        )
        assert (
            persisted.created_at
            is not None
        )
        assert (
            persisted.created_at
            > persisted.detected_at
        )
        assert (
            persisted.is_confirmed
            is False
        )
        assert (
            persisted.is_dismissed
            is False
        )
        assert (
            persisted.is_archived
            is False
        )

        persisted_event = (
            db.query(
                SemanticDriftEvent
            )
            .filter(
                SemanticDriftEvent.id
                == event.id
            )
            .one()
        )

        assert (
            persisted_event.is_anomaly
            is True
        )

    finally:
        db.rollback()
        db.close()


def test_persist_detected_anomaly_is_retry_idempotent():
    db = SessionLocal()

    try:
        organization = _create_organization(
            db,
            prefix="anomaly-retry",
        )

        entity, page = (
            _create_entity_and_page(
                db,
                organization=organization,
                suffix="Retry",
            )
        )

        event = _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=BASE,
            suffix="retry",
        )

        anomaly = _detected_anomaly(
            event=event,
            entity=entity,
            page=page,
        )

        first = persist_detected_anomaly(
            db,
            organization_id=organization.id,
            anomaly=anomaly,
            detected_at=BASE,
        )

        db.flush()
        db.expire_all()

        first_created_at = (
            db.query(
                Anomaly.created_at
            )
            .filter(
                Anomaly.id
                == first.anomaly_id
            )
            .scalar()
        )

        second = persist_detected_anomaly(
            db,
            organization_id=organization.id,
            anomaly=anomaly,
            detected_at=BASE,
        )

        db.flush()
        db.expire_all()

        second_created_at = (
            db.query(
                Anomaly.created_at
            )
            .filter(
                Anomaly.id
                == second.anomaly_id
            )
            .scalar()
        )

        assert first.created is True
        assert second.created is False

        assert (
            first.anomaly_id
            == second.anomaly_id
        )

        assert (
            first.detection_key
            == second.detection_key
        )

        assert (
            first_created_at
            is not None
        )
        assert (
            second_created_at
            == first_created_at
        )

        count = (
            db.query(
                Anomaly
            )
            .filter(
                Anomaly.organization_id
                == organization.id,
                Anomaly.anomaly_type
                == "activity_burst",
                Anomaly.detection_key
                == first.detection_key,
            )
            .count()
        )

        assert count == 1

    finally:
        db.rollback()
        db.close()


def test_different_detection_method_creates_distinct_identity():
    db = SessionLocal()

    try:
        organization = _create_organization(
            db,
            prefix="anomaly-method",
        )

        entity, page = (
            _create_entity_and_page(
                db,
                organization=organization,
                suffix="Method",
            )
        )

        event = _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=BASE,
            suffix="method",
        )

        anomaly = _detected_anomaly(
            event=event,
            entity=entity,
            page=page,
        )

        first = persist_detected_anomaly(
            db,
            organization_id=organization.id,
            anomaly=anomaly,
        )

        second = persist_detected_anomaly(
            db,
            organization_id=organization.id,
            anomaly=anomaly,
            detection_method=(
                "deterministic_activity_burst_v2"
            ),
        )

        assert first.created is True
        assert second.created is True

        assert (
            first.anomaly_id
            != second.anomaly_id
        )

        assert (
            first.detection_key
            != second.detection_key
        )

    finally:
        db.rollback()
        db.close()


def test_different_window_configuration_creates_distinct_identity():
    db = SessionLocal()

    try:
        organization = _create_organization(
            db,
            prefix="anomaly-config",
        )

        entity, page = (
            _create_entity_and_page(
                db,
                organization=organization,
                suffix="Config",
            )
        )

        event = _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=BASE,
            suffix="config",
        )

        first_anomaly = _detected_anomaly(
            event=event,
            entity=entity,
            page=page,
            baseline_days=28,
            window_days=7,
        )

        second_anomaly = replace(
            first_anomaly,
            baseline_days=56,
            window_days=14,
        )

        first = persist_detected_anomaly(
            db,
            organization_id=organization.id,
            anomaly=first_anomaly,
        )

        second = persist_detected_anomaly(
            db,
            organization_id=organization.id,
            anomaly=second_anomaly,
        )

        # Both configurations must create independent persisted anomalies.
        assert first.created is True
        assert second.created is True

        assert (
            first.detection_key
            != second.detection_key
        )

        assert (
            first.anomaly_id
            != second.anomaly_id
        )

        # The deterministic identity must include the analysis-window
        # configuration.
        assert (
            build_anomaly_detection_key(
                first_anomaly
            )
            != build_anomaly_detection_key(
                second_anomaly
            )
        )

        # Flush pending INSERTs before reading the persisted rows.
        db.flush()

        first_row = (
            db.query(
                Anomaly
            )
            .filter(
                Anomaly.id
                == first.anomaly_id
            )
            .one()
        )

        second_row = (
            db.query(
                Anomaly
            )
            .filter(
                Anomaly.id
                == second.anomaly_id
            )
            .one()
        )

        # Verify the detector configuration itself is persisted,
        # not only encoded into detection_key.
        assert (
            first_row.baseline_days
            == 28
        )

        assert (
            first_row.window_days
            == 7
        )

        assert (
            second_row.baseline_days
            == 56
        )

        assert (
            second_row.window_days
            == 14
        )

        # Both persisted anomalies still point to the same deterministic
        # anchor event; their identity differs because their analysis
        # configuration differs.
        assert (
            first_row.event_id
            == event.id
        )

        assert (
            second_row.event_id
            == event.id
        )

        assert (
            first_row.detection_key
            == first.detection_key
        )

        assert (
            second_row.detection_key
            == second.detection_key
        )

    finally:
        db.rollback()
        db.close()


def test_cross_tenant_anchor_event_is_rejected():
    db = SessionLocal()

    try:
        first_org = _create_organization(
            db,
            prefix="anomaly-cross-a",
        )

        second_org = _create_organization(
            db,
            prefix="anomaly-cross-b",
        )

        entity, page = (
            _create_entity_and_page(
                db,
                organization=first_org,
                suffix="CrossTenant",
            )
        )

        event = _create_event(
            db,
            organization=first_org,
            page=page,
            detected_at=BASE,
            suffix="cross-tenant",
        )

        anomaly = _detected_anomaly(
            event=event,
            entity=entity,
            page=page,
        )

        with pytest.raises(
            AnomalyPersistenceValidationError,
            match=(
                "anchor event must belong "
                "to the target organization"
            ),
        ):
            persist_detected_anomaly(
                db,
                organization_id=second_org.id,
                anomaly=anomaly,
            )

        assert (
            db.query(
                Anomaly
            )
            .filter(
                Anomaly.organization_id
                == second_org.id
            )
            .count()
            == 0
        )

    finally:
        db.rollback()
        db.close()


def test_wrong_monitored_page_is_rejected():
    db = SessionLocal()

    try:
        organization = _create_organization(
            db,
            prefix="anomaly-wrong-page",
        )

        entity, page = (
            _create_entity_and_page(
                db,
                organization=organization,
                suffix="SourcePage",
            )
        )

        _, other_page = (
            _create_entity_and_page(
                db,
                organization=organization,
                suffix="OtherPage",
            )
        )

        event = _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=BASE,
            suffix="wrong-page",
        )

        anomaly = replace(
            _detected_anomaly(
                event=event,
                entity=entity,
                page=page,
            ),
            monitored_page_id=(
                other_page.id
            ),
        )

        with pytest.raises(
            AnomalyPersistenceValidationError,
            match=(
                "anchor event must belong "
                "to anomaly.monitored_page_id"
            ),
        ):
            persist_detected_anomaly(
                db,
                organization_id=organization.id,
                anomaly=anomaly,
            )

        assert (
            db.query(
                Anomaly
            )
            .filter(
                Anomaly.organization_id
                == organization.id
            )
            .count()
            == 0
        )

    finally:
        db.rollback()
        db.close()


def test_wrong_source_entity_is_rejected():
    db = SessionLocal()

    try:
        organization = _create_organization(
            db,
            prefix="anomaly-wrong-entity",
        )

        entity, page = (
            _create_entity_and_page(
                db,
                organization=organization,
                suffix="SourceEntity",
            )
        )

        other_entity, _ = (
            _create_entity_and_page(
                db,
                organization=organization,
                suffix="OtherEntity",
            )
        )

        event = _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=BASE,
            suffix="wrong-entity",
        )

        anomaly = replace(
            _detected_anomaly(
                event=event,
                entity=entity,
                page=page,
            ),
            entity_id=(
                other_entity.id
            ),
        )

        with pytest.raises(
            AnomalyPersistenceValidationError,
            match=(
                "source entity must match "
                "anomaly.entity_id"
            ),
        ):
            persist_detected_anomaly(
                db,
                organization_id=organization.id,
                anomaly=anomaly,
            )

        assert (
            db.query(
                Anomaly
            )
            .filter(
                Anomaly.organization_id
                == organization.id
            )
            .count()
            == 0
        )

    finally:
        db.rollback()
        db.close()