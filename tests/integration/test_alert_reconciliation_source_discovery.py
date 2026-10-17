from __future__ import annotations

import uuid
from datetime import (
    datetime,
    timedelta,
    timezone,
)

from sqlalchemy import delete

from src.core.database import SessionLocal
from src.models.organization import Organization
from src.models.organization_alert_policy_revision import (
    OrganizationAlertPolicyRevision,
)
from src.models.organization_work_schedule import (
    WORK_KIND_ALERT_RECONCILIATION,
    WORK_KIND_INTELLIGENCE,
    OrganizationWorkSchedule,
)
from src.models.pipeline_checkpoint import (
    PIPELINE_ALERT_DELIVERY_RECONCILIATION_V1,
    PIPELINE_ALERT_GENERATION_V1,
    PipelineCheckpoint,
)
from src.models.risk_assessment import RiskAssessment
from src.models.webhook_subscription import WebhookSubscription
from src.services.organization_work_schedule_service import (
    schedule_organization_work_retry,
)
from src.services.pipeline_checkpoint_service import (
    persist_pipeline_checkpoint,
)
from src.services.pipeline_source_discovery_service import (
    discover_alert_reconciliation_source,
    discover_pending_alert_reconciliation_sources,
)
from src.services.risk_assessment_service import (
    RISK_VERSION_V2,
)


def _make_organization(
    db,
    *,
    suffix=None,
):
    suffix = (
        suffix
        or uuid.uuid4().hex[:10]
    )

    organization = Organization(
        id=uuid.uuid4(),
        name=f"alert-reconciliation-{suffix}",
        slug=f"alert-reconciliation-{suffix}",
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
    assessment_id=None,
):
    assessment = RiskAssessment(
        id=assessment_id or uuid.uuid4(),
        organization_id=organization_id,
        semantic_drift_event_id=None,
        risk_version=RISK_VERSION_V2,
        assessed_at=assessed_at,
        score=0.50,
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


def _make_policy_revision(
    db,
    *,
    organization_id,
    policy_revision,
    reconciled_revision,
):
    revision = OrganizationAlertPolicyRevision(
        organization_id=organization_id,
        policy_revision=policy_revision,
        reconciled_revision=reconciled_revision,
    )

    db.add(
        revision
    )
    db.flush()

    return revision


def _make_webhook(
    db,
    *,
    organization_id,
    created_at,
):
    subscription = WebhookSubscription(
        id=uuid.uuid4(),
        organization_id=organization_id,
        url=(
            "https://example.com/hooks/"
            f"{uuid.uuid4().hex}"
        ),
        events=["high_risk"],
        secret_ciphertext=b"integration-test-ciphertext",
        secret_nonce=b"0123456789ab",
        secret_encryption_version=1,
        is_active=True,
        created_at=created_at,
    )

    db.add(
        subscription
    )
    db.flush()

    return subscription


def _cleanup(
    db,
    organization_ids,
):
    db.rollback()

    if not organization_ids:
        return

    db.execute(
        delete(
            OrganizationWorkSchedule
        ).where(
            OrganizationWorkSchedule.organization_id.in_(
                organization_ids
            )
        )
    )


    db.execute(
        delete(
            PipelineCheckpoint
        ).where(
            PipelineCheckpoint.organization_id.in_(
                organization_ids
            )
        )
    )

    db.execute(
        delete(
            WebhookSubscription
        ).where(
            WebhookSubscription.organization_id.in_(
                organization_ids
            )
        )
    )

    db.execute(
        delete(
            OrganizationAlertPolicyRevision
        ).where(
            OrganizationAlertPolicyRevision.organization_id.in_(
                organization_ids
            )
        )
    )

    db.execute(
        delete(
            RiskAssessment
        ).where(
            RiskAssessment.organization_id.in_(
                organization_ids
            )
        )
    )

    db.execute(
        delete(
            Organization
        ).where(
            Organization.id.in_(
                organization_ids
            )
        )
    )

    db.commit()


def _only_organizations(
    results,
    organization_ids,
):
    organization_id_set = set(
        organization_ids
    )

    return [
        result
        for result in results
        if result.organization_id
        in organization_id_set
    ]


def test_pending_policy_revision_is_discovered_when_alert_checkpoint_is_current():
    db = SessionLocal()
    organization_ids = []

    try:
        organization = _make_organization(
            db
        )
        organization_ids.append(
            organization.id
        )

        assessed_at = datetime(
            2026,
            9,
            10,
            12,
            0,
            tzinfo=timezone.utc,
        )

        source = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=assessed_at,
            created_at=(
                assessed_at
                + timedelta(hours=1)
            ),
        )

        _make_policy_revision(
            db,
            organization_id=organization.id,
            policy_revision=2,
            reconciled_revision=1,
        )

        db.commit()

        persist_pipeline_checkpoint(
            db,
            organization_id=organization.id,
            pipeline_name=(
                PIPELINE_ALERT_GENERATION_V1
            ),
            source_created_at=source.created_at,
            source_record_id=source.id,
            knowledge_cursor=assessed_at,
        )

        db.commit()

        discovered = (
            discover_alert_reconciliation_source(
                db,
                organization_id=organization.id,
            )
        )

        assert discovered is not None
        assert (
            discovered.organization_id
            == organization.id
        )
        assert (
            discovered.pipeline_name
            == PIPELINE_ALERT_GENERATION_V1
        )
        assert (
            discovered.source_created_at
            == source.created_at
        )
        assert (
            discovered.source_record_id
            == source.id
        )
        assert (
            discovered.temporal_cursor
            == assessed_at
        )

    finally:
        try:
            _cleanup(
                db,
                organization_ids,
            )
        finally:
            db.close()


