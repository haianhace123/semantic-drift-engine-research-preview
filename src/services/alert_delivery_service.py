"""
Deterministic Alert delivery persistence.

This service owns durable delivery fan-out materialization only.

Responsibilities:
- validate Alert and destination tenant ownership;
- validate supported destination type;
- snapshot the outbound payload;
- derive deterministic delivery identity;
- persist AlertDelivery idempotently.

Non-responsibilities:
- no external network I/O;
- no delivery attempts;
- no retry scheduling;
- no commit / rollback.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.models.alert import Alert
from src.models.alert_delivery import (
    ALERT_DELIVERY_DESTINATION_WEBHOOK,
    ALERT_DELIVERY_STATUS_PENDING,
    AlertDelivery,
)
from src.models.webhook_subscription import WebhookSubscription


class AlertDeliveryError(ValueError):
    """Raised when an Alert delivery cannot be materialized safely."""


@dataclass(frozen=True)
class PersistedAlertDeliveryResult:
    delivery: AlertDelivery
    created: bool


def build_delivery_key(
    *,
    alert_id: uuid.UUID,
    destination_type: str,
    destination_id: uuid.UUID,
) -> str:
    """
    Build deterministic AlertDelivery identity.

    Mutable destination configuration and payload presentation are
    intentionally excluded from the key.
    """
    canonical = (
        f"{alert_id}|"
        f"{destination_type.strip().lower()}|"
        f"{destination_id}"
    )

    return hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def build_webhook_payload(
    *,
    alert: Alert,
) -> dict[str, Any]:
    """
    Build a durable webhook payload snapshot from Alert intelligence.

    The queue row stores this snapshot so later Alert edits cannot
    mutate an already-materialized outbound message.
    """
    return {
        "schema_version": 1,
        "event": "alert.created",
        "alert": {
            "id": str(alert.id),
            "organization_id": str(alert.organization_id),
            "event_id": (
                str(alert.event_id)
                if alert.event_id is not None
                else None
            ),
            "source_type": alert.source_type,
            "source_id": (
                str(alert.source_id)
                if alert.source_id is not None
                else None
            ),
            "alert_type": alert.alert_type,
            "title": alert.title,
            "message": alert.message,
            "severity": alert.severity,
            "recommendation": alert.recommendation,
            "affected_entities": list(
                alert.affected_entities or []
            ),
            "occurred_at": (
                alert.occurred_at.isoformat()
                if alert.occurred_at is not None
                else None
            ),
            "created_at": (
                alert.created_at.isoformat()
                if alert.created_at is not None
                else None
            ),
        },
    }


def _normalize_payload(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    return dict(payload)


def _load_alert(
    db: Session,
    *,
    organization_id: uuid.UUID,
    alert_id: uuid.UUID,
) -> Alert:
    alert = (
        db.query(Alert)
        .filter(
            Alert.id == alert_id,
            Alert.organization_id == organization_id,
        )
        .one_or_none()
    )

    if alert is None:
        raise AlertDeliveryError(
            "Alert does not exist for the requested organization."
        )

    return alert


def _load_webhook_subscription(
    db: Session,
    *,
    organization_id: uuid.UUID,
    destination_id: uuid.UUID,
) -> WebhookSubscription:
    subscription = (
        db.query(WebhookSubscription)
        .filter(
            WebhookSubscription.id == destination_id,
            WebhookSubscription.organization_id
            == organization_id,
        )
        .one_or_none()
    )

    if subscription is None:
        raise AlertDeliveryError(
            "Webhook destination does not exist for the requested "
            "organization."
        )

    if subscription.is_active is not True:
        raise AlertDeliveryError(
            "Webhook destination is not active."
        )

    return subscription


def _load_existing_delivery(
    db: Session,
    *,
    organization_id: uuid.UUID,
    delivery_key: str,
) -> AlertDelivery:
    delivery = (
        db.query(AlertDelivery)
        .filter(
            AlertDelivery.organization_id == organization_id,
            AlertDelivery.delivery_key == delivery_key,
        )
        .one_or_none()
    )

    if delivery is None:
        raise AlertDeliveryError(
            "AlertDelivery persistence conflict could not be "
            "resolved."
        )

    return delivery


def persist_alert_delivery(
    db: Session,
    *,
    organization_id: uuid.UUID,
    alert_id: uuid.UUID,
    destination_type: str,
    destination_id: uuid.UUID,
    payload: Mapping[str, Any] | None = None,
) -> PersistedAlertDeliveryResult:
    """
    Materialize one durable Alert -> destination delivery.

    PostgreSQL ON CONFLICT makes repeated calls idempotent.

    The caller owns commit / rollback.
    """
    normalized_destination_type = (
        destination_type.strip().lower()
    )

    if (
        normalized_destination_type
        != ALERT_DELIVERY_DESTINATION_WEBHOOK
    ):
        raise AlertDeliveryError(
            "Unsupported Alert delivery destination type: "
            f"{destination_type!r}"
        )

    alert = _load_alert(
        db,
        organization_id=organization_id,
        alert_id=alert_id,
    )

    _load_webhook_subscription(
        db,
        organization_id=organization_id,
        destination_id=destination_id,
    )

    delivery_key = build_delivery_key(
        alert_id=alert.id,
        destination_type=normalized_destination_type,
        destination_id=destination_id,
    )

    payload_snapshot = (
        build_webhook_payload(alert=alert)
        if payload is None
        else _normalize_payload(payload)
    )

    statement = (
        insert(AlertDelivery)
        .values(
            id=uuid.uuid4(),
            organization_id=organization_id,
            alert_id=alert.id,
            destination_type=normalized_destination_type,
            destination_id=destination_id,
            delivery_key=delivery_key,
            status=ALERT_DELIVERY_STATUS_PENDING,
            payload=payload_snapshot,
            attempt_count=0,
        )
        .on_conflict_do_nothing(
            index_elements=[
                AlertDelivery.organization_id,
                AlertDelivery.delivery_key,
            ],
        )
        .returning(AlertDelivery.id)
    )

    created_id = db.execute(statement).scalar_one_or_none()

    if created_id is not None:
        delivery = (
            db.query(AlertDelivery)
            .filter(AlertDelivery.id == created_id)
            .one()
        )

        return PersistedAlertDeliveryResult(
            delivery=delivery,
            created=True,
        )

    existing = _load_existing_delivery(
        db,
        organization_id=organization_id,
        delivery_key=delivery_key,
    )

    return PersistedAlertDeliveryResult(
        delivery=existing,
        created=False,
    )