from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid

from sqlalchemy import delete

from scripts.vector_worker import VectorWorker
from src.core.database import SessionLocal
from src.models.alert import Alert
from src.models.alert_delivery import AlertDelivery
from src.models.organization import Organization
from src.models.organization_alert_policy_revision import (
    OrganizationAlertPolicyRevision,
)
from src.models.pipeline_checkpoint import (
    PIPELINE_ALERT_DELIVERY_RECONCILIATION_V1,
    PIPELINE_ALERT_GENERATION_V1,
    PIPELINE_ANOMALY_DETECTION_V1,
    PIPELINE_PATTERN_DETECTION_V1,
    PipelineCheckpoint,
)
from src.models.risk_assessment import RiskAssessment
from src.models.watchlist import Watchlist
from src.models.webhook_subscription import WebhookSubscription
from src.services.alert_policy_revision_service import (
    bump_alert_policy_revision,
    load_alert_policy_revision,
)
from src.services.risk_assessment_service import (
    RISK_VERSION_V2,
)


UTC = timezone.utc


def _make_organization(db):
    suffix = uuid.uuid4().hex[:10]

    organization = Organization(
        id=uuid.uuid4(),
        name=f"worker-intelligence-{suffix}",
        slug=f"worker-intelligence-{suffix}",
        billing_email=f"{suffix}@example.com",
    )

    db.add(organization)
    db.flush()

    return organization