def test_reconciled_policy_revision_is_not_discovered():
    db = SessionLocal()
    organization_ids = []

    try:
        organization = _make_organization(
            db
        )
        organization_ids.append(
            organization.id
        )

        assessed_at = datetime(
            2026,
            9,
            10,
            12,
            0,
            tzinfo=timezone.utc,
        )

        _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=assessed_at,
            created_at=(
                assessed_at
                + timedelta(hours=1)
            ),
        )

        _make_policy_revision(
            db,
            organization_id=organization.id,
            policy_revision=3,
            reconciled_revision=3,
        )

        db.commit()

        discovered = (
            discover_alert_reconciliation_source(
                db,
                organization_id=organization.id,
            )
        )

        assert discovered is None

    finally:
        try:
            _cleanup(
                db,
                organization_ids,
            )
        finally:
            db.close()


def test_new_webhook_after_delivery_checkpoint_is_discovered():
    db = SessionLocal()
    organization_ids = []

    try:
        organization = _make_organization(
            db
        )
        organization_ids.append(
            organization.id
        )

        base = datetime(
            2026,
            9,
            10,
            12,
            0,
            tzinfo=timezone.utc,
        )

        source = _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=base,
            created_at=(
                base
                + timedelta(hours=1)
            ),
        )

        first_webhook = _make_webhook(
            db,
            organization_id=organization.id,
            created_at=(
                base
                + timedelta(hours=2)
            ),
        )

        db.commit()

        persist_pipeline_checkpoint(
            db,
            organization_id=organization.id,
            pipeline_name=(
                PIPELINE_ALERT_GENERATION_V1
            ),
            source_created_at=source.created_at,
            source_record_id=source.id,
            knowledge_cursor=base,
        )

        persist_pipeline_checkpoint(
            db,
            organization_id=organization.id,
            pipeline_name=(
                PIPELINE_ALERT_DELIVERY_RECONCILIATION_V1
            ),
            source_created_at=first_webhook.created_at,
            source_record_id=first_webhook.id,
            knowledge_cursor=base,
        )

        db.commit()

        second_webhook = _make_webhook(
            db,
            organization_id=organization.id,
            created_at=(
                base
                + timedelta(hours=3)
            ),
        )

        db.commit()

        discovered = (
            discover_alert_reconciliation_source(
                db,
                organization_id=organization.id,
            )
        )

        assert discovered is not None
        assert (
            discovered.organization_id
            == organization.id
        )

        results = (
            discover_pending_alert_reconciliation_sources(
                db,
                limit=100,
            )
        )

        results = _only_organizations(
            results,
            organization_ids,
        )

        assert [
            result.organization_id
            for result in results
        ] == [
            organization.id
        ]

        assert (
            second_webhook.created_at
            > first_webhook.created_at
        )

    finally:
        try:
            _cleanup(
                db,
                organization_ids,
            )
        finally:
            db.close()


