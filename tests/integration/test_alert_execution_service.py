from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import delete, event

from src.core.database import SessionLocal
from src.models.alert import Alert
from src.models.alert_delivery import AlertDelivery
from src.models.anomaly import Anomaly
from src.models.entity import Entity
from src.models.monitored_page import MonitoredPage
from src.models.organization import Organization
from src.models.page_snapshot import PageSnapshot
from src.models.webhook_subscription import WebhookSubscription
from src.models.pattern import Pattern
from src.models.semantic_drift_event import SemanticDriftEvent
from src.models.watchlist import Watchlist
from src.models.pipeline_checkpoint import (
    PIPELINE_ALERT_ANOMALY_MATERIALIZATION_V1,
    PIPELINE_ALERT_DELIVERY_RECONCILIATION_V1,
    PIPELINE_ALERT_GENERATION_V1,
    PIPELINE_ALERT_PATTERN_MATERIALIZATION_V1,
    PipelineCheckpoint,
)
from src.models.risk_assessment import RiskAssessment
from src.services.alert_execution_service import (
    _load_evidence_maps,
    _load_materialized_anomalies,
    _load_materialized_patterns,
    execute_alert_generation,
)
from src.services.alert_policy_revision_service import (
    bump_alert_policy_revision,
    load_alert_policy_revision,
)
from src.services.pipeline_checkpoint_service import (
    persist_pipeline_checkpoint,
)
from src.services.pipeline_source_discovery_service import (
    discover_alert_reconciliation_source,
    discover_pipeline_source,
)
from src.services.risk_assessment_service import (
    RISK_VERSION_V2,
)


UTC = timezone.utc


def _make_organization(db):
    suffix = uuid.uuid4().hex[:10]

    organization = Organization(
        id=uuid.uuid4(),
        name=f"alert-execution-{suffix}",
        slug=f"alert-execution-{suffix}",
        billing_email=f"{suffix}@example.com",
    )

    db.add(
        organization
    )
    db.flush()

    return organization


def _make_risk_assessment(
    db,
    *,
    organization_id,
    assessed_at,
    created_at,
    score,
    assessment_id=None,
):
    assessment = RiskAssessment(
        id=assessment_id or uuid.uuid4(),
        organization_id=organization_id,
        semantic_drift_event_id=None,
        risk_version=RISK_VERSION_V2,
        assessed_at=assessed_at,
        score=score,
        semantic_magnitude_contribution=0.10,
        legacy_impact_contribution=0.10,
        semantic_severity_contribution=0.10,
        page_context_contribution=0.10,
        confidence_contribution=0.10,
        topology_score=0.10,
        topology_contribution=0.10,
        topology_shift_component=0.10,
        topology_centrality_component=0.10,
        topology_downstream_component=0.10,
        has_topology_signal=True,
        has_previous_topology_state=True,
        created_at=created_at,
    )

    db.add(
        assessment
    )
    db.flush()

    return assessment


def _make_pattern(
    db,
    *,
    organization_id,
    entity_id,
    detected_at,
    created_at=None,
    pattern_id=None,
):
    pattern = Pattern(
        id=pattern_id or uuid.uuid4(),
        organization_id=organization_id,
        name="Repeated pricing drift",
        description="Repeated pricing changes detected.",
        pattern_type="recurring_change",
        detection_key=uuid.uuid4().hex,
        detection_method="integration-test",
        event_ids=[],
        entities=[
            str(
                entity_id
            )
        ],
        categories=[],
        event_count=3,
        entity_count=1,
        time_span_days=5,
        significance_score=0.85,
        first_event_at=(
            detected_at
            - timedelta(days=5)
        ),
        last_event_at=(
            detected_at
            - timedelta(hours=1)
        ),
        summary="Repeated pricing drift detected.",
        detected_at=detected_at,
        created_at=created_at,
        is_active=True,
        is_archived=False,
    )

    db.add(
        pattern
    )
    db.flush()

    return pattern

def _make_entity_and_page(
    db,
    *,
    organization_id,
    suffix,
):
    entity = Entity(
        organization_id=organization_id,
        name=f"Alert execution {suffix}",
        domain=(
            f"{suffix.lower()}-"
            f"{uuid.uuid4().hex}.example.com"
        ),
        category="technology",
    )

    db.add(
        entity
    )
    db.flush()

    page = MonitoredPage(
        organization_id=organization_id,
        entity_id=entity.id,
        url=f"https://{entity.domain}/docs",
        page_type="docs",
        crawl_frequency="daily",
        is_active=True,
    )

    db.add(
        page
    )
    db.flush()

    return (
        entity,
        page,
    )


def _make_event(
    db,
    *,
    organization_id,
    page,
    detected_at,
    suffix,
):
    old_snapshot = PageSnapshot(
        organization_id=organization_id,
        monitored_page_id=page.id,
        content_hash=(
            f"alert-old-{suffix}-"
            f"{uuid.uuid4().hex}"
        ),
        raw_content=f"old {suffix}",
        content_size=len(
            f"old {suffix}"
        ),
        crawled_at=(
            detected_at
            - timedelta(minutes=1)
        ),
    )

    new_snapshot = PageSnapshot(
        organization_id=organization_id,
        monitored_page_id=page.id,
        content_hash=(
            f"alert-new-{suffix}-"
            f"{uuid.uuid4().hex}"
        ),
        raw_content=f"new {suffix}",
        content_size=len(
            f"new {suffix}"
        ),
        crawled_at=detected_at,
    )

    db.add_all(
        [
            old_snapshot,
            new_snapshot,
        ]
    )
    db.flush()

    event = SemanticDriftEvent(
        organization_id=organization_id,
        monitored_page_id=page.id,
        old_snapshot_id=old_snapshot.id,
        new_snapshot_id=new_snapshot.id,
        semantic_delta_json={
            "test": suffix,
        },
        impact_score=50,
        detected_at=detected_at,
        analysis_status="completed",
        vector_analysis_status="completed",
    )

    db.add(
        event
    )
    db.flush()

    return event


def _make_anomaly(
    db,
    *,
    organization_id,
    event,
    detected_at,
    created_at=None,
):
    anomaly = Anomaly(
        organization_id=organization_id,
        event_id=event.id,
        anomaly_type="activity_burst",
        detection_key=uuid.uuid4().hex,
        description=(
            "Activity exceeded its historical baseline."
        ),
        expected_value=1.0,
        actual_value=5.0,
        deviation_score=0.90,
        significance=0.95,
        baseline_days=28,
        window_days=7,
        detection_method="integration-test",
        detected_at=detected_at,
        created_at=created_at,
        is_confirmed=False,
        is_dismissed=False,
        is_archived=False,
    )

    db.add(
        anomaly
    )
    db.flush()

    return anomaly


def _make_watchlist(
    db,
    *,
    organization_id,
    entity_ids=None,
    page_ids=None,
    categories=None,
    alert_on_anomaly=True,
):
    watchlist = Watchlist(
        organization_id=organization_id,
        name=(
            f"Alert execution "
            f"{uuid.uuid4().hex[:10]}"
        ),
        entity_ids=entity_ids or [],
        page_ids=page_ids or [],
        categories=categories or [],
        alert_on_high_risk=True,
        alert_on_medium_risk=False,
        alert_on_pattern=True,
        alert_on_anomaly=alert_on_anomaly,
        is_active=True,
        is_archived=False,
    )

    db.add(
        watchlist
    )
    db.flush()

    return watchlist

def _make_webhook(
    db,
    *,
    organization_id,
    events,
    is_active=True,
):
    subscription = WebhookSubscription(
        id=uuid.uuid4(),
        organization_id=organization_id,
        url="https://example.test/webhook",
        events=events,
        secret_ciphertext=b"integration-test-ciphertext",
        secret_nonce=b"0123456789ab",
        secret_encryption_version=1,
        is_active=is_active,
    )

    db.add(
        subscription
    )
    db.flush()

    return subscription

def _cleanup(
    db,
    organization_id,
):
    if organization_id is None:
        return

    db.rollback()

    db.execute(
        delete(
            AlertDelivery
        ).where(
            AlertDelivery.organization_id
            == organization_id
        )
    )

    db.execute(
        delete(
            WebhookSubscription
        ).where(
            WebhookSubscription.organization_id
            == organization_id
        )
    )

    db.execute(
        delete(
            Alert
        ).where(
            Alert.organization_id
            == organization_id
        )
    )

    db.execute(
        delete(
            PipelineCheckpoint
        ).where(
            PipelineCheckpoint.organization_id
            == organization_id
        )
    )

    db.execute(
        delete(
            Anomaly
        ).where(
            Anomaly.organization_id
            == organization_id
        )
    )

    db.execute(
        delete(
            Watchlist
        ).where(
            Watchlist.organization_id
            == organization_id
        )
    )

    db.execute(
        delete(
            Pattern
        ).where(
            Pattern.organization_id
            == organization_id
        )
    )

    db.execute(
        delete(
            RiskAssessment
        ).where(
            RiskAssessment.organization_id
            == organization_id
        )
    )

    db.execute(
        delete(
            SemanticDriftEvent
        ).where(
            SemanticDriftEvent.organization_id
            == organization_id
        )
    )

    db.execute(
        delete(
            PageSnapshot
        ).where(
            PageSnapshot.organization_id
            == organization_id
        )
    )

    db.execute(
        delete(
            MonitoredPage
        ).where(
            MonitoredPage.organization_id
            == organization_id
        )
    )

    db.execute(
        delete(
            Entity
        ).where(
            Entity.organization_id
            == organization_id
        )
    )

    db.execute(
        delete(
            Organization
        ).where(
            Organization.id
            == organization_id
        )
    )

    db.commit()


def _alerts_for_organization(
    db,
    *,
    organization_id,
):
    return (
        db.query(
            Alert
        )
        .filter(
            Alert.organization_id
            == organization_id
        )
        .order_by(
            Alert.created_at.asc(),
            Alert.id.asc(),
        )
        .all()
    )


def _checkpoint_for_organization(
    db,
    *,
    organization_id,
    pipeline_name=PIPELINE_ALERT_GENERATION_V1,
):
    return (
        db.query(
            PipelineCheckpoint
        )
        .filter(
            PipelineCheckpoint.organization_id
            == organization_id,
            PipelineCheckpoint.pipeline_name
            == pipeline_name,
        )
        .one_or_none()
    )