def _make_risk_assessment(
    db,
    *,
    organization_id,
    assessed_at,
    created_at,
    score=0.90,
):
    assessment = RiskAssessment(
        id=uuid.uuid4(),
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

    db.add(assessment)
    db.flush()

    return assessment


def _checkpoint(
    db,
    *,
    organization_id,
    pipeline_name,
):
    return (
        db.query(PipelineCheckpoint)
        .filter(
            PipelineCheckpoint.organization_id
            == organization_id,
            PipelineCheckpoint.pipeline_name
            == pipeline_name,
        )
        .one_or_none()
    )


def _alerts(
    db,
    *,
    organization_id,
):
    return (
        db.query(Alert)
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

def _make_watchlist(
    db,
    *,
    organization_id,
):
    watchlist = Watchlist(
        organization_id=organization_id,
        name=(
            "Worker reconciliation "
            f"{uuid.uuid4().hex[:10]}"
        ),
        entity_ids=[],
        page_ids=[],
        categories=[],
        alert_on_high_risk=True,
        alert_on_medium_risk=False,
        alert_on_pattern=True,
        alert_on_anomaly=True,
        is_active=True,
        is_archived=False,
    )

    db.add(watchlist)
    db.flush()

    return watchlist


def _make_webhook(
    db,
    *,
    organization_id,
    events,
):
    subscription = WebhookSubscription(
        id=uuid.uuid4(),
        organization_id=organization_id,
        url="https://example.test/webhook",
        events=events,
        secret_ciphertext=b"integration-test-ciphertext",
        secret_nonce=b"0123456789ab",
        secret_encryption_version=1,
        is_active=True,
    )

    db.add(subscription)
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
        delete(AlertDelivery).where(
            AlertDelivery.organization_id
            == organization_id
        )
    )

    db.execute(
        delete(WebhookSubscription).where(
            WebhookSubscription.organization_id
            == organization_id
        )
    )

    db.execute(
        delete(Watchlist).where(
            Watchlist.organization_id
            == organization_id
        )
    )

    db.execute(
        delete(
            OrganizationAlertPolicyRevision
        ).where(
            OrganizationAlertPolicyRevision.organization_id
            == organization_id
        )
    )
    db.execute(
        delete(Alert).where(
            Alert.organization_id
            == organization_id
        )
    )

    db.execute(
        delete(PipelineCheckpoint).where(
            PipelineCheckpoint.organization_id
            == organization_id
        )
    )

    db.execute(
        delete(RiskAssessment).where(
            RiskAssessment.organization_id
            == organization_id
        )
    )

    db.execute(
        delete(Organization).where(
            Organization.id
            == organization_id
        )
    )

    db.commit()


def _worker() -> VectorWorker:
    return VectorWorker(
        snapshot_limit=1,
        event_limit=1,
        risk_limit=1,
        pattern_limit=10,
        anomaly_limit=10,
        max_attempts=1,
        process_embeddings=False,
        process_analysis=False,
        process_risk_scoring=False,
        process_pattern_detection=True,
        process_anomaly_detection=True,
    )


def test_worker_executes_real_intelligence_pipeline_and_commits():
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

        risk = _make_risk_assessment(
            setup_db,
            organization_id=organization.id,
            assessed_at=base,
            created_at=(
                base
                + timedelta(hours=1)
            ),
            score=0.90,
        )

        risk_id = risk.id

        setup_db.commit()

        worker = _worker()

        stats = (
            worker.process_intelligence_organizations()
        )

        assert stats.selected == 1
        assert stats.completed == 1
        assert stats.failed == 0
        assert stats.deferred == 0

        verify_db = SessionLocal()

        try:
            pattern_checkpoint = _checkpoint(
                verify_db,
                organization_id=organization_id,
                pipeline_name=(
                    PIPELINE_PATTERN_DETECTION_V1
                ),
            )

            alert_checkpoint = _checkpoint(
                verify_db,
                organization_id=organization_id,
                pipeline_name=(
                    PIPELINE_ALERT_GENERATION_V1
                ),
            )

            anomaly_checkpoint = _checkpoint(
                verify_db,
                organization_id=organization_id,
                pipeline_name=(
                    PIPELINE_ANOMALY_DETECTION_V1
                ),
            )

            assert pattern_checkpoint is not None
            assert (
                pattern_checkpoint.source_record_id
                == risk_id
            )

            assert alert_checkpoint is not None
            assert (
                alert_checkpoint.source_record_id
                == risk_id
            )

            assert anomaly_checkpoint is None

            alerts = _alerts(
                verify_db,
                organization_id=organization_id,
            )

            assert len(alerts) == 1
            assert alerts[0].source_id == risk_id

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


def test_worker_second_run_is_not_pending_and_is_idempotent():
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
            6,
            0,
            tzinfo=UTC,
        )

        risk = _make_risk_assessment(
            setup_db,
            organization_id=organization.id,
            assessed_at=base,
            created_at=(
                base
                + timedelta(hours=1)
            ),
            score=0.90,
        )

        risk_id = risk.id

        setup_db.commit()

        worker = _worker()

        first = (
            worker.process_intelligence_organizations()
        )

        assert first.selected == 1
        assert first.completed == 1
        assert first.failed == 0

        second = (
            worker.process_intelligence_organizations()
        )

        assert second.selected == 0
        assert second.completed == 0
        assert second.failed == 0
        assert second.deferred == 0

        verify_db = SessionLocal()

        try:
            alerts = _alerts(
                verify_db,
                organization_id=organization_id,
            )

            assert len(alerts) == 1
            assert alerts[0].source_id == risk_id

            assert (
                _checkpoint(
                    verify_db,
                    organization_id=organization_id,
                    pipeline_name=(
                        PIPELINE_PATTERN_DETECTION_V1
                    ),
                )
                is not None
            )

            assert (
                _checkpoint(
                    verify_db,
                    organization_id=organization_id,
                    pipeline_name=(
                        PIPELINE_ALERT_GENERATION_V1
                    ),
                )
                is not None
            )

            assert (
                _checkpoint(
                    verify_db,
                    organization_id=organization_id,
                    pipeline_name=(
                        PIPELINE_ANOMALY_DETECTION_V1
                    ),
                )
                is None
            )

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