def test_delivery_checkpoint_covering_latest_webhook_is_not_pending():
    db = SessionLocal()
    organization_ids = []

    try:
        organization = _make_organization(
            db
        )
        organization_ids.append(
            organization.id
        )

        base = datetime(
            2026,
            9,
            10,
            12,
            0,
            tzinfo=timezone.utc,
        )

        _make_risk_assessment(
            db,
            organization_id=organization.id,
            assessed_at=base,
            created_at=(
                base
                + timedelta(hours=1)
            ),
        )

        webhook = _make_webhook(
            db,
            organization_id=organization.id,
            created_at=(
                base
                + timedelta(hours=2)
            ),
        )

        db.commit()

        persist_pipeline_checkpoint(
            db,
            organization_id=organization.id,
            pipeline_name=(
                PIPELINE_ALERT_DELIVERY_RECONCILIATION_V1
            ),
            source_created_at=webhook.created_at,
            source_record_id=webhook.id,
            knowledge_cursor=base,
        )

        db.commit()

        discovered = (
            discover_alert_reconciliation_source(
                db,
                organization_id=organization.id,
            )
        )

        assert discovered is None

    finally:
        try:
            _cleanup(
                db,
                organization_ids,
            )
        finally:
            db.close()


def test_action_state_without_risk_v2_does_not_fabricate_source():
    db = SessionLocal()
    organization_ids = []

    try:
        organization = _make_organization(
            db
        )
        organization_ids.append(
            organization.id
        )

        _make_policy_revision(
            db,
            organization_id=organization.id,
            policy_revision=1,
            reconciled_revision=0,
        )

        _make_webhook(
            db,
            organization_id=organization.id,
            created_at=datetime(
                2026,
                9,
                10,
                12,
                0,
                tzinfo=timezone.utc,
            ),
        )

        db.commit()

        discovered = (
            discover_alert_reconciliation_source(
                db,
                organization_id=organization.id,
            )
        )

        assert discovered is None

        results = (
            discover_pending_alert_reconciliation_sources(
                db,
                limit=100,
            )
        )

        results = _only_organizations(
            results,
            organization_ids,
        )

        assert results == []

    finally:
        try:
            _cleanup(
                db,
                organization_ids,
            )
        finally:
            db.close()


def test_alert_reconciliation_limit_is_deterministic():
    db = SessionLocal()
    organization_ids = []

    try:
        base = datetime(
            2026,
            9,
            10,
            12,
            0,
            tzinfo=timezone.utc,
        )

        first = _make_organization(
            db
        )
        second = _make_organization(
            db
        )

        organization_ids.extend(
            [
                first.id,
                second.id,
            ]
        )

        first_source = _make_risk_assessment(
            db,
            organization_id=first.id,
            assessed_at=base,
            created_at=(
                base
                + timedelta(hours=1)
            ),
        )

        _make_risk_assessment(
            db,
            organization_id=second.id,
            assessed_at=(
                base
                + timedelta(minutes=1)
            ),
            created_at=(
                base
                + timedelta(hours=2)
            ),
        )

        _make_policy_revision(
            db,
            organization_id=first.id,
            policy_revision=1,
            reconciled_revision=0,
        )

        _make_policy_revision(
            db,
            organization_id=second.id,
            policy_revision=1,
            reconciled_revision=0,
        )

        db.commit()

        results = (
            discover_pending_alert_reconciliation_sources(
                db,
                limit=1,
            )
        )

        results = _only_organizations(
            results,
            organization_ids,
        )

        assert len(results) == 1
        assert (
            results[0].organization_id
            == first.id
        )
        assert (
            results[0].source_record_id
            == first_source.id
        )

    finally:
        try:
            _cleanup(
                db,
                organization_ids,
            )
        finally:
            db.close()

