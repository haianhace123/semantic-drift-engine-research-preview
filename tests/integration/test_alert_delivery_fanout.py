from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import delete

from src.core.database import SessionLocal
from src.models.alert import Alert
from src.models.alert_delivery import (
    ALERT_DELIVERY_STATUS_PENDING,
    AlertDelivery,
)
from src.models.organization import Organization
from src.models.webhook_subscription import WebhookSubscription
from src.services.alert_delivery_fanout_service import (
    fanout_alert_deliveries,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


def _make_organization(
    db,
    *,
    prefix: str,
) -> Organization:
    suffix = uuid.uuid4().hex[:12]

    organization = Organization(
        id=uuid.uuid4(),
        name=f"alert-fanout-{prefix}-{suffix}",
        slug=f"alert-fanout-{prefix}-{suffix}",
        billing_email=(
            f"alert-fanout-{prefix}-{suffix}"
            "@example.com"
        ),
    )

    db.add(organization)
    db.flush()

    return organization


def _make_alert(
    db,
    *,
    organization_id: uuid.UUID,
    source_type: str = "risk",
    severity: str = "high",
) -> Alert:
    alert = Alert(
        id=uuid.uuid4(),
        organization_id=organization_id,
        event_id=None,
        source_type=source_type,
        source_id=uuid.uuid4(),
        detection_key=uuid.uuid4().hex,
        alert_type=source_type,
        title="Material intelligence change",
        message="Material semantic intelligence detected.",
        severity=severity,
        recommendation="Review the affected source.",
        status="pending",
        affected_entities=[],
        sent_to=[],
    )

    db.add(alert)
    db.flush()

    return alert


def _make_webhook(
    db,
    *,
    organization_id: uuid.UUID,
    events: list[str],
    is_active: bool = True,
) -> WebhookSubscription:
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

    db.add(subscription)
    db.flush()

    return subscription


def _cleanup(
    organization_ids: list[uuid.UUID],
) -> None:
    if not organization_ids:
        return

    db = SessionLocal()

    try:
        db.execute(
            delete(AlertDelivery).where(
                AlertDelivery.organization_id.in_(
                    organization_ids
                )
            )
        )

        db.execute(
            delete(WebhookSubscription).where(
                WebhookSubscription.organization_id.in_(
                    organization_ids
                )
            )
        )

        db.execute(
            delete(Alert).where(
                Alert.organization_id.in_(
                    organization_ids
                )
            )
        )

        db.execute(
            delete(Organization).where(
                Organization.id.in_(
                    organization_ids
                )
            )
        )

        db.commit()

    finally:
        db.close()


def test_fanout_materializes_matching_webhook_delivery():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            db,
            prefix="matching",
        )
        organization_ids.append(
            organization.id
        )

        alert = _make_alert(
            db,
            organization_id=organization.id,
            source_type="risk",
            severity="high",
        )

        webhook = _make_webhook(
            db,
            organization_id=organization.id,
            events=["high_risk"],
        )

        result = fanout_alert_deliveries(
            db,
            organization_id=organization.id,
            alert_id=alert.id,
        )

        assert result.organization_id == organization.id
        assert result.alert_id == alert.id
        assert result.matched_destination_count == 1
        assert result.delivery_count == 1

        delivery = (
            db.query(AlertDelivery)
            .filter(
                AlertDelivery.organization_id
                == organization.id,
                AlertDelivery.alert_id
                == alert.id,
                AlertDelivery.destination_id
                == webhook.id,
            )
            .one()
        )

        assert delivery.status == ALERT_DELIVERY_STATUS_PENDING
        assert delivery.attempt_count == 0
        assert delivery.payload["event"] == "alert.created"
        assert (
            delivery.payload["alert"]["id"]
            == str(alert.id)
        )

        db.commit()

    finally:
        db.close()
        _cleanup(organization_ids)