def test_alert_execution_consumes_complete_pending_risk_range():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            db
        )
        organization_id = organization.id

        base = datetime(
            2026,
            9,
            1,
            0,
            0,
            tzinfo=UTC,
        )

        first = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=base + timedelta(days=1),
            created_at=base + timedelta(days=10),
            score=0.70,
        )

        second = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=base + timedelta(days=2),
            created_at=base + timedelta(days=11),
            score=0.90,
        )

        third = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=base + timedelta(days=3),
            created_at=base + timedelta(days=12),
            score=0.75,
        )

        db.commit()

        source = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert source is not None
        assert (
            source.source_record_id
            == third.id
        )

        result = execute_alert_generation(
            db,
            source=source,
        )

        db.commit()

        assert result.risk_source_count == 3
        assert result.risk_candidate_count == 3
        assert result.alert_created_count == 3
        assert result.alert_existing_count == 0
        assert result.checkpoint.advanced is True

        alerts = _alerts_for_organization(
            db,
            organization_id=organization.id,
        )

        assert len(alerts) == 3

        source_ids = {
            alert.source_id
            for alert in alerts
        }

        assert source_ids == {
            first.id,
            second.id,
            third.id,
        }

        checkpoint = _checkpoint_for_organization(
            db,
            organization_id=organization.id,
        )

        assert checkpoint is not None
        assert (
            checkpoint.source_created_at
            == third.created_at
        )
        assert (
            checkpoint.source_record_id
            == third.id
        )

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_alert_execution_consumes_only_rows_after_existing_checkpoint():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            db
        )
        organization_id = organization.id

        base = datetime(
            2026,
            9,
            2,
            0,
            0,
            tzinfo=UTC,
        )

        already_processed = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=base + timedelta(days=1),
            created_at=base + timedelta(days=5),
            score=0.70,
        )

        pending_one = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=base + timedelta(days=2),
            created_at=base + timedelta(days=6),
            score=0.70,
        )

        pending_two = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=base + timedelta(days=3),
            created_at=base + timedelta(days=7),
            score=0.90,
        )

        db.commit()

        persist_pipeline_checkpoint(
            db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
            source_created_at=(
                already_processed.created_at
            ),
            source_record_id=(
                already_processed.id
            ),
            knowledge_cursor=(
                already_processed.assessed_at
            ),
            metadata_json={
                "test": "existing",
            },
        )

        db.commit()

        source = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert source is not None
        assert (
            source.source_record_id
            == pending_two.id
        )

        result = execute_alert_generation(
            db,
            source=source,
        )

        db.commit()

        assert result.risk_source_count == 2
        assert result.risk_candidate_count == 2
        assert result.alert_created_count == 2

        alerts = _alerts_for_organization(
            db,
            organization_id=organization.id,
        )

        assert {
            alert.source_id
            for alert in alerts
        } == {
            pending_one.id,
            pending_two.id,
        }

        assert (
            already_processed.id
            not in {
                alert.source_id
                for alert in alerts
            }
        )

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_alert_execution_uses_uuid_tiebreaker_for_same_created_at():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            db
        )
        organization_id = organization.id

        created_at = datetime(
            2026,
            9,
            10,
            1,
            0,
            tzinfo=UTC,
        )

        low_id = uuid.UUID(
            "00000000-0000-0000-0000-000000000001"
        )

        middle_id = uuid.UUID(
            "77777777-7777-7777-7777-777777777777"
        )

        high_id = uuid.UUID(
            "ffffffff-ffff-ffff-ffff-ffffffffffff"
        )

        low = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessment_id=low_id,
            assessed_at=created_at - timedelta(hours=3),
            created_at=created_at,
            score=0.70,
        )

        middle = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessment_id=middle_id,
            assessed_at=created_at - timedelta(hours=2),
            created_at=created_at,
            score=0.70,
        )

        high = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessment_id=high_id,
            assessed_at=created_at - timedelta(hours=1),
            created_at=created_at,
            score=0.90,
        )

        db.commit()

        persist_pipeline_checkpoint(
            db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
            source_created_at=created_at,
            source_record_id=low.id,
            knowledge_cursor=low.assessed_at,
        )

        db.commit()

        source = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert source is not None
        assert source.source_record_id == high.id

        result = execute_alert_generation(
            db,
            source=source,
        )

        db.commit()

        assert result.risk_source_count == 2

        alerts = _alerts_for_organization(
            db,
            organization_id=organization.id,
        )

        assert {
            alert.source_id
            for alert in alerts
        } == {
            middle.id,
            high.id,
        }

        assert low.id not in {
            alert.source_id
            for alert in alerts
        }

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_pattern_materialization_is_not_reprocessed_across_new_risk_watermarks(
    monkeypatch,
):
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            db
        )
        organization_id = organization.id

        base = datetime(
            2026,
            9,
            1,
            0,
            0,
            tzinfo=UTC,
        )

        entity_id = uuid.uuid4()

        pattern = _make_pattern(
            db,
            organization_id=organization.id,
            entity_id=entity_id,
            detected_at=base + timedelta(days=5),
        )

        first_risk = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=base + timedelta(days=6),
            created_at=base + timedelta(days=10),
            score=0.20,
        )

        db.commit()

        first_source = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert first_source is not None

        first_result = execute_alert_generation(
            db,
            source=first_source,
        )

        db.commit()

        assert first_result.risk_candidate_count == 0
        assert first_result.pattern_source_count == 1
        assert first_result.pattern_candidate_count == 1
        assert first_result.alert_created_count == 1

        pattern_checkpoint = (
            _checkpoint_for_organization(
                db,
                organization_id=organization.id,
                pipeline_name=(
                    PIPELINE_ALERT_PATTERN_MATERIALIZATION_V1
                ),
            )
        )

        assert pattern_checkpoint is not None
        assert (
            pattern_checkpoint.source_created_at
            == pattern.created_at
        )
        assert (
            pattern_checkpoint.source_record_id
            == pattern.id
        )
        assert (
            pattern_checkpoint.knowledge_cursor
            == first_source.temporal_cursor
        )
        assert pattern_checkpoint.metadata_json == {
            "source_count": 1,
        }

        pattern_checkpoint_id = (
            pattern_checkpoint.id
        )
        pattern_checkpoint_processed_at = (
            pattern_checkpoint.processed_at
        )

        first_alerts = _alerts_for_organization(
            db,
            organization_id=organization.id,
        )

        assert len(first_alerts) == 1
        assert (
            first_alerts[0].source_id
            == pattern.id
        )

        second_risk = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=base + timedelta(days=7),
            created_at=base + timedelta(days=11),
            score=0.20,
        )

        db.commit()

        second_source = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert second_source is not None
        assert (
            second_source.source_record_id
            == second_risk.id
        )
        import src.services.alert_execution_service as execution_module

        def fail_historical_pattern_scan(*args, **kwargs):
            raise AssertionError(
                "normal alert execution must not scan historical Patterns"
            )

        def fail_historical_anomaly_scan(*args, **kwargs):
            raise AssertionError(
                "normal alert execution must not scan historical Anomalies"
            )

        monkeypatch.setattr(
            execution_module,
            "_load_visible_pattern_reconciliation_batch",
            fail_historical_pattern_scan,
        )
        monkeypatch.setattr(
            execution_module,
            "_load_visible_anomaly_reconciliation_batch",
            fail_historical_anomaly_scan,
        )
        second_result = execute_alert_generation(
            db,
            source=second_source,
        )

        db.commit()

        assert second_result.risk_source_count == 1
        assert second_result.risk_candidate_count == 0
        assert second_result.pattern_source_count == 0
        assert second_result.pattern_candidate_count == 0

        assert second_result.alert_created_count == 0
        assert second_result.alert_existing_count == 0

        pattern_checkpoint = (
            _checkpoint_for_organization(
                db,
                organization_id=organization.id,
                pipeline_name=(
                    PIPELINE_ALERT_PATTERN_MATERIALIZATION_V1
                ),
            )
        )

        assert pattern_checkpoint is not None
        assert (
            pattern_checkpoint.id
            == pattern_checkpoint_id
        )
        assert (
            pattern_checkpoint.source_created_at
            == pattern.created_at
        )
        assert (
            pattern_checkpoint.source_record_id
            == pattern.id
        )
        assert (
            pattern_checkpoint.processed_at
            == pattern_checkpoint_processed_at
        )

        second_alerts = _alerts_for_organization(
            db,
            organization_id=organization.id,
        )

        assert len(second_alerts) == 1
        assert (
            second_alerts[0].source_id
            == pattern.id
        )

        assert (
            first_risk.id
            != second_risk.id
        )

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_alerts_and_checkpoint_roll_back_together():
    setup_db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            setup_db
        )
        organization_id = organization.id

        base = datetime(
            2026,
            9,
            10,
            3,
            0,
            tzinfo=UTC,
        )

        source_risk = _make_risk_assessment(
            setup_db,
            organization_id=organization.id,
            assessed_at=base,
            created_at=base + timedelta(hours=1),
            score=0.90,
        )

        setup_db.commit()

        execution_db = SessionLocal()

        try:
            source = discover_pipeline_source(
                execution_db,
                organization_id=organization.id,
                pipeline_name=PIPELINE_ALERT_GENERATION_V1,
            )

            assert source is not None
            assert (
                source.source_record_id
                == source_risk.id
            )

            result = execute_alert_generation(
                execution_db,
                source=source,
            )

            assert result.alert_created_count == 1
            assert result.checkpoint.advanced is True

            visible_inside_transaction = (
                _alerts_for_organization(
                    execution_db,
                    organization_id=organization.id,
                )
            )

            assert len(
                visible_inside_transaction
            ) == 1

            execution_db.rollback()

        finally:
            execution_db.close()

        verify_db = SessionLocal()

        try:
            alerts = _alerts_for_organization(
                verify_db,
                organization_id=organization.id,
            )

            checkpoint = _checkpoint_for_organization(
                verify_db,
                organization_id=organization.id,
            )

            assert alerts == []
            assert checkpoint is None

        finally:
            verify_db.close()

    finally:
        try:
            _cleanup(
                setup_db,
                organization_id,
            )
        finally:
            setup_db.close()

def test_alert_execution_materializes_delivery_before_checkpoint():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            db
        )
        organization_id = organization.id

        webhook = _make_webhook(
            db,
            organization_id=organization.id,
            events=["high_risk"],
        )

        base = datetime(
            2026,
            9,
            11,
            1,
            0,
            tzinfo=UTC,
        )

        source_risk = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=base,
            created_at=base + timedelta(hours=1),
            score=0.90,
        )

        db.commit()

        source = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert source is not None
        assert (
            source.source_record_id
            == source_risk.id
        )

        result = execute_alert_generation(
            db,
            source=source,
        )

        assert result.alert_created_count == 1
        assert result.checkpoint.advanced is True

        alert = (
            db.query(
                Alert
            )
            .filter(
                Alert.organization_id
                == organization.id,
                Alert.source_id
                == source_risk.id,
            )
            .one()
        )

        delivery = (
            db.query(
                AlertDelivery
            )
            .filter(
                AlertDelivery.organization_id
                == organization.id,
                AlertDelivery.alert_id
                == alert.id,
                AlertDelivery.destination_id
                == webhook.id,
            )
            .one_or_none()
        )

        assert delivery is not None
        assert (
            delivery.payload["event"]
            == "alert.created"
        )
        assert (
            delivery.payload["alert"]["id"]
            == str(alert.id)
        )

        checkpoint = (
            _checkpoint_for_organization(
                db,
                organization_id=organization.id,
            )
        )

        assert checkpoint is not None
        assert (
            checkpoint.source_record_id
            == source_risk.id
        )

        db.commit()

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_new_pattern_webhook_backfills_missing_delivery_for_existing_alert():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            db
        )
        organization_id = organization.id

        base = datetime(
            2026,
            9,
            11,
            2,
            0,
            tzinfo=UTC,
        )

        pattern = _make_pattern(
            db,
            organization_id=organization.id,
            entity_id=uuid.uuid4(),
            detected_at=base + timedelta(days=1),
        )

        first_risk = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=base + timedelta(days=2),
            created_at=base + timedelta(days=3),
            score=0.20,
        )

        db.commit()

        first_source = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert first_source is not None

        first_result = execute_alert_generation(
            db,
            source=first_source,
        )

        db.commit()

        assert first_result.alert_created_count == 1

        alert = (
            db.query(
                Alert
            )
            .filter(
                Alert.organization_id
                == organization.id,
                Alert.source_id
                == pattern.id,
            )
            .one()
        )

        assert (
            db.query(
                AlertDelivery
            )
            .filter(
                AlertDelivery.organization_id
                == organization.id,
                AlertDelivery.alert_id
                == alert.id,
            )
            .count()
            == 0
        )

        webhook = _make_webhook(
            db,
            organization_id=organization.id,
            events=["pattern"],
        )

        second_risk = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=base + timedelta(days=3),
            created_at=base + timedelta(days=4),
            score=0.20,
        )

        db.commit()

        second_source = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert second_source is not None
        assert (
            second_source.source_record_id
            == second_risk.id
        )

        second_result = execute_alert_generation(
            db,
            source=second_source,
        )

        assert second_result.alert_created_count == 0
        assert second_result.alert_existing_count == 0

        delivery = (
            db.query(
                AlertDelivery
            )
            .filter(
                AlertDelivery.organization_id
                == organization.id,
                AlertDelivery.alert_id
                == alert.id,
                AlertDelivery.destination_id
                == webhook.id,
            )
            .one_or_none()
        )

        assert delivery is not None

        assert (
            db.query(
                Alert
            )
            .filter(
                Alert.organization_id
                == organization.id,
                Alert.source_id
                == pattern.id,
            )
            .count()
            == 1
        )

        assert (
            db.query(
                AlertDelivery
            )
            .filter(
                AlertDelivery.organization_id
                == organization.id,
                AlertDelivery.alert_id
                == alert.id,
                AlertDelivery.destination_id
                == webhook.id,
            )
            .count()
            == 1
        )

        assert first_risk.id != second_risk.id

        db.commit()

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_fanout_failure_prevents_checkpoint_advancement(
    monkeypatch,
):
    import src.services.alert_execution_service as execution_module

    setup_db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            setup_db
        )
        organization_id = organization.id

        base = datetime(
            2026,
            9,
            11,
            4,
            0,
            tzinfo=UTC,
        )

        source_risk = _make_risk_assessment(
            setup_db,
            organization_id=organization.id,
            assessed_at=base,
            created_at=base + timedelta(hours=1),
            score=0.90,
        )

        setup_db.commit()

        execution_db = SessionLocal()

        try:
            source = discover_pipeline_source(
                execution_db,
                organization_id=organization.id,
                pipeline_name=PIPELINE_ALERT_GENERATION_V1,
            )

            assert source is not None

            def fail_fanout(
                db,
                *,
                organization_id,
                alert_id,
            ):
                raise RuntimeError(
                    "simulated fanout failure"
                )

            monkeypatch.setattr(
                execution_module,
                "fanout_alert_deliveries",
                fail_fanout,
            )

            try:
                execute_alert_generation(
                    execution_db,
                    source=source,
                )

                raise AssertionError(
                    "Expected fanout failure."
                )

            except RuntimeError as exc:
                assert (
                    str(exc)
                    == "simulated fanout failure"
                )

            alert_checkpoint_inside_transaction = (
                _checkpoint_for_organization(
                    execution_db,
                    organization_id=organization.id,
                )
            )

            pattern_checkpoint_inside_transaction = (
                _checkpoint_for_organization(
                    execution_db,
                    organization_id=organization.id,
                    pipeline_name=(
                        PIPELINE_ALERT_PATTERN_MATERIALIZATION_V1
                    ),
                )
            )

            anomaly_checkpoint_inside_transaction = (
                _checkpoint_for_organization(
                    execution_db,
                    organization_id=organization.id,
                    pipeline_name=(
                        PIPELINE_ALERT_ANOMALY_MATERIALIZATION_V1
                    ),
                )
            )

            assert (
                alert_checkpoint_inside_transaction
                is None
            )
            assert (
                pattern_checkpoint_inside_transaction
                is None
            )
            assert (
                anomaly_checkpoint_inside_transaction
                is None
            )

            execution_db.rollback()

        finally:
            execution_db.close()

        verify_db = SessionLocal()

        try:
            alerts = _alerts_for_organization(
                verify_db,
                organization_id=organization.id,
            )

            checkpoint = (
                _checkpoint_for_organization(
                    verify_db,
                    organization_id=organization.id,
                )
            )

            deliveries = (
                verify_db.query(
                    AlertDelivery
                )
                .filter(
                    AlertDelivery.organization_id
                    == organization.id
                )
                .all()
            )

            assert alerts == []
            assert checkpoint is None
            assert deliveries == []

        finally:
            verify_db.close()

        assert source_risk.id is not None

    finally:
        try:
            _cleanup(
                setup_db,
                organization_id,
            )
        finally:
            setup_db.close()