def test_worker_reconciles_pending_policy_without_new_risk():
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
            14,
            1,
            0,
            tzinfo=UTC,
        )

        risk = _make_risk_assessment(
            setup_db,
            organization_id=organization.id,
            assessed_at=base,
            created_at=(
                base
                + timedelta(hours=1)
            ),
            score=0.90,
        )

        risk_id = risk.id

        setup_db.commit()

        worker = _worker()

        initial = (
            worker.process_intelligence_organizations()
        )

        assert initial.selected == 1
        assert initial.completed == 1
        assert initial.failed == 0

        verify_initial_db = SessionLocal()

        try:
            alert_checkpoint = _checkpoint(
                verify_initial_db,
                organization_id=organization_id,
                pipeline_name=(
                    PIPELINE_ALERT_GENERATION_V1
                ),
            )

            assert alert_checkpoint is not None
            assert (
                alert_checkpoint.source_record_id
                == risk_id
            )

            initial_alerts = _alerts(
                verify_initial_db,
                organization_id=organization_id,
            )

            assert len(initial_alerts) == 1

        finally:
            verify_initial_db.close()

        mutation_db = SessionLocal()

        try:
            _make_watchlist(
                mutation_db,
                organization_id=organization_id,
            )

            revision = bump_alert_policy_revision(
                mutation_db,
                organization_id=organization_id,
            )

            assert revision.policy_revision == 1
            assert revision.reconciled_revision == 0
            assert (
                revision.reconciliation_required
                is True
            )

            mutation_db.commit()

        finally:
            mutation_db.close()

        no_new_intelligence = (
            worker.process_intelligence_organizations()
        )

        assert no_new_intelligence.selected == 0
        assert no_new_intelligence.completed == 0
        assert no_new_intelligence.failed == 0

        reconciliation = (
            worker.process_alert_reconciliation_organizations()
        )

        assert reconciliation.selected == 1
        assert reconciliation.completed == 1
        assert reconciliation.failed == 0
        assert reconciliation.deferred == 0

        verify_db = SessionLocal()

        try:
            persisted_revision = (
                load_alert_policy_revision(
                    verify_db,
                    organization_id=organization_id,
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

            alerts = _alerts(
                verify_db,
                organization_id=organization_id,
            )

            assert len(alerts) == 1
            assert alerts[0].source_id == risk_id

            alert_checkpoint = _checkpoint(
                verify_db,
                organization_id=organization_id,
                pipeline_name=(
                    PIPELINE_ALERT_GENERATION_V1
                ),
            )

            assert alert_checkpoint is not None
            assert (
                alert_checkpoint.source_record_id
                == risk_id
            )

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

def test_failed_alert_reconciliation_organization_does_not_starve_later_ready_organization(
    monkeypatch,
):
    setup_db = SessionLocal()
    organization_ids = []

    try:
        poison_organization = _make_organization(
            setup_db
        )
        healthy_organization = _make_organization(
            setup_db
        )
        organization_ids.extend(
            [
                poison_organization.id,
                healthy_organization.id,
            ]
        )

        poison_base = datetime(
            2036,
            9,
            14,
            3,
            0,
            tzinfo=UTC,
        )
        healthy_base = (
            poison_base
            + timedelta(days=1)
        )

        _make_risk_assessment(
            setup_db,
            organization_id=poison_organization.id,
            assessed_at=poison_base,
            created_at=poison_base,
            score=0.90,
        )
        _make_risk_assessment(
            setup_db,
            organization_id=healthy_organization.id,
            assessed_at=healthy_base,
            created_at=healthy_base,
            score=0.90,
        )

        _make_watchlist(
            setup_db,
            organization_id=poison_organization.id,
        )
        _make_watchlist(
            setup_db,
            organization_id=healthy_organization.id,
        )

        bump_alert_policy_revision(
            setup_db,
            organization_id=poison_organization.id,
        )
        bump_alert_policy_revision(
            setup_db,
            organization_id=healthy_organization.id,
        )

        setup_db.commit()

        import scripts.vector_worker as worker_module

        real_execute_alert_generation = (
            worker_module.execute_alert_generation
        )

        def fail_poison_organization(
            execution_db,
            *,
            source,
        ):
            if (
                source.organization_id
                == poison_organization.id
            ):
                raise RuntimeError(
                    "forced alert reconciliation failure"
                )

            return real_execute_alert_generation(
                execution_db,
                source=source,
            )

        monkeypatch.setattr(
            worker_module,
            "execute_alert_generation",
            fail_poison_organization,
        )

        worker = _worker()
        worker.alert_reconciliation_limit = 1

        first_stats = (
            worker.process_alert_reconciliation_organizations()
        )

        assert first_stats.selected == 1
        assert first_stats.completed == 0
        assert first_stats.failed == 1
        assert first_stats.deferred == 0

        second_stats = (
            worker.process_alert_reconciliation_organizations()
        )

        assert second_stats.selected == 1
        assert second_stats.completed == 1
        assert second_stats.failed == 0
        assert second_stats.deferred == 0

        verify_db = SessionLocal()

        try:
            poison_revision = (
                load_alert_policy_revision(
                    verify_db,
                    organization_id=poison_organization.id,
                )
            )
            healthy_revision = (
                load_alert_policy_revision(
                    verify_db,
                    organization_id=healthy_organization.id,
                )
            )

            assert poison_revision is not None
            assert (
                poison_revision.reconciliation_required
                is True
            )

            assert healthy_revision is not None
            assert (
                healthy_revision.reconciliation_required
                is False
            )

        finally:
            verify_db.close()

    finally:
        try:
            for organization_id in organization_ids:
                _cleanup(
                    setup_db,
                    organization_id,
                )
        finally:
            setup_db.close()


def test_worker_reconciles_new_webhook_without_new_risk():
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
            14,
            3,
            0,
            tzinfo=UTC,
        )

        risk = _make_risk_assessment(
            setup_db,
            organization_id=organization.id,
            assessed_at=base,
            created_at=(
                base
                + timedelta(hours=1)
            ),
            score=0.90,
        )

        risk_id = risk.id

        setup_db.commit()

        worker = _worker()

        initial = (
            worker.process_intelligence_organizations()
        )

        assert initial.selected == 1
        assert initial.completed == 1
        assert initial.failed == 0

        verify_initial_db = SessionLocal()

        try:
            alerts = _alerts(
                verify_initial_db,
                organization_id=organization_id,
            )

            assert len(alerts) == 1
            alert_id = alerts[0].id

            assert (
                verify_initial_db.query(
                    AlertDelivery
                )
                .filter(
                    AlertDelivery.organization_id
                    == organization_id,
                    AlertDelivery.alert_id
                    == alert_id,
                )
                .count()
                == 0
            )

        finally:
            verify_initial_db.close()

        webhook_db = SessionLocal()

        try:
            webhook = _make_webhook(
                webhook_db,
                organization_id=organization_id,
                events=["high_risk"],
            )

            webhook_id = webhook.id

            webhook_db.commit()
            webhook_created_at = webhook.created_at

        finally:
            webhook_db.close()

        no_new_intelligence = (
            worker.process_intelligence_organizations()
        )

        assert no_new_intelligence.selected == 0
        assert no_new_intelligence.completed == 0
        assert no_new_intelligence.failed == 0

        reconciliation = (
            worker.process_alert_reconciliation_organizations()
        )

        assert reconciliation.selected == 1
        assert reconciliation.completed == 1
        assert reconciliation.failed == 0
        assert reconciliation.deferred == 0

        verify_db = SessionLocal()

        try:
            alerts = _alerts(
                verify_db,
                organization_id=organization_id,
            )

            assert len(alerts) == 1
            assert alerts[0].id == alert_id
            assert alerts[0].source_id == risk_id

            delivery = (
                verify_db.query(
                    AlertDelivery
                )
                .filter(
                    AlertDelivery.organization_id
                    == organization_id,
                    AlertDelivery.alert_id
                    == alert_id,
                    AlertDelivery.destination_id
                    == webhook_id,
                )
                .one_or_none()
            )

            assert delivery is not None

            delivery_checkpoint = _checkpoint(
                verify_db,
                organization_id=organization_id,
                pipeline_name=(
                    PIPELINE_ALERT_DELIVERY_RECONCILIATION_V1
                ),
            )

            assert delivery_checkpoint is not None
            assert (
                delivery_checkpoint.source_record_id
                == webhook_id
            )
            assert (
                delivery_checkpoint.source_created_at
                == webhook_created_at
            )

            alert_checkpoint = _checkpoint(
                verify_db,
                organization_id=organization_id,
                pipeline_name=(
                    PIPELINE_ALERT_GENERATION_V1
                ),
            )

            assert alert_checkpoint is not None
            assert (
                alert_checkpoint.source_record_id
                == risk_id
            )

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