def test_fanout_rerun_is_idempotent():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            db,
            prefix="idempotent",
        )
        organization_ids.append(
            organization.id
        )

        alert = _make_alert(
            db,
            organization_id=organization.id,
        )

        webhook = _make_webhook(
            db,
            organization_id=organization.id,
            events=["alert.created"],
        )

        first = fanout_alert_deliveries(
            db,
            organization_id=organization.id,
            alert_id=alert.id,
        )

        second = fanout_alert_deliveries(
            db,
            organization_id=organization.id,
            alert_id=alert.id,
        )

        assert first.delivery_count == 1
        assert second.delivery_count == 1

        deliveries = (
            db.query(AlertDelivery)
            .filter(
                AlertDelivery.organization_id
                == organization.id,
                AlertDelivery.alert_id
                == alert.id,
                AlertDelivery.destination_id
                == webhook.id,
            )
            .all()
        )

        assert len(deliveries) == 1

        db.commit()

    finally:
        db.close()
        _cleanup(organization_ids)


def test_fanout_skips_nonmatching_and_inactive_webhooks():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            db,
            prefix="filtered",
        )
        organization_ids.append(
            organization.id
        )

        alert = _make_alert(
            db,
            organization_id=organization.id,
            source_type="risk",
            severity="medium",
        )

        high_risk_webhook = _make_webhook(
            db,
            organization_id=organization.id,
            events=["high_risk"],
        )

        inactive_webhook = _make_webhook(
            db,
            organization_id=organization.id,
            events=["alert.created"],
            is_active=False,
        )

        result = fanout_alert_deliveries(
            db,
            organization_id=organization.id,
            alert_id=alert.id,
        )

        assert result.matched_destination_count == 0
        assert result.delivery_count == 0

        deliveries = (
            db.query(AlertDelivery)
            .filter(
                AlertDelivery.organization_id
                == organization.id,
                AlertDelivery.alert_id
                == alert.id,
            )
            .all()
        )

        assert deliveries == []

        assert (
            db.query(AlertDelivery)
            .filter(
                AlertDelivery.destination_id
                == high_risk_webhook.id
            )
            .count()
            == 0
        )

        assert (
            db.query(AlertDelivery)
            .filter(
                AlertDelivery.destination_id
                == inactive_webhook.id
            )
            .count()
            == 0
        )

        db.commit()

    finally:
        db.close()
        _cleanup(organization_ids)


def test_fanout_never_crosses_tenant_boundary():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        alert_organization = _make_organization(
            db,
            prefix="tenant-alert",
        )
        organization_ids.append(
            alert_organization.id
        )

        other_organization = _make_organization(
            db,
            prefix="tenant-other",
        )
        organization_ids.append(
            other_organization.id
        )

        alert = _make_alert(
            db,
            organization_id=alert_organization.id,
        )

        own_webhook = _make_webhook(
            db,
            organization_id=alert_organization.id,
            events=["alert.created"],
        )

        foreign_webhook = _make_webhook(
            db,
            organization_id=other_organization.id,
            events=["alert.created"],
        )

        result = fanout_alert_deliveries(
            db,
            organization_id=alert_organization.id,
            alert_id=alert.id,
        )

        assert result.matched_destination_count == 1
        assert result.delivery_count == 1

        own_delivery = (
            db.query(AlertDelivery)
            .filter(
                AlertDelivery.organization_id
                == alert_organization.id,
                AlertDelivery.alert_id
                == alert.id,
                AlertDelivery.destination_id
                == own_webhook.id,
            )
            .one_or_none()
        )

        foreign_delivery = (
            db.query(AlertDelivery)
            .filter(
                AlertDelivery.alert_id
                == alert.id,
                AlertDelivery.destination_id
                == foreign_webhook.id,
            )
            .one_or_none()
        )

        assert own_delivery is not None
        assert foreign_delivery is None

        db.commit()

    finally:
        db.close()
        _cleanup(organization_ids)