def test_alert_execution_materializes_matching_delivery():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            db
        )
        organization_id = organization.id

        base = datetime(
            2026,
            9,
            10,
            6,
            0,
            tzinfo=UTC,
        )

        source_risk = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=base,
            created_at=base + timedelta(hours=1),
            score=0.90,
        )

        webhook = _make_webhook(
            db,
            organization_id=organization.id,
            events=["high_risk"],
        )

        db.commit()

        source = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert source is not None
        assert (
            source.source_record_id
            == source_risk.id
        )

        result = execute_alert_generation(
            db,
            source=source,
        )

        assert result.alert_created_count == 1
        assert result.checkpoint.advanced is True

        alerts = _alerts_for_organization(
            db,
            organization_id=organization.id,
        )

        assert len(alerts) == 1

        delivery = (
            db.query(
                AlertDelivery
            )
            .filter(
                AlertDelivery.organization_id
                == organization.id,
                AlertDelivery.alert_id
                == alerts[0].id,
                AlertDelivery.destination_id
                == webhook.id,
            )
            .one_or_none()
        )

        assert delivery is not None
        assert delivery.attempt_count == 0
        assert (
            delivery.payload["event"]
            == "alert.created"
        )

        db.commit()

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_new_webhook_reconciles_existing_alert_delivery():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            db
        )
        organization_id = organization.id

        base = datetime(
            2026,
            9,
            10,
            9,
            0,
            tzinfo=UTC,
        )

        entity_id = uuid.uuid4()

        pattern = _make_pattern(
            db,
            organization_id=organization.id,
            entity_id=entity_id,
            detected_at=base,
        )

        first_risk = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=base,
            created_at=base + timedelta(hours=1),
            score=0.20,
        )

        db.commit()

        first_source = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert first_source is not None

        first_result = execute_alert_generation(
            db,
            source=first_source,
        )

        db.commit()

        assert first_result.alert_created_count == 1

        alerts = _alerts_for_organization(
            db,
            organization_id=organization.id,
        )

        assert len(alerts) == 1
        assert alerts[0].source_id == pattern.id

        delivery_count_before = (
            db.query(
                AlertDelivery
            )
            .filter(
                AlertDelivery.organization_id
                == organization.id
            )
            .count()
        )

        assert delivery_count_before == 0

        webhook = _make_webhook(
            db,
            organization_id=organization.id,
            events=["pattern"],
        )

        second_risk = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=base + timedelta(hours=2),
            created_at=base + timedelta(hours=3),
            score=0.20,
        )

        db.commit()

        second_source = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert second_source is not None
        assert (
            second_source.source_record_id
            == second_risk.id
        )

        second_result = execute_alert_generation(
            db,
            source=second_source,
        )

        delivery_checkpoint = (
            db.query(
                PipelineCheckpoint
            )
            .filter(
                PipelineCheckpoint.organization_id
                == organization.id,
                PipelineCheckpoint.pipeline_name
                == PIPELINE_ALERT_DELIVERY_RECONCILIATION_V1,
            )
            .one_or_none()
        )

        assert delivery_checkpoint is not None
        assert (
            delivery_checkpoint.source_record_id
            == webhook.id
        )
        assert (
            delivery_checkpoint.source_created_at
            == webhook.created_at
        )
        assert (
            delivery_checkpoint.metadata_json[
                "source_count"
            ]
            == 1
        )

        db.commit()

        assert second_result.alert_created_count == 0
        assert second_result.alert_existing_count == 0

        alerts_after = _alerts_for_organization(
            db,
            organization_id=organization.id,
        )

        assert len(alerts_after) == 1
        assert alerts_after[0].id == alerts[0].id

        deliveries = (
            db.query(
                AlertDelivery
            )
            .filter(
                AlertDelivery.organization_id
                == organization.id,
                AlertDelivery.alert_id
                == alerts_after[0].id,
                AlertDelivery.destination_id
                == webhook.id,
            )
            .all()
        )

        assert len(deliveries) == 1

        assert first_risk.id != second_risk.id

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()

def test_inactive_new_webhook_advances_delivery_reconciliation_checkpoint():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            db
        )
        organization_id = organization.id

        base = datetime(
            2026,
            9,
            13,
            14,
            0,
            tzinfo=UTC,
        )

        pattern = _make_pattern(
            db,
            organization_id=organization.id,
            entity_id=uuid.uuid4(),
            detected_at=base,
        )

        first_risk = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=base,
            created_at=base + timedelta(hours=1),
            score=0.20,
        )

        db.commit()

        first_source = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert first_source is not None

        first_result = execute_alert_generation(
            db,
            source=first_source,
        )

        db.commit()

        assert first_result.alert_created_count == 1

        alert = (
            db.query(
                Alert
            )
            .filter(
                Alert.organization_id
                == organization.id,
                Alert.source_id
                == pattern.id,
            )
            .one()
        )

        webhook = _make_webhook(
            db,
            organization_id=organization.id,
            events=["pattern"],
        )
        webhook.is_active = False

        second_risk = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=base + timedelta(hours=2),
            created_at=base + timedelta(hours=3),
            score=0.20,
        )

        db.commit()

        second_source = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert second_source is not None
        assert (
            second_source.source_record_id
            == second_risk.id
        )

        execute_alert_generation(
            db,
            source=second_source,
        )

        delivery_checkpoint = (
            db.query(
                PipelineCheckpoint
            )
            .filter(
                PipelineCheckpoint.organization_id
                == organization.id,
                PipelineCheckpoint.pipeline_name
                == PIPELINE_ALERT_DELIVERY_RECONCILIATION_V1,
            )
            .one()
        )

        assert (
            delivery_checkpoint.source_record_id
            == webhook.id
        )
        assert (
            delivery_checkpoint.source_created_at
            == webhook.created_at
        )
        assert (
            delivery_checkpoint.metadata_json[
                "source_count"
            ]
            == 1
        )

        assert (
            db.query(
                AlertDelivery
            )
            .filter(
                AlertDelivery.organization_id
                == organization.id,
                AlertDelivery.alert_id
                == alert.id,
                AlertDelivery.destination_id
                == webhook.id,
            )
            .count()
            == 0
        )

        db.commit()

        assert first_risk.id != second_risk.id

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()

def test_delivery_reconciliation_failure_prevents_checkpoint_advancement(
    monkeypatch,
):
    import src.services.alert_execution_service as execution_module

    setup_db = SessionLocal()
    execution_db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            setup_db
        )
        organization_id = organization.id

        base = datetime(
            2026,
            9,
            13,
            15,
            0,
            tzinfo=UTC,
        )

        pattern = _make_pattern(
            setup_db,
            organization_id=organization.id,
            entity_id=uuid.uuid4(),
            detected_at=base,
        )

        second_pattern = _make_pattern(
            setup_db,
            organization_id=organization.id,
            entity_id=uuid.uuid4(),
            detected_at=(
                base
                + timedelta(seconds=1)
            ),
        )

        first_risk = _make_risk_assessment(
            setup_db,
            organization_id=organization.id,
            assessed_at=base,
            created_at=base + timedelta(hours=1),
            score=0.20,
        )

        setup_db.commit()

        first_source = discover_pipeline_source(
            execution_db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert first_source is not None
        assert (
            first_source.source_record_id
            == first_risk.id
        )

        first_result = execute_alert_generation(
            execution_db,
            source=first_source,
        )

        execution_db.commit()

        assert first_result.alert_created_count == 2

        existing_alert = (
            execution_db.query(
                Alert
            )
            .filter(
                Alert.organization_id
                == organization.id,
                Alert.source_id
                == pattern.id,
            )
            .one()
        )

        second_existing_alert = (
            execution_db.query(
                Alert
            )
            .filter(
                Alert.organization_id
                == organization.id,
                Alert.source_id
                == second_pattern.id,
            )
            .one()
        )

        webhook = _make_webhook(
            setup_db,
            organization_id=organization.id,
            events=["pattern"],
        )

        second_risk = _make_risk_assessment(
            setup_db,
            organization_id=organization.id,
            assessed_at=base + timedelta(hours=2),
            created_at=base + timedelta(hours=3),
            score=0.20,
        )

        setup_db.commit()

        alert_checkpoint_before = (
            _checkpoint_for_organization(
                execution_db,
                organization_id=organization.id,
            )
        )

        assert alert_checkpoint_before is not None
        assert (
            alert_checkpoint_before.source_record_id
            == first_risk.id
        )

        delivery_checkpoint_before = (
            _checkpoint_for_organization(
                execution_db,
                organization_id=organization.id,
                pipeline_name=(
                    PIPELINE_ALERT_DELIVERY_RECONCILIATION_V1
                ),
            )
        )

        assert delivery_checkpoint_before is None

        second_source = discover_pipeline_source(
            execution_db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert second_source is not None
        assert (
            second_source.source_record_id
            == second_risk.id
        )

        original_reconcile_alert_deliveries = (
            execution_module.reconcile_alert_deliveries
        )

        reconciliation_call_count = 0

        def fail_second_reconciliation_batch(
            db,
            *,
            organization_id,
            alerts,
            subscriptions,
        ):
            nonlocal reconciliation_call_count

            reconciliation_call_count += 1

            if reconciliation_call_count == 2:
                raise RuntimeError(
                    "simulated delivery reconciliation failure"
                )

            return original_reconcile_alert_deliveries(
                db,
                organization_id=organization_id,
                alerts=alerts,
                subscriptions=subscriptions,
            )

        monkeypatch.setattr(
            execution_module,
            "ALERT_DELIVERY_RECONCILIATION_BATCH_SIZE",
            1,
        )

        monkeypatch.setattr(
            execution_module,
            "reconcile_alert_deliveries",
            fail_second_reconciliation_batch,
        )

        eligible_alerts = (
            execution_db.query(Alert)
            .filter(
                Alert.organization_id
                == organization.id,
                Alert.alert_type == "pattern",
            )
            .all()
        )

        assert len(eligible_alerts) == 2

        with pytest.raises(
            RuntimeError,
            match=(
                "simulated delivery reconciliation failure"
            ),
        ):
            execute_alert_generation(
                execution_db,
                source=second_source,
            )

        execution_db.rollback()
        assert reconciliation_call_count == 2

        verify_db = SessionLocal()

        try:
            alert_checkpoint_after = (
                _checkpoint_for_organization(
                    verify_db,
                    organization_id=organization.id,
                )
            )

            assert alert_checkpoint_after is not None
            assert (
                alert_checkpoint_after.source_record_id
                == first_risk.id
            )

            delivery_checkpoint_after = (
                _checkpoint_for_organization(
                    verify_db,
                    organization_id=organization.id,
                    pipeline_name=(
                        PIPELINE_ALERT_DELIVERY_RECONCILIATION_V1
                    ),
                )
            )

            assert delivery_checkpoint_after is None

            assert (
                verify_db.query(
                    AlertDelivery
                )
                .filter(
                    AlertDelivery.organization_id
                    == organization.id,
                    AlertDelivery.destination_id
                    == webhook.id,
                    AlertDelivery.alert_id.in_(
                        (
                            existing_alert.id,
                            second_existing_alert.id,
                        )
                    ),
                )
                .count()
                == 0
            )

        finally:
            verify_db.close()

    finally:
        execution_db.close()

        try:
            _cleanup(
                setup_db,
                organization_id,
            )
        finally:
            setup_db.close()

def test_alert_execution_reconciles_pending_watchlist_policy_revision(
    monkeypatch,
):
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            db
        )
        organization_id = organization.id

        base = datetime(
            2026,
            9,
            10,
            11,
            0,
            tzinfo=UTC,
        )

        source_risk = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=base,
            created_at=(
                base
                + timedelta(hours=1)
            ),
            score=0.20,
        )

        _make_watchlist(
            db,
            organization_id=organization.id,
            entity_ids=[],
            alert_on_anomaly=False,
        )

        revision = bump_alert_policy_revision(
            db,
            organization_id=organization.id,
        )

        assert revision.policy_revision == 1
        assert revision.reconciled_revision == 0
        assert revision.reconciliation_required is True

        db.commit()

        source = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert source is not None
        assert (
            source.source_record_id
            == source_risk.id
        )
        import src.services.alert_execution_service as execution_module

        original_load_visible_patterns = (
            execution_module._load_visible_pattern_reconciliation_batch
        )
        original_load_visible_anomalies = (
            execution_module._load_visible_anomaly_reconciliation_batch
        )

        historical_pattern_scan_count = 0
        historical_anomaly_scan_count = 0

        def track_historical_pattern_scan(
            db,
            *,
            organization_id,
            temporal_cursor,
            after_created_at,
            after_id,
            limit,
        ):
            nonlocal historical_pattern_scan_count
            historical_pattern_scan_count += 1

            return original_load_visible_patterns(
                db,
                organization_id=organization_id,
                temporal_cursor=temporal_cursor,
                after_created_at=after_created_at,
                after_id=after_id,
                limit=limit,
            )

        def track_historical_anomaly_scan(
            db,
            *,
            organization_id,
            temporal_cursor,
            after_created_at,
            after_id,
            limit,
        ):
            nonlocal historical_anomaly_scan_count
            historical_anomaly_scan_count += 1

            return original_load_visible_anomalies(
                db,
                organization_id=organization_id,
                temporal_cursor=temporal_cursor,
                after_created_at=after_created_at,
                after_id=after_id,
                limit=limit,
            )

        monkeypatch.setattr(
            execution_module,
            "_load_visible_pattern_reconciliation_batch",
            track_historical_pattern_scan,
        )
        monkeypatch.setattr(
            execution_module,
            "_load_visible_anomaly_reconciliation_batch",
            track_historical_anomaly_scan,
        )
        result = execute_alert_generation(
            db,
            source=source,
        )

        assert historical_pattern_scan_count == 1
        assert historical_anomaly_scan_count == 1

        revision_inside_transaction = (
            load_alert_policy_revision(
                db,
                organization_id=organization.id,
            )
        )

        assert revision_inside_transaction is not None
        assert (
            revision_inside_transaction.policy_revision
            == 1
        )
        assert (
            revision_inside_transaction.reconciled_revision
            == 1
        )
        assert (
            revision_inside_transaction.reconciliation_required
            is False
        )
        assert result.checkpoint.advanced is True

        db.commit()

        verify_db = SessionLocal()

        try:
            persisted_revision = (
                load_alert_policy_revision(
                    verify_db,
                    organization_id=organization.id,
                )
            )

            assert persisted_revision is not None
            assert (
                persisted_revision.policy_revision
                == 1
            )
            assert (
                persisted_revision.reconciled_revision
                == 1
            )
            assert (
                persisted_revision.reconciliation_required
                is False
            )
            assert (
                persisted_revision.reconciled_at
                is not None
            )

        finally:
            verify_db.close()

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()

