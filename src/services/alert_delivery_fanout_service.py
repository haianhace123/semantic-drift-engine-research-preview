"""
Deterministic Alert delivery fan-out.

Responsibilities:
- load active WebhookSubscriptions for one organization;
- apply subscription event filters;
- materialize deterministic AlertDelivery rows;
- backfill missing deliveries for existing Alerts.

Non-responsibilities:
- no external HTTP;
- no retry processing;
- no commit / rollback.

The caller owns the surrounding transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
import uuid

from sqlalchemy.orm import Session

from src.models.alert import (
    ALERT_SOURCE_ANOMALY,
    ALERT_SOURCE_PATTERN,
    ALERT_SOURCE_RISK,
    Alert,
)
from src.models.alert_delivery import (
    ALERT_DELIVERY_DESTINATION_WEBHOOK,
)
from src.models.webhook_subscription import (
    WebhookSubscription,
)
from src.services.alert_delivery_service import (
    persist_alert_delivery,
)


WEBHOOK_EVENT_ALL = "*"
WEBHOOK_EVENT_ALERT_CREATED = "alert.created"
WEBHOOK_EVENT_HIGH_RISK = "high_risk"
WEBHOOK_EVENT_MEDIUM_RISK = "medium_risk"
WEBHOOK_EVENT_PATTERN = "pattern"
WEBHOOK_EVENT_ANOMALY = "anomaly"


class AlertDeliveryFanoutError(
    RuntimeError
):
    """Raised when delivery fan-out evidence is inconsistent."""


@dataclass(frozen=True)
class AlertDeliveryFanoutResult:
    organization_id: uuid.UUID
    alert_id: uuid.UUID
    matched_destination_count: int
    delivery_count: int


@dataclass(frozen=True)
class AlertDeliveryReconciliationResult:
    organization_id: uuid.UUID
    alert_count: int
    subscription_count: int
    active_subscription_count: int
    matched_destination_count: int
    delivery_count: int


def _normalize_events(
    events,
) -> frozenset[str]:
    if not events:
        return frozenset()

    normalized = {
        str(event).strip().lower()
        for event in events
        if str(event).strip()
    }

    return frozenset(
        normalized
    )


def webhook_subscription_matches_alert(
    *,
    subscription: WebhookSubscription,
    alert: Alert,
) -> bool:
    """
    Return whether one active subscription wants this Alert.

    Legacy compatibility:
    - high_risk means Risk Alert severity high or critical.

    Modern generic subscriptions may use alert.created or *.
    """
    if subscription.is_active is not True:
        return False

    events = _normalize_events(
        subscription.events
    )

    if not events:
        return False

    if (
        WEBHOOK_EVENT_ALL in events
        or WEBHOOK_EVENT_ALERT_CREATED in events
    ):
        return True

    source_type = str(
        alert.source_type or ""
    ).strip().lower()

    severity = str(
        alert.severity or ""
    ).strip().lower()

    if source_type == ALERT_SOURCE_RISK:
        if (
            WEBHOOK_EVENT_HIGH_RISK in events
            and severity in {
                "high",
                "critical",
            }
        ):
            return True

        if (
            WEBHOOK_EVENT_MEDIUM_RISK in events
            and severity == "medium"
        ):
            return True

        return False

    if source_type == ALERT_SOURCE_PATTERN:
        return (
            WEBHOOK_EVENT_PATTERN
            in events
        )

    if source_type == ALERT_SOURCE_ANOMALY:
        return (
            WEBHOOK_EVENT_ANOMALY
            in events
        )

    return False


def _load_alert(
    db: Session,
    *,
    organization_id: uuid.UUID,
    alert_id: uuid.UUID,
) -> Alert:
    alert = (
        db.query(
            Alert
        )
        .filter(
            Alert.id == alert_id,
            Alert.organization_id
            == organization_id,
        )
        .one_or_none()
    )

    if alert is None:
        raise AlertDeliveryFanoutError(
            "Alert does not exist for the requested organization."
        )

    return alert


def _load_active_webhooks(
    db: Session,
    *,
    organization_id: uuid.UUID,
) -> tuple[
    WebhookSubscription,
    ...
]:
    return tuple(
        db.query(
            WebhookSubscription
        )
        .filter(
            WebhookSubscription.organization_id
            == organization_id,
            WebhookSubscription.is_active.is_(
                True
            ),
        )
        .order_by(
            WebhookSubscription.id.asc()
        )
        .all()
    )


def fanout_alert_deliveries(
    db: Session,
    *,
    organization_id: uuid.UUID,
    alert_id: uuid.UUID,
) -> AlertDeliveryFanoutResult:
    """
    Materialize all matching webhook deliveries for one Alert.

    Idempotency is delegated to persist_alert_delivery(), whose
    deterministic identity is based on Alert + destination.

    Existing Alerts are intentionally supported so a rerun can
    backfill a missing delivery row before the Alert pipeline
    checkpoint advances.
    """
    alert = _load_alert(
        db,
        organization_id=organization_id,
        alert_id=alert_id,
    )

    subscriptions = (
        _load_active_webhooks(
            db,
            organization_id=organization_id,
        )
    )

    matched = tuple(
        subscription
        for subscription in subscriptions
        if webhook_subscription_matches_alert(
            subscription=subscription,
            alert=alert,
        )
    )

    for subscription in matched:
        persist_alert_delivery(
            db,
            organization_id=organization_id,
            alert_id=alert.id,
            destination_type=(
                ALERT_DELIVERY_DESTINATION_WEBHOOK
            ),
            destination_id=subscription.id,
        )

    return AlertDeliveryFanoutResult(
        organization_id=organization_id,
        alert_id=alert.id,
        matched_destination_count=len(
            matched
        ),
        delivery_count=len(
            matched
        ),
    )

def reconcile_alert_deliveries(
    db: Session,
    *,
    organization_id: uuid.UUID,
    alerts: tuple[Alert, ...],
    subscriptions: tuple[
        WebhookSubscription,
        ...
    ],
) -> AlertDeliveryReconciliationResult:
    """
    Materialize missing deliveries for existing Alerts against a supplied
    immutable WebhookSubscription materialization range.

    The caller owns source loading, checkpointing, and the surrounding
    transaction.
    """
    for alert in alerts:
        if alert.organization_id != organization_id:
            raise AlertDeliveryFanoutError(
                "Alert does not belong to the requested organization."
            )

    for subscription in subscriptions:
        if subscription.organization_id != organization_id:
            raise AlertDeliveryFanoutError(
                "WebhookSubscription does not belong to the requested "
                "organization."
            )

    active_subscriptions = tuple(
        subscription
        for subscription in subscriptions
        if subscription.is_active is True
    )

    matched_destination_count = 0
    delivery_count = 0

    for alert in alerts:
        for subscription in active_subscriptions:
            if not webhook_subscription_matches_alert(
                subscription=subscription,
                alert=alert,
            ):
                continue

            matched_destination_count += 1

            persist_alert_delivery(
                db,
                organization_id=organization_id,
                alert_id=alert.id,
                destination_type=(
                    ALERT_DELIVERY_DESTINATION_WEBHOOK
                ),
                destination_id=subscription.id,
            )

            delivery_count += 1

    return AlertDeliveryReconciliationResult(
        organization_id=organization_id,
        alert_count=len(alerts),
        subscription_count=len(
            subscriptions
        ),
        active_subscription_count=len(
            active_subscriptions
        ),
        matched_destination_count=(
            matched_destination_count
        ),
        delivery_count=delivery_count,
    )
