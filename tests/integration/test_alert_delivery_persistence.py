from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import delete

from src.core.database import SessionLocal
from src.models.alert import Alert
from src.models.alert_delivery import (
    ALERT_DELIVERY_DESTINATION_WEBHOOK,
    ALERT_DELIVERY_STATUS_PENDING,
    AlertDelivery,
)
from src.models.organization import Organization
from src.models.webhook_subscription import WebhookSubscription
from src.services.alert_delivery_service import (
    AlertDeliveryError,
    persist_alert_delivery,
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
        name=f"alert-delivery-{prefix}-{suffix}",
        slug=f"alert-delivery-{prefix}-{suffix}",
        billing_email=(
            f"alert-delivery-{prefix}-{suffix}"
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
) -> Alert:
    alert = Alert(
        id=uuid.uuid4(),
        organization_id=organization_id,
        event_id=None,
        source_type="risk",
        source_id=uuid.uuid4(),
        detection_key=uuid.uuid4().hex,
        alert_type="risk",
        title="Risk increased",
        message="Material semantic risk detected.",
        severity="high",
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
    is_active: bool = True,
) -> WebhookSubscription:
    subscription = WebhookSubscription(
        id=uuid.uuid4(),
        organization_id=organization_id,
        url="https://example.test/webhook",
        events=["high_risk"],
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


def test_persist_alert_delivery_is_idempotent():
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
        )

        first = persist_alert_delivery(
            db,
            organization_id=organization.id,
            alert_id=alert.id,
            destination_type=(
                ALERT_DELIVERY_DESTINATION_WEBHOOK
            ),
            destination_id=webhook.id,
        )

        second = persist_alert_delivery(
            db,
            organization_id=organization.id,
            alert_id=alert.id,
            destination_type=(
                ALERT_DELIVERY_DESTINATION_WEBHOOK
            ),
            destination_id=webhook.id,
        )

        assert first.created is True
        assert second.created is False
        assert first.delivery.id == second.delivery.id

        rows = (
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

        assert len(rows) == 1
        assert (
            rows[0].status
            == ALERT_DELIVERY_STATUS_PENDING
        )
        assert rows[0].attempt_count == 0

        db.commit()

    finally:
        db.close()
        _cleanup(organization_ids)


def test_persist_alert_delivery_blocks_cross_tenant_destination():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        alert_organization = _make_organization(
            db,
            prefix="cross-tenant-alert",
        )
        organization_ids.append(
            alert_organization.id
        )

        destination_organization = _make_organization(
            db,
            prefix="cross-tenant-destination",
        )
        organization_ids.append(
            destination_organization.id
        )

        alert = _make_alert(
            db,
            organization_id=alert_organization.id,
        )

        webhook = _make_webhook(
            db,
            organization_id=(
                destination_organization.id
            ),
        )

        with pytest.raises(
            AlertDeliveryError,
            match=(
                "Webhook destination does not exist "
                "for the requested organization"
            ),
        ):
            persist_alert_delivery(
                db,
                organization_id=(
                    alert_organization.id
                ),
                alert_id=alert.id,
                destination_type=(
                    ALERT_DELIVERY_DESTINATION_WEBHOOK
                ),
                destination_id=webhook.id,
            )

        count = (
            db.query(AlertDelivery)
            .filter(
                AlertDelivery.organization_id
                == alert_organization.id
            )
            .count()
        )

        assert count == 0

        db.commit()

    finally:
        db.close()
        _cleanup(organization_ids)


def test_persist_alert_delivery_blocks_inactive_webhook():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            db,
            prefix="inactive",
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
            is_active=False,
        )

        with pytest.raises(
            AlertDeliveryError,
            match="Webhook destination is not active",
        ):
            persist_alert_delivery(
                db,
                organization_id=organization.id,
                alert_id=alert.id,
                destination_type=(
                    ALERT_DELIVERY_DESTINATION_WEBHOOK
                ),
                destination_id=webhook.id,
            )

        count = (
            db.query(AlertDelivery)
            .filter(
                AlertDelivery.organization_id
                == organization.id
            )
            .count()
        )

        assert count == 0

        db.commit()

    finally:
        db.close()
        _cleanup(organization_ids)


def test_persist_alert_delivery_snapshots_custom_payload():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            db,
            prefix="payload",
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
        )

        custom_payload = {
            "schema_version": 1,
            "event": "custom.alert",
            "data": {
                "alert_id": str(alert.id),
                "value": 42,
            },
        }

        result = persist_alert_delivery(
            db,
            organization_id=organization.id,
            alert_id=alert.id,
            destination_type=(
                ALERT_DELIVERY_DESTINATION_WEBHOOK
            ),
            destination_id=webhook.id,
            payload=custom_payload,
        )

        assert result.created is True
        assert result.delivery.payload == (
            custom_payload
        )

        persisted = (
            db.query(AlertDelivery)
            .filter(
                AlertDelivery.id
                == result.delivery.id
            )
            .one()
        )

        assert persisted.payload == custom_payload

        db.commit()

    finally:
        db.close()
        _cleanup(organization_ids)


def test_alert_delivery_is_removed_by_caller_rollback():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(
            db,
            prefix="rollback",
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
        )

        organization_id = organization.id
        alert_id = alert.id
        webhook_id = webhook.id

        # Persist fixture state first. The rollback below must
        # affect only the AlertDelivery materialization.
        db.commit()

        result = persist_alert_delivery(
            db,
            organization_id=organization_id,
            alert_id=alert_id,
            destination_type=(
                ALERT_DELIVERY_DESTINATION_WEBHOOK
            ),
            destination_id=webhook_id,
        )

        delivery_id = result.delivery.id

        assert (
            db.query(AlertDelivery)
            .filter(
                AlertDelivery.id
                == delivery_id
            )
            .one_or_none()
            is not None
        )

        db.rollback()

        persisted_delivery = (
            db.query(AlertDelivery)
            .filter(
                AlertDelivery.id
                == delivery_id
            )
            .one_or_none()
        )

        assert persisted_delivery is None

        persisted_alert = (
            db.query(Alert)
            .filter(
                Alert.id == alert_id
            )
            .one_or_none()
        )

        persisted_webhook = (
            db.query(WebhookSubscription)
            .filter(
                WebhookSubscription.id
                == webhook_id
            )
            .one_or_none()
        )

        assert persisted_alert is not None
        assert persisted_webhook is not None

    finally:
        db.close()
        _cleanup(organization_ids)