def test_alert_policy_reconciliation_scans_patterns_in_keyset_batches(
    monkeypatch,
):
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            db
        )
        organization_id = organization.id

        base = datetime(
            2026,
            9,
            10,
            11,
            15,
            tzinfo=UTC,
        )

        source_risk = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=(
                base
                + timedelta(days=1)
            ),
            created_at=(
                base
                + timedelta(days=2)
            ),
            score=0.20,
        )

        entity_id = uuid.uuid4()
        shared_created_at = (
            base
            + timedelta(hours=1)
        )

        pattern_ids = sorted(
            (
                uuid.uuid4()
                for _ in range(5)
            ),
            key=lambda value: value.int,
        )

        patterns = tuple(
            _make_pattern(
                db,
                organization_id=organization.id,
                entity_id=entity_id,
                detected_at=(
                    base
                    + timedelta(minutes=index)
                ),
                created_at=shared_created_at,
                pattern_id=pattern_id,
            )
            for index, pattern_id in enumerate(
                pattern_ids
            )
        )

        _make_watchlist(
            db,
            organization_id=organization.id,
            entity_ids=[],
            alert_on_anomaly=False,
        )

        db.commit()

        initial_source = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert initial_source is not None
        assert (
            initial_source.source_record_id
            == source_risk.id
        )

        initial_result = execute_alert_generation(
            db,
            source=initial_source,
        )

        assert initial_result.pattern_source_count == len(
            patterns
        )
        assert initial_result.pattern_candidate_count == len(
            patterns
        )
        assert initial_result.alert_created_count == len(
            patterns
        )
        assert initial_result.alert_existing_count == 0

        db.commit()

        baseline_alerts = _alerts_for_organization(
            db,
            organization_id=organization.id,
        )

        assert len(
            baseline_alerts
        ) == len(
            patterns
        )
        assert {
            alert.source_id
            for alert in baseline_alerts
        } == set(
            pattern_ids
        )

        revision = bump_alert_policy_revision(
            db,
            organization_id=organization.id,
        )

        assert revision.policy_revision == 1
        assert revision.reconciled_revision == 0
        assert revision.reconciliation_required is True

        db.commit()

        normal_source = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert normal_source is None

        source = discover_alert_reconciliation_source(
            db,
            organization_id=organization.id,
        )

        assert source is not None
        assert (
            source.pipeline_name
            == PIPELINE_ALERT_GENERATION_V1
        )
        assert (
            source.source_record_id
            == source_risk.id
        )

        import src.services.alert_execution_service as execution_module

        monkeypatch.setattr(
            execution_module,
            "ALERT_POLICY_RECONCILIATION_BATCH_SIZE",
            2,
        )

        original_loader = (
            execution_module
            ._load_visible_pattern_reconciliation_batch
        )
        original_fanout = (
            execution_module
            .fanout_alert_deliveries
        )

        loaded_batches = []
        execution_events = []

        def track_pattern_batch(
            db,
            *,
            organization_id,
            temporal_cursor,
            after_created_at,
            after_id,
            limit,
        ):
            batch = original_loader(
                db,
                organization_id=organization_id,
                temporal_cursor=temporal_cursor,
                after_created_at=after_created_at,
                after_id=after_id,
                limit=limit,
            )

            batch_ids = tuple(
                pattern.id
                for pattern in batch
            )

            loaded_batches.append(
                batch_ids
            )
            execution_events.append(
                ("load", batch_ids)
            )

            return batch

        def track_fanout(
            db,
            *,
            organization_id,
            alert_id,
        ):
            alert = (
                db.query(Alert)
                .filter(
                    Alert.id == alert_id,
                    Alert.organization_id
                    == organization_id,
                )
                .one()
            )

            execution_events.append(
                ("fanout", alert.source_id)
            )

            return original_fanout(
                db,
                organization_id=organization_id,
                alert_id=alert_id,
            )

        monkeypatch.setattr(
            execution_module,
            "_load_visible_pattern_reconciliation_batch",
            track_pattern_batch,
        )
        monkeypatch.setattr(
            execution_module,
            "fanout_alert_deliveries",
            track_fanout,
        )

        result = execute_alert_generation(
            db,
            source=source,
        )

        assert loaded_batches == [
            tuple(
                pattern_ids[0:2]
            ),
            tuple(
                pattern_ids[2:4]
            ),
            tuple(
                pattern_ids[4:5]
            ),
            (),
        ]

        assert execution_events == [
            (
                "load",
                tuple(pattern_ids[0:2]),
            ),
            ("fanout", pattern_ids[0]),
            ("fanout", pattern_ids[1]),
            (
                "load",
                tuple(pattern_ids[2:4]),
            ),
            ("fanout", pattern_ids[2]),
            ("fanout", pattern_ids[3]),
            (
                "load",
                tuple(pattern_ids[4:5]),
            ),
            ("fanout", pattern_ids[4]),
            ("load", ()),
        ]

        loaded_pattern_ids = [
            pattern_id
            for batch in loaded_batches
            for pattern_id in batch
        ]

        assert loaded_pattern_ids == pattern_ids
        assert len(
            loaded_pattern_ids
        ) == len(
            set(
                loaded_pattern_ids
            )
        )

        assert result.pattern_source_count == len(
            patterns
        )
        assert result.pattern_candidate_count == len(
            patterns
        )
        assert result.alert_created_count == 0
        assert result.alert_existing_count == len(
            patterns
        )

        revision_inside_transaction = (
            load_alert_policy_revision(
                db,
                organization_id=organization.id,
            )
        )

        assert revision_inside_transaction is not None
        assert (
            revision_inside_transaction.policy_revision
            == 1
        )
        assert (
            revision_inside_transaction.reconciled_revision
            == 1
        )
        assert (
            revision_inside_transaction.reconciliation_required
            is False
        )

        db.commit()

        alerts = _alerts_for_organization(
            db,
            organization_id=organization.id,
        )

        assert len(
            alerts
        ) == len(
            patterns
        )
        assert {
            alert.source_id
            for alert in alerts
        } == set(
            pattern_ids
        )

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()