def test_webhook_materialization_cursor_is_non_null():
    db = SessionLocal()
    organization_ids = []

    try:
        organization = _make_organization(
            db
        )

        organization_ids.append(
            organization.id
        )

        webhook = _make_webhook(
            db,
            organization_id=organization.id,
            created_at=datetime(
                2026,
                9,
                14,
                6,
                0,
                tzinfo=timezone.utc,
            ),
        )

        db.commit()

        db.refresh(
            webhook
        )

        assert webhook.created_at is not None

    finally:
        try:
            _cleanup(
                db,
                organization_ids,
            )
        finally:
            db.close()


def test_alert_schedule_controls_only_alert_reconciliation_eligibility():
    db = SessionLocal()
    organization_ids = []

    try:
        organization = _make_organization(
            db
        )
        organization_id = organization.id

        organization_ids.append(
            organization_id
        )

        now = datetime.now(
            timezone.utc
        )

        source = _make_risk_assessment(
            db,
            organization_id=organization_id,
            assessed_at=now - timedelta(hours=2),
            created_at=now - timedelta(hours=1),
        )

        source_id = source.id

        _make_policy_revision(
            db,
            organization_id=organization_id,
            policy_revision=2,
            reconciled_revision=1,
        )

        db.commit()

        schedule_organization_work_retry(
            db,
            organization_id=organization.id,
            work_kind=WORK_KIND_INTELLIGENCE,
            retry_seconds=3600,
            now=now,
        )

        db.commit()

        results = (
            discover_pending_alert_reconciliation_sources(
                db,
                limit=100,
            )
        )

        results = _only_organizations(
            results,
            organization_ids,
        )

        assert [
            result.organization_id
            for result in results
        ] == [
            organization.id
        ]

        assert (
            results[0].source_record_id
            == source.id
        )

        schedule_organization_work_retry(
            db,
            organization_id=organization.id,
            work_kind=WORK_KIND_ALERT_RECONCILIATION,
            retry_seconds=3600,
            now=now,
        )

        db.commit()

        results = (
            discover_pending_alert_reconciliation_sources(
                db,
                limit=100,
            )
        )

        assert _only_organizations(
            results,
            organization_ids,
        ) == []

        schedule = (
            db.query(
                OrganizationWorkSchedule
            )
            .filter(
                OrganizationWorkSchedule.organization_id
                == organization.id,
                OrganizationWorkSchedule.work_kind
                == WORK_KIND_ALERT_RECONCILIATION,
            )
            .one()
        )

        schedule.retry_after = (
            now - timedelta(seconds=1)
        )

        db.commit()
        db.close()

        db = SessionLocal()

        results = (
            discover_pending_alert_reconciliation_sources(
                db,
                limit=100,
            )
        )

        results = _only_organizations(
            results,
            organization_ids,
        )

        assert [
            result.organization_id
            for result in results
        ] == [
            organization_id
        ]

        assert (
            results[0].source_record_id
            == source_id
        )

    finally:
        db.close()

        cleanup_db = SessionLocal()

        try:
            _cleanup(
                cleanup_db,
                organization_ids,
            )
        finally:
            cleanup_db.close()