def test_alert_policy_reconciliation_scans_anomalies_in_bounded_batches(
    monkeypatch,
):
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            db
        )
        organization_id = organization.id

        _, page = _make_entity_and_page(
            db,
            organization_id=organization.id,
            suffix="AnomalyPolicyBatch",
        )

        base = datetime(
            2026,
            9,
            10,
            11,
            20,
            tzinfo=UTC,
        )

        shared_created_at = (
            base
            + timedelta(hours=1)
        )

        anomalies = []

        for index in range(5):
            event = _make_event(
                db,
                organization_id=organization.id,
                page=page,
                detected_at=(
                    base
                    + timedelta(minutes=index)
                ),
                suffix=f"apb-{index}",
            )

            anomalies.append(
                _make_anomaly(
                    db,
                    organization_id=organization.id,
                    event=event,
                    detected_at=(
                        base
                        + timedelta(
                            minutes=index,
                            seconds=30,
                        )
                    ),
                    created_at=shared_created_at,
                )
            )

        source_risk = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=(
                base
                + timedelta(hours=2)
            ),
            created_at=(
                base
                + timedelta(hours=3)
            ),
            score=0.20,
        )

        watchlist = _make_watchlist(
            db,
            organization_id=organization.id,
            page_ids=[
                str(
                    page.id
                )
            ],
            alert_on_anomaly=False,
        )

        db.commit()

        source = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert source is not None
        assert (
            source.source_record_id
            == source_risk.id
        )

        first_result = execute_alert_generation(
            db,
            source=source,
        )

        assert first_result.anomaly_source_count == len(
            anomalies
        )
        assert first_result.anomaly_candidate_count == 0
        assert first_result.alert_created_count == 0

        db.commit()

        anomaly_checkpoint = (
            _checkpoint_for_organization(
                db,
                organization_id=organization.id,
                pipeline_name=(
                    PIPELINE_ALERT_ANOMALY_MATERIALIZATION_V1
                ),
            )
        )

        assert anomaly_checkpoint is not None
        assert anomaly_checkpoint.metadata_json == {
            "source_count": len(
                anomalies
            ),
        }

        watchlist.alert_on_anomaly = True

        revision = bump_alert_policy_revision(
            db,
            organization_id=organization.id,
        )

        assert revision.policy_revision == 1
        assert revision.reconciled_revision == 0
        assert revision.reconciliation_required is True

        db.commit()

        import src.services.alert_execution_service as execution_module

        monkeypatch.setattr(
            execution_module,
            "ALERT_POLICY_RECONCILIATION_BATCH_SIZE",
            2,
        )

        original_loader = (
            execution_module
            ._load_visible_anomaly_reconciliation_batch
        )
        original_load_evidence_maps = (
            execution_module._load_evidence_maps
        )

        loaded_batches = []
        historical_evidence_batches = []

        def track_anomaly_batch(
            db,
            *,
            organization_id,
            temporal_cursor,
            after_created_at,
            after_id,
            limit,
        ):
            batch = original_loader(
                db,
                organization_id=organization_id,
                temporal_cursor=temporal_cursor,
                after_created_at=after_created_at,
                after_id=after_id,
                limit=limit,
            )

            loaded_batches.append(
                tuple(
                    anomaly.id
                    for anomaly in batch
                )
            )

            return batch

        def track_evidence_maps(
            db,
            *,
            organization_id,
            risk_sources,
            anomalies,
        ):
            if (
                not risk_sources
                and anomalies
            ):
                historical_evidence_batches.append(
                    tuple(
                        anomaly.id
                        for anomaly in anomalies
                    )
                )

            return original_load_evidence_maps(
                db,
                organization_id=organization_id,
                risk_sources=risk_sources,
                anomalies=anomalies,
            )

        monkeypatch.setattr(
            execution_module,
            "_load_visible_anomaly_reconciliation_batch",
            track_anomaly_batch,
        )
        monkeypatch.setattr(
            execution_module,
            "_load_evidence_maps",
            track_evidence_maps,
        )

        second_result = execute_alert_generation(
            db,
            source=source,
        )

        expected_anomaly_ids = sorted(
            (
                anomaly.id
                for anomaly in anomalies
            ),
            key=lambda value: value.int,
        )

        assert loaded_batches == [
            tuple(
                expected_anomaly_ids[0:2]
            ),
            tuple(
                expected_anomaly_ids[2:4]
            ),
            tuple(
                expected_anomaly_ids[4:5]
            ),
            (),
        ]

        assert historical_evidence_batches == [
            tuple(
                expected_anomaly_ids[0:2]
            ),
            tuple(
                expected_anomaly_ids[2:4]
            ),
            tuple(
                expected_anomaly_ids[4:5]
            ),
        ]

        loaded_anomaly_ids = [
            anomaly_id
            for batch in loaded_batches
            for anomaly_id in batch
        ]

        assert loaded_anomaly_ids == expected_anomaly_ids
        assert len(
            loaded_anomaly_ids
        ) == len(
            set(
                loaded_anomaly_ids
            )
        )

        evidence_anomaly_ids = [
            anomaly_id
            for batch in historical_evidence_batches
            for anomaly_id in batch
        ]

        assert evidence_anomaly_ids == expected_anomaly_ids
        assert all(
            len(
                batch
            ) <= 2
            for batch in historical_evidence_batches
        )

        assert second_result.anomaly_source_count == len(
            anomalies
        )
        assert second_result.anomaly_candidate_count == len(
            anomalies
        )
        assert second_result.alert_created_count == len(
            anomalies
        )

        revision_inside_transaction = (
            load_alert_policy_revision(
                db,
                organization_id=organization.id,
            )
        )

        assert revision_inside_transaction is not None
        assert (
            revision_inside_transaction.policy_revision
            == 1
        )
        assert (
            revision_inside_transaction.reconciled_revision
            == 1
        )
        assert (
            revision_inside_transaction.reconciliation_required
            is False
        )

        db.commit()

        alerts = _alerts_for_organization(
            db,
            organization_id=organization.id,
        )

        assert len(
            alerts
        ) == len(
            anomalies
        )
        assert {
            alert.source_id
            for alert in alerts
        } == {
            anomaly.id
            for anomaly in anomalies
        }

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()

def test_alert_policy_reconciliation_rolls_back_with_alert_transaction():
    setup_db = SessionLocal()
    execution_db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            setup_db
        )
        organization_id = organization.id

        base = datetime(
            2026,
            9,
            10,
            11,
            30,
            tzinfo=UTC,
        )

        source_risk = _make_risk_assessment(
            setup_db,
            organization_id=organization.id,
            assessed_at=base,
            created_at=(
                base
                + timedelta(hours=1)
            ),
            score=0.20,
        )

        _make_watchlist(
            setup_db,
            organization_id=organization.id,
            entity_ids=[],
            alert_on_anomaly=False,
        )

        revision = bump_alert_policy_revision(
            setup_db,
            organization_id=organization.id,
        )

        assert revision.policy_revision == 1
        assert revision.reconciled_revision == 0

        setup_db.commit()

        source = discover_pipeline_source(
            execution_db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert source is not None
        assert (
            source.source_record_id
            == source_risk.id
        )

        assert source is not None
        assert (
            source.pipeline_name
            == PIPELINE_ALERT_GENERATION_V1
        )
        assert (
            source.source_record_id
            == source_risk.id
        )

        result = execute_alert_generation(
            execution_db,
            source=source,
        )

        revision_inside_transaction = (
            load_alert_policy_revision(
                execution_db,
                organization_id=organization.id,
            )
        )

        assert revision_inside_transaction is not None
        assert (
            revision_inside_transaction.policy_revision
            == 1
        )
        assert (
            revision_inside_transaction.reconciled_revision
            == 1
        )
        assert (
            revision_inside_transaction.reconciliation_required
            is False
        )
        assert result.checkpoint.advanced is True

        execution_db.rollback()

        verify_db = SessionLocal()

        try:
            persisted_revision = (
                load_alert_policy_revision(
                    verify_db,
                    organization_id=organization.id,
                )
            )

            assert persisted_revision is not None
            assert (
                persisted_revision.policy_revision
                == 1
            )
            assert (
                persisted_revision.reconciled_revision
                == 0
            )
            assert (
                persisted_revision.reconciliation_required
                is True
            )
            assert (
                persisted_revision.reconciled_at
                is None
            )

            alert_checkpoint = (
                _checkpoint_for_organization(
                    verify_db,
                    organization_id=organization.id,
                )
            )

            assert alert_checkpoint is None

        finally:
            verify_db.close()

    finally:
        execution_db.close()

        try:
            _cleanup(
                setup_db,
                organization_id,
            )
        finally:
            setup_db.close()

def test_fanout_failure_prevents_alert_checkpoint_advancement(
    monkeypatch,
):
    setup_db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            setup_db
        )
        organization_id = organization.id

        base = datetime(
            2026,
            9,
            10,
            12,
            0,
            tzinfo=UTC,
        )

        source_risk = _make_risk_assessment(
            setup_db,
            organization_id=organization.id,
            assessed_at=base,
            created_at=base + timedelta(hours=1),
            score=0.90,
        )

        entity, page = _make_entity_and_page(
            setup_db,
            organization_id=organization.id,
            suffix="FanoutCheckpointRollback",
        )

        event = _make_event(
            setup_db,
            organization_id=organization.id,
            page=page,
            detected_at=base,
            suffix="fanout-rb",
        )

        pattern = _make_pattern(
            setup_db,
            organization_id=organization.id,
            entity_id=entity.id,
            detected_at=base,
            created_at=(
                base
                + timedelta(minutes=30)
            ),
        )

        anomaly = _make_anomaly(
            setup_db,
            organization_id=organization.id,
            event=event,
            detected_at=base,
            created_at=(
                base
                + timedelta(minutes=45)
            ),
        )

        policy_revision = (
            bump_alert_policy_revision(
                setup_db,
                organization_id=(
                    organization.id
                ),
            )
        )

        assert (
            policy_revision.policy_revision
            == 1
        )
        assert (
            policy_revision.reconciled_revision
            == 0
        )

        setup_db.commit()

        def _fail_fanout(
            db,
            *,
            organization_id,
            alert_id,
        ):
            raise RuntimeError(
                "forced fanout failure"
            )

        monkeypatch.setattr(
            "src.services.alert_execution_service."
            "fanout_alert_deliveries",
            _fail_fanout,
        )

        execution_db = SessionLocal()

        try:
            source = discover_pipeline_source(
                execution_db,
                organization_id=organization.id,
                pipeline_name=PIPELINE_ALERT_GENERATION_V1,
            )

            assert source is not None
            assert (
                source.source_record_id
                == source_risk.id
            )

            with pytest.raises(
                RuntimeError,
                match="forced fanout failure",
            ):
                execute_alert_generation(
                    execution_db,
                    source=source,
                )

            alert_checkpoint_inside_transaction = (
                _checkpoint_for_organization(
                    execution_db,
                    organization_id=organization.id,
                )
            )

            pattern_checkpoint_inside_transaction = (
                _checkpoint_for_organization(
                    execution_db,
                    organization_id=organization.id,
                    pipeline_name=(
                        PIPELINE_ALERT_PATTERN_MATERIALIZATION_V1
                    ),
                )
            )

            anomaly_checkpoint_inside_transaction = (
                _checkpoint_for_organization(
                    execution_db,
                    organization_id=organization.id,
                    pipeline_name=(
                        PIPELINE_ALERT_ANOMALY_MATERIALIZATION_V1
                    ),
                )
            )

            assert (
                alert_checkpoint_inside_transaction
                is None
            )
            assert (
                pattern_checkpoint_inside_transaction
                is None
            )
            assert (
                anomaly_checkpoint_inside_transaction
                is None
            )

            policy_revision_inside_transaction = (
                load_alert_policy_revision(
                    execution_db,
                    organization_id=(
                        organization.id
                    ),
                )
            )

            assert (
                policy_revision_inside_transaction
                is not None
            )
            assert (
                policy_revision_inside_transaction.policy_revision
                == 1
            )
            assert (
                policy_revision_inside_transaction.reconciled_revision
                == 0
            )
            assert (
                policy_revision_inside_transaction.reconciliation_required
                is True
            )

            execution_db.rollback()

        finally:
            execution_db.close()

        verify_db = SessionLocal()

        try:
            alerts = _alerts_for_organization(
                verify_db,
                organization_id=organization.id,
            )

            alert_checkpoint = (
                _checkpoint_for_organization(
                    verify_db,
                    organization_id=organization.id,
                )
            )

            pattern_checkpoint = (
                _checkpoint_for_organization(
                    verify_db,
                    organization_id=organization.id,
                    pipeline_name=(
                        PIPELINE_ALERT_PATTERN_MATERIALIZATION_V1
                    ),
                )
            )

            anomaly_checkpoint = (
                _checkpoint_for_organization(
                    verify_db,
                    organization_id=organization.id,
                    pipeline_name=(
                        PIPELINE_ALERT_ANOMALY_MATERIALIZATION_V1
                    ),
                )
            )

            deliveries = (
                verify_db.query(
                    AlertDelivery
                )
                .filter(
                    AlertDelivery.organization_id
                    == organization.id
                )
                .all()
            )

            assert alerts == []
            assert alert_checkpoint is None
            assert pattern_checkpoint is None
            assert anomaly_checkpoint is None
            assert deliveries == []
            policy_revision = (
                load_alert_policy_revision(
                    verify_db,
                    organization_id=(
                        organization.id
                    ),
                )
            )

            assert policy_revision is not None
            assert (
                policy_revision.policy_revision
                == 1
            )
            assert (
                policy_revision.reconciled_revision
                == 0
            )
            assert (
                policy_revision.reconciliation_required
                is True
            )
            assert (
                policy_revision.reconciled_at
                is None
            )
            assert pattern.id is not None
            assert anomaly.id is not None

        finally:
            verify_db.close()

    finally:
        try:
            _cleanup(
                setup_db,
                organization_id,
            )
        finally:
            setup_db.close()

def test_anomaly_rescan_materializes_alert_and_delivery():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            db
        )
        organization_id = organization.id

        entity, page = _make_entity_and_page(
            db,
            organization_id=organization.id,
            suffix="AnomalyDelivery",
        )

        base = datetime(
            2026,
            9,
            12,
            1,
            0,
            tzinfo=UTC,
        )

        event = _make_event(
            db,
            organization_id=organization.id,
            page=page,
            detected_at=base,
            suffix="anomaly-delivery",
        )

        anomaly = _make_anomaly(
            db,
            organization_id=organization.id,
            event=event,
            detected_at=(
                base
                + timedelta(minutes=5)
            ),
        )

        webhook = _make_webhook(
            db,
            organization_id=organization.id,
            events=["alert.created"],
        )

        source_risk = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=(
                base
                + timedelta(hours=1)
            ),
            created_at=(
                base
                + timedelta(hours=2)
            ),
            score=0.20,
        )

        db.commit()

        source = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert source is not None
        assert (
            source.source_record_id
            == source_risk.id
        )

        result = execute_alert_generation(
            db,
            source=source,
        )

        assert result.risk_candidate_count == 0
        assert result.anomaly_source_count == 1
        assert result.anomaly_candidate_count == 1
        assert result.alert_created_count == 1
        assert result.alert_existing_count == 0
        assert result.checkpoint.advanced is True

        alert = (
            db.query(
                Alert
            )
            .filter(
                Alert.organization_id
                == organization.id,
                Alert.source_type
                == "anomaly",
                Alert.source_id
                == anomaly.id,
            )
            .one()
        )

        assert alert.event_id == event.id
        assert alert.severity is None
        assert (
            str(entity.id)
            in (
                alert.affected_entities
                or []
            )
        )

        delivery = (
            db.query(
                AlertDelivery
            )
            .filter(
                AlertDelivery.organization_id
                == organization.id,
                AlertDelivery.alert_id
                == alert.id,
                AlertDelivery.destination_id
                == webhook.id,
            )
            .one_or_none()
        )

        assert delivery is not None
        assert (
            delivery.payload["alert"]["id"]
            == str(alert.id)
        )

        db.commit()

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()

def test_anomaly_watchlist_policy_can_suppress_alert():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            db
        )
        organization_id = organization.id

        entity, page = _make_entity_and_page(
            db,
            organization_id=organization.id,
            suffix="AnomalySuppressed",
        )

        base = datetime(
            2026,
            9,
            12,
            2,
            0,
            tzinfo=UTC,
        )

        event = _make_event(
            db,
            organization_id=organization.id,
            page=page,
            detected_at=base,
            suffix="anomaly-suppressed",
        )

        _make_anomaly(
            db,
            organization_id=organization.id,
            event=event,
            detected_at=(
                base
                + timedelta(minutes=5)
            ),
        )

        _make_watchlist(
            db,
            organization_id=organization.id,
            entity_ids=[
                str(entity.id)
            ],
            alert_on_anomaly=False,
        )

        source_risk = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=(
                base
                + timedelta(hours=1)
            ),
            created_at=(
                base
                + timedelta(hours=2)
            ),
            score=0.20,
        )

        db.commit()

        source = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert source is not None
        assert (
            source.source_record_id
            == source_risk.id
        )

        result = execute_alert_generation(
            db,
            source=source,
        )

        db.commit()

        assert result.anomaly_source_count == 1
        assert result.anomaly_candidate_count == 0
        assert result.alert_created_count == 0

        anomaly_alert_count = (
            db.query(
                Alert
            )
            .filter(
                Alert.organization_id
                == organization.id,
                Alert.source_type
                == "anomaly",
            )
            .count()
        )

        assert anomaly_alert_count == 0

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()

def test_anomaly_watchlist_requires_scope_match():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            db
        )
        organization_id = organization.id

        _, page = _make_entity_and_page(
            db,
            organization_id=organization.id,
            suffix="AnomalyScope",
        )

        base = datetime(
            2026,
            9,
            12,
            3,
            0,
            tzinfo=UTC,
        )

        event = _make_event(
            db,
            organization_id=organization.id,
            page=page,
            detected_at=base,
            suffix="anomaly-scope",
        )

        _make_anomaly(
            db,
            organization_id=organization.id,
            event=event,
            detected_at=(
                base
                + timedelta(minutes=5)
            ),
        )

        _make_watchlist(
            db,
            organization_id=organization.id,
            entity_ids=[
                str(
                    uuid.uuid4()
                )
            ],
            alert_on_anomaly=True,
        )

        source_risk = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=(
                base
                + timedelta(hours=1)
            ),
            created_at=(
                base
                + timedelta(hours=2)
            ),
            score=0.20,
        )

        db.commit()

        source = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert source is not None
        assert (
            source.source_record_id
            == source_risk.id
        )

        result = execute_alert_generation(
            db,
            source=source,
        )

        db.commit()

        assert result.anomaly_source_count == 1
        assert result.anomaly_candidate_count == 0
        assert result.alert_created_count == 0

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()

def test_anomaly_materialization_is_not_reprocessed_across_new_risk_watermarks():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            db
        )
        organization_id = organization.id

        _, page = _make_entity_and_page(
            db,
            organization_id=organization.id,
            suffix="AnomalyIdempotent",
        )

        base = datetime(
            2026,
            9,
            12,
            4,
            0,
            tzinfo=UTC,
        )

        event = _make_event(
            db,
            organization_id=organization.id,
            page=page,
            detected_at=base,
            suffix="anomaly-idempotent",
        )

        anomaly = _make_anomaly(
            db,
            organization_id=organization.id,
            event=event,
            detected_at=(
                base
                + timedelta(minutes=5)
            ),
        )

        webhook = _make_webhook(
            db,
            organization_id=organization.id,
            events=["alert.created"],
        )

        _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=(
                base
                + timedelta(hours=1)
            ),
            created_at=(
                base
                + timedelta(hours=2)
            ),
            score=0.20,
        )

        db.commit()

        first_source = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert first_source is not None

        first_result = execute_alert_generation(
            db,
            source=first_source,
        )

        db.commit()

        assert first_result.anomaly_source_count == 1
        assert first_result.anomaly_candidate_count == 1
        assert first_result.alert_created_count == 1
        assert first_result.alert_existing_count == 0

        anomaly_checkpoint = (
            _checkpoint_for_organization(
                db,
                organization_id=organization.id,
                pipeline_name=(
                    PIPELINE_ALERT_ANOMALY_MATERIALIZATION_V1
                ),
            )
        )

        assert anomaly_checkpoint is not None
        assert (
            anomaly_checkpoint.source_created_at
            == anomaly.created_at
        )
        assert (
            anomaly_checkpoint.source_record_id
            == anomaly.id
        )
        assert (
            anomaly_checkpoint.knowledge_cursor
            == first_source.temporal_cursor
        )
        assert anomaly_checkpoint.metadata_json == {
            "source_count": 1,
        }

        anomaly_checkpoint_id = (
            anomaly_checkpoint.id
        )
        anomaly_checkpoint_processed_at = (
            anomaly_checkpoint.processed_at
        )

        second_risk = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=(
                base
                + timedelta(hours=3)
            ),
            created_at=(
                base
                + timedelta(hours=4)
            ),
            score=0.20,
        )

        db.commit()

        second_source = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert second_source is not None
        assert (
            second_source.source_record_id
            == second_risk.id
        )

        second_result = execute_alert_generation(
            db,
            source=second_source,
        )

        db.commit()

        assert second_result.risk_source_count == 1
        assert second_result.risk_candidate_count == 0
        assert second_result.anomaly_source_count == 0
        assert second_result.anomaly_candidate_count == 0
        assert second_result.alert_created_count == 0
        assert second_result.alert_existing_count == 0

        anomaly_checkpoint = (
            _checkpoint_for_organization(
                db,
                organization_id=organization.id,
                pipeline_name=(
                    PIPELINE_ALERT_ANOMALY_MATERIALIZATION_V1
                ),
            )
        )

        assert anomaly_checkpoint is not None
        assert (
            anomaly_checkpoint.id
            == anomaly_checkpoint_id
        )
        assert (
            anomaly_checkpoint.source_created_at
            == anomaly.created_at
        )
        assert (
            anomaly_checkpoint.source_record_id
            == anomaly.id
        )
        assert (
            anomaly_checkpoint.processed_at
            == anomaly_checkpoint_processed_at
        )

        anomaly_alerts = (
            db.query(
                Alert
            )
            .filter(
                Alert.organization_id
                == organization.id,
                Alert.source_type
                == "anomaly",
                Alert.source_id
                == anomaly.id,
            )
            .all()
        )

        assert len(
            anomaly_alerts
        ) == 1

        deliveries = (
            db.query(
                AlertDelivery
            )
            .filter(
                AlertDelivery.organization_id
                == organization.id,
                AlertDelivery.alert_id
                == anomaly_alerts[0].id,
                AlertDelivery.destination_id
                == webhook.id,
            )
            .all()
        )

        assert len(
            deliveries
        ) == 1

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()

def test_evidence_resolution_uses_constant_query_count():
    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            db
        )
        organization_id = organization.id

        base = datetime(
            2026,
            9,
            13,
            9,
            0,
            tzinfo=UTC,
        )

        _, first_page = _make_entity_and_page(
            db,
            organization_id=organization.id,
            suffix="EvidenceBulkFirst",
        )

        _, second_page = _make_entity_and_page(
            db,
            organization_id=organization.id,
            suffix="EvidenceBulkSecond",
        )

        first_event = _make_event(
            db,
            organization_id=organization.id,
            page=first_page,
            detected_at=base,
            suffix="evidence-bulk-first",
        )

        second_event = _make_event(
            db,
            organization_id=organization.id,
            page=second_page,
            detected_at=(
                base
                + timedelta(minutes=1)
            ),
            suffix="evidence-bulk-second",
        )

        first_risk = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=(
                base
                + timedelta(minutes=10)
            ),
            created_at=(
                base
                + timedelta(hours=1)
            ),
            score=0.90,
        )

        second_risk = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=(
                base
                + timedelta(minutes=11)
            ),
            created_at=(
                base
                + timedelta(hours=2)
            ),
            score=0.80,
        )

        first_risk.semantic_drift_event_id = (
            first_event.id
        )

        second_risk.semantic_drift_event_id = (
            second_event.id
        )

        first_anomaly = _make_anomaly(
            db,
            organization_id=organization.id,
            event=first_event,
            detected_at=(
                base
                + timedelta(minutes=20)
            ),
        )

        second_anomaly = _make_anomaly(
            db,
            organization_id=organization.id,
            event=second_event,
            detected_at=(
                base
                + timedelta(minutes=21)
            ),
        )

        db.commit()

        select_counts = {
            "semantic_drift_events": 0,
            "monitored_pages": 0,
        }

        def count_evidence_selects(
            connection,
            cursor,
            statement,
            parameters,
            context,
            executemany,
        ):
            del (
                connection,
                cursor,
                parameters,
                context,
                executemany,
            )

            normalized = (
                statement
                .lower()
                .strip()
            )

            if not normalized.startswith(
                "select"
            ):
                return

            if (
                "from semantic_drift_events"
                in normalized
            ):
                select_counts[
                    "semantic_drift_events"
                ] += 1

            if (
                "from monitored_pages"
                in normalized
            ):
                select_counts[
                    "monitored_pages"
                ] += 1

        engine = db.get_bind()

        event.listen(
            engine,
            "before_cursor_execute",
            count_evidence_selects,
        )

        try:
            (
                event_by_id,
                page_by_id,
            ) = _load_evidence_maps(
                db,
                organization_id=(
                    organization.id
                ),
                risk_sources=(
                    first_risk,
                    second_risk,
                ),
                anomalies=(
                    first_anomaly,
                    second_anomaly,
                ),
            )

        finally:
            event.remove(
                engine,
                "before_cursor_execute",
                count_evidence_selects,
            )

        assert set(
            event_by_id
        ) == {
            first_event.id,
            second_event.id,
        }

        assert set(
            page_by_id
        ) == {
            first_page.id,
            second_page.id,
        }

        assert select_counts == {
            "semantic_drift_events": 1,
            "monitored_pages": 1,
        }

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()

def test_materialized_pattern_loader_uses_created_at_not_detected_at():
    db = SessionLocal()

    try:
        organization = _make_organization(
            db
        )

        effective_time = datetime(
            2026,
            6,
            1,
            12,
            0,
            tzinfo=UTC,
        )

        checkpoint_time = datetime(
            2026,
            9,
            12,
            12,
            0,
            tzinfo=UTC,
        )

        materialized_at = datetime(
            2026,
            9,
            13,
            12,
            0,
            tzinfo=UTC,
        )

        pattern = _make_pattern(
            db,
            organization_id=organization.id,
            entity_id=uuid.uuid4(),
            detected_at=effective_time,
            created_at=materialized_at,
        )

        checkpoint = PipelineCheckpoint(
            source_created_at=checkpoint_time,
            source_record_id=uuid.uuid4(),
            knowledge_cursor=effective_time,
        )

        rows = _load_materialized_patterns(
            db,
            organization_id=organization.id,
            checkpoint=checkpoint,
        )

        assert [
            row.id
            for row in rows
        ] == [
            pattern.id
        ]

        assert (
            pattern.detected_at
            < checkpoint.source_created_at
        )

        assert (
            pattern.created_at
            > checkpoint.source_created_at
        )

    finally:
        db.rollback()
        db.close()


def test_materialized_pattern_loader_uses_uuid_tiebreaker():
    db = SessionLocal()

    try:
        organization = _make_organization(
            db
        )

        materialized_at = datetime(
            2026,
            9,
            13,
            13,
            0,
            tzinfo=UTC,
        )

        low_id = uuid.UUID(
            "11111111-1111-1111-1111-111111111111"
        )
        high_id = uuid.UUID(
            "ffffffff-ffff-ffff-ffff-ffffffffffff"
        )

        low = _make_pattern(
            db,
            organization_id=organization.id,
            entity_id=uuid.uuid4(),
            detected_at=(
                materialized_at
                - timedelta(days=10)
            ),
            created_at=materialized_at,
            pattern_id=low_id,
        )

        high = _make_pattern(
            db,
            organization_id=organization.id,
            entity_id=uuid.uuid4(),
            detected_at=(
                materialized_at
                - timedelta(days=9)
            ),
            created_at=materialized_at,
            pattern_id=high_id,
        )

        checkpoint = PipelineCheckpoint(
            source_created_at=materialized_at,
            source_record_id=low_id,
            knowledge_cursor=materialized_at,
        )

        rows = _load_materialized_patterns(
            db,
            organization_id=organization.id,
            checkpoint=checkpoint,
        )

        assert [
            row.id
            for row in rows
        ] == [
            high_id
        ]

    finally:
        db.rollback()
        db.close()


def test_materialized_anomaly_loader_uses_created_at_boundary():
    db = SessionLocal()

    try:
        organization = _make_organization(
            db
        )

        _, page = _make_entity_and_page(
            db,
            organization_id=organization.id,
            suffix="MaterializedAnomaly",
        )

        effective_time = datetime(
            2026,
            6,
            2,
            12,
            0,
            tzinfo=UTC,
        )

        checkpoint_time = datetime(
            2026,
            9,
            12,
            14,
            0,
            tzinfo=UTC,
        )

        event = _make_event(
            db,
            organization_id=organization.id,
            page=page,
            detected_at=effective_time,
            suffix="materialized-anomaly",
        )

        anomaly = _make_anomaly(
            db,
            organization_id=organization.id,
            event=event,
            detected_at=effective_time,
            created_at=(
                checkpoint_time
                + timedelta(hours=1)
            ),
        )

        checkpoint = PipelineCheckpoint(
            source_created_at=checkpoint_time,
            source_record_id=uuid.uuid4(),
            knowledge_cursor=effective_time,
        )

        rows = _load_materialized_anomalies(
            db,
            organization_id=organization.id,
            checkpoint=checkpoint,
        )

        assert [
            row.id
            for row in rows
        ] == [
            anomaly.id
        ]

        assert (
            anomaly.detected_at
            < checkpoint.source_created_at
        )

        assert (
            anomaly.created_at
            > checkpoint.source_created_at
        )

    finally:
        db.rollback()
        db.close()


def test_materialized_source_loaders_are_tenant_isolated():
    db = SessionLocal()

    try:
        first_organization = _make_organization(
            db
        )
        second_organization = _make_organization(
            db
        )

        materialized_at = datetime(
            2026,
            9,
            13,
            15,
            0,
            tzinfo=UTC,
        )

        first_pattern = _make_pattern(
            db,
            organization_id=first_organization.id,
            entity_id=uuid.uuid4(),
            detected_at=(
                materialized_at
                - timedelta(days=30)
            ),
            created_at=materialized_at,
        )

        _make_pattern(
            db,
            organization_id=second_organization.id,
            entity_id=uuid.uuid4(),
            detected_at=(
                materialized_at
                - timedelta(days=30)
            ),
            created_at=materialized_at,
        )

        _, first_page = _make_entity_and_page(
            db,
            organization_id=first_organization.id,
            suffix="MaterializedTenantA",
        )

        _, second_page = _make_entity_and_page(
            db,
            organization_id=second_organization.id,
            suffix="MaterializedTenantB",
        )

        first_event = _make_event(
            db,
            organization_id=first_organization.id,
            page=first_page,
            detected_at=(
                materialized_at
                - timedelta(days=20)
            ),
            suffix="materialized-tenant-a",
        )

        second_event = _make_event(
            db,
            organization_id=second_organization.id,
            page=second_page,
            detected_at=(
                materialized_at
                - timedelta(days=20)
            ),
            suffix="materialized-tenant-b",
        )

        first_anomaly = _make_anomaly(
            db,
            organization_id=first_organization.id,
            event=first_event,
            detected_at=first_event.detected_at,
            created_at=materialized_at,
        )

        _make_anomaly(
            db,
            organization_id=second_organization.id,
            event=second_event,
            detected_at=second_event.detected_at,
            created_at=materialized_at,
        )

        patterns = _load_materialized_patterns(
            db,
            organization_id=first_organization.id,
            checkpoint=None,
        )

        anomalies = _load_materialized_anomalies(
            db,
            organization_id=first_organization.id,
            checkpoint=None,
        )

        assert [
            row.id
            for row in patterns
        ] == [
            first_pattern.id
        ]

        assert [
            row.id
            for row in anomalies
        ] == [
            first_anomaly.id
        ]

    finally:
        db.rollback()
        db.close()

def test_delivery_reconciliation_scans_existing_alerts_in_keyset_batches(
    monkeypatch,
):
    import src.services.alert_execution_service as execution_module

    db = SessionLocal()
    organization_id = None
    other_organization_id = None

    try:
        organization = _make_organization(
            db
        )
        organization_id = organization.id

        other_organization = _make_organization(
            db
        )
        other_organization_id = other_organization.id

        base = datetime(
            2026,
            9,
            13,
            8,
            0,
            tzinfo=UTC,
        )

        for index in range(5):
            db.add(
                Alert(
                    organization_id=organization.id,
                    event_id=None,
                    source_type="pattern",
                    source_id=uuid.uuid4(),
                    detection_key=(
                        f"{organization.id.hex}"
                        f"{index:02d}"
                    )[:64],
                    alert_type="pattern_detected",
                    title=f"Existing alert {index}",
                    severity=None,
                    occurred_at=(
                        base
                        + timedelta(minutes=index)
                    ),
                    created_at=(
                        base
                        + timedelta(minutes=index)
                    ),
                )
            )

        db.add(
            Alert(
                organization_id=other_organization.id,
                event_id=None,
                source_type="pattern",
                source_id=uuid.uuid4(),
                detection_key=(
                    f"{other_organization.id.hex}"
                    "other"
                )[:64],
                alert_type="pattern_detected",
                title="Other tenant alert",
                severity=None,
                occurred_at=base,
                created_at=base,
            )
        )

        webhook = _make_webhook(
            db,
            organization_id=organization.id,
            events=["alert.created"],
        )

        source_risk = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=base + timedelta(hours=1),
            created_at=base + timedelta(hours=2),
            score=0.20,
        )

        db.commit()

        monkeypatch.setattr(
            execution_module,
            "ALERT_DELIVERY_RECONCILIATION_BATCH_SIZE",
            2,
        )

        source = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert source is not None
        assert source.source_record_id == source_risk.id

        execute_alert_generation(
            db,
            source=source,
        )

        deliveries = (
            db.query(AlertDelivery)
            .filter(
                AlertDelivery.organization_id
                == organization.id,
                AlertDelivery.destination_id
                == webhook.id,
            )
            .all()
        )

        assert len(deliveries) == 5

        other_tenant_deliveries = (
            db.query(AlertDelivery)
            .filter(
                AlertDelivery.organization_id
                == other_organization.id
            )
            .count()
        )

        assert other_tenant_deliveries == 0

        db.commit()

    finally:
        try:
            if organization_id is not None:
                _cleanup(
                    db,
                    organization_id,
                )

            if other_organization_id is not None:
                _cleanup(
                    db,
                    other_organization_id,
                )
        finally:
            db.close()

def test_delivery_reconciliation_keyset_uses_alert_id_tiebreaker(
    monkeypatch,
):
    import src.services.alert_execution_service as execution_module

    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            db
        )
        organization_id = organization.id

        base = datetime(
            2026,
            9,
            13,
            9,
            0,
            tzinfo=UTC,
        )

        alert_ids = sorted(
            (
                uuid.uuid4(),
                uuid.uuid4(),
                uuid.uuid4(),
            ),
            key=lambda value: value.int,
        )

        for index, alert_id in enumerate(alert_ids):
            db.add(
                Alert(
                    id=alert_id,
                    organization_id=organization.id,
                    event_id=None,
                    source_type="pattern",
                    source_id=uuid.uuid4(),
                    detection_key=(
                        f"{organization.id.hex}"
                        f"tie{index}"
                    )[:64],
                    alert_type="pattern_detected",
                    title=f"Tie alert {index}",
                    severity=None,
                    occurred_at=base,
                    created_at=base,
                )
            )

        webhook = _make_webhook(
            db,
            organization_id=organization.id,
            events=["alert.created"],
        )

        source_risk = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=base + timedelta(hours=1),
            created_at=base + timedelta(hours=2),
            score=0.20,
        )

        db.commit()

        monkeypatch.setattr(
            execution_module,
            "ALERT_DELIVERY_RECONCILIATION_BATCH_SIZE",
            1,
        )

        source = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert source is not None
        assert source.source_record_id == source_risk.id

        execute_alert_generation(
            db,
            source=source,
        )

        delivered_alert_ids = {
            delivery.alert_id
            for delivery in (
                db.query(AlertDelivery)
                .filter(
                    AlertDelivery.organization_id
                    == organization.id,
                    AlertDelivery.destination_id
                    == webhook.id,
                )
                .all()
            )
        }

        assert delivered_alert_ids == set(
            alert_ids
        )

        db.commit()

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()

def test_delivery_reconciliation_scans_webhooks_in_keyset_batches(
    monkeypatch,
):
    import src.services.alert_execution_service as execution_module

    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            db
        )
        organization_id = organization.id

        base = datetime(
            2026,
            9,
            14,
            8,
            0,
            tzinfo=UTC,
        )

        alert = Alert(
            organization_id=organization.id,
            event_id=None,
            source_type="pattern",
            source_id=uuid.uuid4(),
            detection_key=uuid.uuid4().hex,
            alert_type="pattern_detected",
            title="Existing alert",
            severity=None,
            occurred_at=base,
            created_at=base,
        )
        db.add(
            alert
        )

        shared_created_at = (
            base
            + timedelta(hours=1)
        )
        webhook_ids = sorted(
            (
                uuid.uuid4()
                for _ in range(5)
            ),
            key=lambda value: value.int,
        )

        for index, webhook_id in enumerate(
            webhook_ids
        ):
            db.add(
                WebhookSubscription(
                    id=webhook_id,
                    organization_id=organization.id,
                    url=(
                        "https://example.test/"
                        f"webhook-batch-{index}"
                    ),
                    events=["alert.created"],
                    secret_ciphertext=b"integration-test-ciphertext",
                    secret_nonce=b"0123456789ab",
                    secret_encryption_version=1,
                    is_active=True,
                    created_at=shared_created_at,
                )
            )

        source_risk = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=(
                base
                + timedelta(hours=2)
            ),
            created_at=(
                base
                + timedelta(hours=3)
            ),
            score=0.20,
        )

        db.commit()

        monkeypatch.setattr(
            execution_module,
            "ALERT_WEBHOOK_RECONCILIATION_BATCH_SIZE",
            2,
        )

        original_loader = (
            execution_module
            ._load_webhook_reconciliation_batch
        )
        loaded_batches = []

        def record_webhook_batch(
            db,
            *,
            organization_id,
            after_created_at,
            after_id,
            limit,
        ):
            batch = original_loader(
                db,
                organization_id=organization_id,
                after_created_at=after_created_at,
                after_id=after_id,
                limit=limit,
            )
            loaded_batches.append(
                tuple(
                    subscription.id
                    for subscription in batch
                )
            )
            return batch

        monkeypatch.setattr(
            execution_module,
            "_load_webhook_reconciliation_batch",
            record_webhook_batch,
        )

        source = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert source is not None
        assert source.source_record_id == source_risk.id

        execute_alert_generation(
            db,
            source=source,
        )

        assert loaded_batches == [
            tuple(webhook_ids[:2]),
            tuple(webhook_ids[2:4]),
            tuple(webhook_ids[4:]),
            (),
        ]

        delivered_webhook_ids = {
            delivery.destination_id
            for delivery in (
                db.query(AlertDelivery)
                .filter(
                    AlertDelivery.organization_id
                    == organization.id,
                    AlertDelivery.alert_id
                    == alert.id,
                )
                .all()
            )
        }

        assert delivered_webhook_ids == set(
            webhook_ids
        )

        delivery_checkpoint = (
            _checkpoint_for_organization(
                db,
                organization_id=organization.id,
                pipeline_name=(
                    PIPELINE_ALERT_DELIVERY_RECONCILIATION_V1
                ),
            )
        )

        assert delivery_checkpoint is not None
        assert (
            delivery_checkpoint.source_created_at
            == shared_created_at
        )
        assert (
            delivery_checkpoint.source_record_id
            == webhook_ids[-1]
        )
        assert (
            delivery_checkpoint.metadata_json[
                "source_count"
            ]
            == 5
        )

        db.commit()

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_inactive_webhook_in_batched_reconciliation_advances_checkpoint(
    monkeypatch,
):
    import src.services.alert_execution_service as execution_module

    db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            db
        )
        organization_id = organization.id

        base = datetime(
            2026,
            9,
            14,
            9,
            0,
            tzinfo=UTC,
        )

        alert = Alert(
            organization_id=organization.id,
            event_id=None,
            source_type="pattern",
            source_id=uuid.uuid4(),
            detection_key=uuid.uuid4().hex,
            alert_type="pattern_detected",
            title="Existing alert",
            severity=None,
            occurred_at=base,
            created_at=base,
        )
        db.add(
            alert
        )

        shared_created_at = (
            base
            + timedelta(hours=1)
        )
        webhook_ids = sorted(
            (
                uuid.uuid4(),
                uuid.uuid4(),
            ),
            key=lambda value: value.int,
        )

        active_webhook = WebhookSubscription(
            id=webhook_ids[0],
            organization_id=organization.id,
            url=(
                "https://example.test/"
                "active-batched-webhook"
            ),
            events=["alert.created"],
            secret_ciphertext=b"integration-test-ciphertext",
            secret_nonce=b"0123456789ab",
            secret_encryption_version=1,
            is_active=True,
            created_at=shared_created_at,
        )
        inactive_webhook = WebhookSubscription(
            id=webhook_ids[1],
            organization_id=organization.id,
            url=(
                "https://example.test/"
                "inactive-batched-webhook"
            ),
            events=["alert.created"],
            secret_ciphertext=b"integration-test-ciphertext",
            secret_nonce=b"0123456789ab",
            secret_encryption_version=1,
            is_active=False,
            created_at=shared_created_at,
        )

        db.add_all(
            (
                active_webhook,
                inactive_webhook,
            )
        )

        source_risk = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=(
                base
                + timedelta(hours=2)
            ),
            created_at=(
                base
                + timedelta(hours=3)
            ),
            score=0.20,
        )

        db.commit()

        monkeypatch.setattr(
            execution_module,
            "ALERT_WEBHOOK_RECONCILIATION_BATCH_SIZE",
            1,
        )

        source = discover_pipeline_source(
            db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert source is not None
        assert source.source_record_id == source_risk.id

        execute_alert_generation(
            db,
            source=source,
        )

        active_delivery_count = (
            db.query(AlertDelivery)
            .filter(
                AlertDelivery.organization_id
                == organization.id,
                AlertDelivery.alert_id
                == alert.id,
                AlertDelivery.destination_id
                == active_webhook.id,
            )
            .count()
        )
        inactive_delivery_count = (
            db.query(AlertDelivery)
            .filter(
                AlertDelivery.organization_id
                == organization.id,
                AlertDelivery.alert_id
                == alert.id,
                AlertDelivery.destination_id
                == inactive_webhook.id,
            )
            .count()
        )

        assert active_delivery_count == 1
        assert inactive_delivery_count == 0

        delivery_checkpoint = (
            _checkpoint_for_organization(
                db,
                organization_id=organization.id,
                pipeline_name=(
                    PIPELINE_ALERT_DELIVERY_RECONCILIATION_V1
                ),
            )
        )

        assert delivery_checkpoint is not None
        assert (
            delivery_checkpoint.source_created_at
            == shared_created_at
        )
        assert (
            delivery_checkpoint.source_record_id
            == inactive_webhook.id
        )
        assert (
            delivery_checkpoint.metadata_json[
                "source_count"
            ]
            == 2
        )

        db.commit()

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_webhook_reconciliation_later_batch_failure_rolls_back(
    monkeypatch,
):
    import src.services.alert_execution_service as execution_module

    setup_db = SessionLocal()
    execution_db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            setup_db
        )
        organization_id = organization.id

        base = datetime(
            2026,
            9,
            14,
            10,
            0,
            tzinfo=UTC,
        )

        alert = Alert(
            organization_id=organization.id,
            event_id=None,
            source_type="pattern",
            source_id=uuid.uuid4(),
            detection_key=uuid.uuid4().hex,
            alert_type="pattern_detected",
            title="Existing alert",
            severity=None,
            occurred_at=base,
            created_at=base,
        )
        setup_db.add(
            alert
        )

        shared_created_at = (
            base
            + timedelta(hours=1)
        )
        webhook_ids = sorted(
            (
                uuid.uuid4(),
                uuid.uuid4(),
                uuid.uuid4(),
            ),
            key=lambda value: value.int,
        )

        for index, webhook_id in enumerate(
            webhook_ids
        ):
            setup_db.add(
                WebhookSubscription(
                    id=webhook_id,
                    organization_id=organization.id,
                    url=(
                        "https://example.test/"
                        f"rollback-webhook-{index}"
                    ),
                    events=["alert.created"],
                    secret_ciphertext=b"integration-test-ciphertext",
                    secret_nonce=b"0123456789ab",
                    secret_encryption_version=1,
                    is_active=True,
                    created_at=shared_created_at,
                )
            )

        source_risk = _make_risk_assessment(
            setup_db,
            organization_id=organization.id,
            assessed_at=(
                base
                + timedelta(hours=2)
            ),
            created_at=(
                base
                + timedelta(hours=3)
            ),
            score=0.20,
        )

        setup_db.commit()

        source = discover_pipeline_source(
            execution_db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert source is not None
        assert source.source_record_id == source_risk.id

        monkeypatch.setattr(
            execution_module,
            "ALERT_WEBHOOK_RECONCILIATION_BATCH_SIZE",
            1,
        )

        original_reconcile_alert_deliveries = (
            execution_module.reconcile_alert_deliveries
        )
        reconciliation_call_count = 0

        def fail_second_webhook_batch(
            db,
            *,
            organization_id,
            alerts,
            subscriptions,
        ):
            nonlocal reconciliation_call_count
            reconciliation_call_count += 1

            if reconciliation_call_count == 2:
                raise RuntimeError(
                    "forced webhook reconciliation batch failure"
                )

            return original_reconcile_alert_deliveries(
                db,
                organization_id=organization_id,
                alerts=alerts,
                subscriptions=subscriptions,
            )

        monkeypatch.setattr(
            execution_module,
            "reconcile_alert_deliveries",
            fail_second_webhook_batch,
        )

        with pytest.raises(
            RuntimeError,
            match=(
                "forced webhook reconciliation batch failure"
            ),
        ):
            execute_alert_generation(
                execution_db,
                source=source,
            )

        assert reconciliation_call_count == 2

        first_batch_delivery_count = (
            execution_db.query(AlertDelivery)
            .filter(
                AlertDelivery.organization_id
                == organization.id,
                AlertDelivery.alert_id
                == alert.id,
                AlertDelivery.destination_id
                == webhook_ids[0],
            )
            .count()
        )

        assert first_batch_delivery_count == 1

        delivery_checkpoint_inside_transaction = (
            _checkpoint_for_organization(
                execution_db,
                organization_id=organization.id,
                pipeline_name=(
                    PIPELINE_ALERT_DELIVERY_RECONCILIATION_V1
                ),
            )
        )

        assert (
            delivery_checkpoint_inside_transaction
            is None
        )

        execution_db.rollback()

        verify_db = SessionLocal()

        try:
            persisted_delivery_count = (
                verify_db.query(AlertDelivery)
                .filter(
                    AlertDelivery.organization_id
                    == organization.id,
                    AlertDelivery.alert_id
                    == alert.id,
                    AlertDelivery.destination_id.in_(
                        webhook_ids
                    ),
                )
                .count()
            )

            assert persisted_delivery_count == 0

            persisted_delivery_checkpoint = (
                _checkpoint_for_organization(
                    verify_db,
                    organization_id=organization.id,
                    pipeline_name=(
                        PIPELINE_ALERT_DELIVERY_RECONCILIATION_V1
                    ),
                )
            )

            assert persisted_delivery_checkpoint is None

        finally:
            verify_db.close()

    finally:
        execution_db.rollback()
        execution_db.close()

        try:
            _cleanup(
                setup_db,
                organization_id,
            )
        finally:
            setup_db.close()

def test_alert_policy_reconciliation_later_batch_failure_rolls_back(
    monkeypatch,
):
    setup_db = SessionLocal()
    execution_db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            setup_db
        )
        organization_id = organization.id

        base = datetime(
            2026,
            9,
            10,
            11,
            25,
            tzinfo=UTC,
        )

        entity_id = uuid.uuid4()
        shared_created_at = (
            base
            + timedelta(hours=1)
        )

        pattern_ids = sorted(
            (
                uuid.uuid4()
                for _ in range(5)
            ),
            key=lambda value: value.int,
        )

        for index, pattern_id in enumerate(
            pattern_ids
        ):
            _make_pattern(
                setup_db,
                organization_id=organization.id,
                entity_id=entity_id,
                detected_at=(
                    base
                    + timedelta(minutes=index)
                ),
                created_at=shared_created_at,
                pattern_id=pattern_id,
            )

        source_risk = _make_risk_assessment(
            setup_db,
            organization_id=organization.id,
            assessed_at=(
                base
                + timedelta(hours=2)
            ),
            created_at=(
                base
                + timedelta(hours=3)
            ),
            score=0.20,
        )

        _make_watchlist(
            setup_db,
            organization_id=organization.id,
            entity_ids=[],
            alert_on_anomaly=False,
        )

        setup_db.commit()

        initial_source = discover_pipeline_source(
            setup_db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert initial_source is not None
        assert (
            initial_source.source_record_id
            == source_risk.id
        )

        initial_result = execute_alert_generation(
            setup_db,
            source=initial_source,
        )

        assert initial_result.pattern_source_count == 5
        assert initial_result.pattern_candidate_count == 5
        assert initial_result.alert_created_count == 5

        setup_db.commit()

        revision = bump_alert_policy_revision(
            setup_db,
            organization_id=organization.id,
        )

        assert revision.policy_revision == 1
        assert revision.reconciled_revision == 0
        assert revision.reconciliation_required is True

        setup_db.commit()

        normal_source = discover_pipeline_source(
            execution_db,
            organization_id=organization.id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )

        assert normal_source is None

        source = discover_alert_reconciliation_source(
            execution_db,
            organization_id=organization.id,
        )

        assert source is not None
        assert (
            source.pipeline_name
            == PIPELINE_ALERT_GENERATION_V1
        )
        assert (
            source.source_record_id
            == source_risk.id
        )

        import src.services.alert_execution_service as execution_module

        monkeypatch.setattr(
            execution_module,
            "ALERT_POLICY_RECONCILIATION_BATCH_SIZE",
            2,
        )

        original_loader = (
            execution_module
            ._load_visible_pattern_reconciliation_batch
        )

        loader_call_count = 0

        def fail_second_pattern_batch(
            db,
            *,
            organization_id,
            temporal_cursor,
            after_created_at,
            after_id,
            limit,
        ):
            nonlocal loader_call_count
            loader_call_count += 1

            if loader_call_count == 2:
                raise RuntimeError(
                    "forced policy reconciliation batch failure"
                )

            return original_loader(
                db,
                organization_id=organization_id,
                temporal_cursor=temporal_cursor,
                after_created_at=after_created_at,
                after_id=after_id,
                limit=limit,
            )

        monkeypatch.setattr(
            execution_module,
            "_load_visible_pattern_reconciliation_batch",
            fail_second_pattern_batch,
        )

        with pytest.raises(
            RuntimeError,
            match="forced policy reconciliation batch failure",
        ):
            execute_alert_generation(
                execution_db,
                source=source,
            )

        assert loader_call_count == 2

        revision_inside_transaction = (
            load_alert_policy_revision(
                execution_db,
                organization_id=organization.id,
            )
        )

        assert revision_inside_transaction is not None
        assert (
            revision_inside_transaction.policy_revision
            == 1
        )
        assert (
            revision_inside_transaction.reconciled_revision
            == 0
        )
        assert (
            revision_inside_transaction.reconciliation_required
            is True
        )

        execution_db.rollback()

        verify_db = SessionLocal()

        try:
            persisted_revision = (
                load_alert_policy_revision(
                    verify_db,
                    organization_id=organization.id,
                )
            )

            assert persisted_revision is not None
            assert (
                persisted_revision.policy_revision
                == 1
            )
            assert (
                persisted_revision.reconciled_revision
                == 0
            )
            assert (
                persisted_revision.reconciliation_required
                is True
            )
            assert persisted_revision.reconciled_at is None

            persisted_alerts = (
                _alerts_for_organization(
                    verify_db,
                    organization_id=organization.id,
                )
            )

            assert len(
                persisted_alerts
            ) == 5

        finally:
            verify_db.close()

    finally:
        execution_db.rollback()
        execution_db.close()

        try:
            _cleanup(
                setup_db,
                organization_id,
            )
        finally:
            setup_db.close()
