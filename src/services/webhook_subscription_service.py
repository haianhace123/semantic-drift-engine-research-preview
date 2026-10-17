"""
Tenant-scoped management for webhook subscriptions.

This service owns webhook configuration validation and persistence.
It does not send webhook requests or commit database transactions.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from src.core.config import settings
from src.models.webhook_subscription import WebhookSubscription
from src.services.webhook_delivery_transport_service import (
    PermanentWebhookDeliveryError,
    TransientWebhookDeliveryError,
    validate_webhook_url,
)
from src.services.webhook_secret_encryption_service import (
    EncryptedWebhookSecret,
    encrypt_webhook_secret,
)


WEBHOOK_EVENT_ALL = "*"
WEBHOOK_EVENT_ALERT_CREATED = "alert.created"
WEBHOOK_EVENT_HIGH_RISK = "high_risk"
WEBHOOK_EVENT_MEDIUM_RISK = "medium_risk"
WEBHOOK_EVENT_PATTERN = "pattern"
WEBHOOK_EVENT_ANOMALY = "anomaly"

SUPPORTED_WEBHOOK_EVENTS = frozenset(
    {
        WEBHOOK_EVENT_ALL,
        WEBHOOK_EVENT_ALERT_CREATED,
        WEBHOOK_EVENT_HIGH_RISK,
        WEBHOOK_EVENT_MEDIUM_RISK,
        WEBHOOK_EVENT_PATTERN,
        WEBHOOK_EVENT_ANOMALY,
    }
)

DEFAULT_WEBHOOK_EVENTS = (
    WEBHOOK_EVENT_HIGH_RISK,
)


class WebhookSubscriptionValidationError(ValueError):
    """Raised when webhook configuration is invalid."""


class WebhookSubscriptionNotFoundError(LookupError):
    """Raised when a tenant-owned webhook subscription does not exist."""


@dataclass(frozen=True)
class CreatedWebhookSubscription:
    subscription: WebhookSubscription
    secret_key: str


def _normalize_events(
    events: list[str] | tuple[str, ...] | None,
) -> list[str]:
    if events is None:
        normalized = list(
            DEFAULT_WEBHOOK_EVENTS
        )
    else:
        normalized = []

        for value in events:
            event = str(
                value or ""
            ).strip()

            if not event:
                raise WebhookSubscriptionValidationError(
                    "Webhook events cannot contain empty values."
                )

            if event not in SUPPORTED_WEBHOOK_EVENTS:
                raise WebhookSubscriptionValidationError(
                    f"Unsupported webhook event: {event}"
                )

            if event not in normalized:
                normalized.append(
                    event
                )

    if not normalized:
        raise WebhookSubscriptionValidationError(
            "At least one webhook event is required."
        )

    if (
        WEBHOOK_EVENT_ALL in normalized
        and len(normalized) > 1
    ):
        raise WebhookSubscriptionValidationError(
            'Webhook event "*" cannot be combined with other events.'
        )

    return normalized


def _validate_url(
    url: str,
) -> str:
    candidate = str(
        url or ""
    ).strip()

    if not candidate:
        raise WebhookSubscriptionValidationError(
            "Webhook URL is required."
        )

    try:
        return validate_webhook_url(
            candidate
        )

    except (
        PermanentWebhookDeliveryError,
        TransientWebhookDeliveryError,
    ) as exc:
        raise WebhookSubscriptionValidationError(
            str(exc)
        ) from exc


def _generate_secret() -> str:
    return secrets.token_urlsafe(
        32
    )


def _encrypt_secret(
    secret_key: str,
    *,
    organization_id: uuid.UUID,
    subscription_id: uuid.UUID,
) -> EncryptedWebhookSecret:
    return encrypt_webhook_secret(
        secret_key,
        organization_id=organization_id,
        subscription_id=subscription_id,
        encoded_encryption_key=(
            settings.WEBHOOK_SECRET_ENCRYPTION_KEY
        ),
    )


def _store_encrypted_secret(
    subscription: WebhookSubscription,
    encrypted: EncryptedWebhookSecret,
) -> None:
    subscription.secret_ciphertext = (
        encrypted.ciphertext
    )
    subscription.secret_nonce = (
        encrypted.nonce
    )
    subscription.secret_encryption_version = (
        encrypted.version
    )


def get_webhook_subscription(
    db: Session,
    *,
    organization_id: uuid.UUID,
    subscription_id: uuid.UUID,
) -> WebhookSubscription:
    subscription = (
        db.query(
            WebhookSubscription
        )
        .filter(
            WebhookSubscription.id
            == subscription_id,
            WebhookSubscription.organization_id
            == organization_id,
        )
        .first()
    )

    if subscription is None:
        raise WebhookSubscriptionNotFoundError(
            "Webhook subscription not found."
        )

    return subscription


def list_webhook_subscriptions(
    db: Session,
    *,
    organization_id: uuid.UUID,
) -> list[WebhookSubscription]:
    return (
        db.query(
            WebhookSubscription
        )
        .filter(
            WebhookSubscription.organization_id
            == organization_id
        )
        .order_by(
            WebhookSubscription.created_at.desc(),
            WebhookSubscription.id.asc(),
        )
        .all()
    )


def create_webhook_subscription(
    db: Session,
    *,
    organization_id: uuid.UUID,
    url: str,
    events: list[str] | tuple[str, ...] | None = None,
) -> CreatedWebhookSubscription:
    validated_url = _validate_url(
        url
    )

    normalized_events = _normalize_events(
        events
    )

    subscription_id = uuid.uuid4()
    secret_key = _generate_secret()

    encrypted = _encrypt_secret(
        secret_key,
        organization_id=organization_id,
        subscription_id=subscription_id,
    )

    subscription = WebhookSubscription(
        id=subscription_id,
        organization_id=organization_id,
        url=validated_url,
        events=normalized_events,
        is_active=True,
    )

    _store_encrypted_secret(
        subscription,
        encrypted,
    )

    db.add(
        subscription
    )

    db.flush()

    return CreatedWebhookSubscription(
        subscription=subscription,
        secret_key=secret_key,
    )


def rotate_webhook_secret(
    db: Session,
    *,
    organization_id: uuid.UUID,
    subscription_id: uuid.UUID,
) -> str:
    subscription = get_webhook_subscription(
        db,
        organization_id=organization_id,
        subscription_id=subscription_id,
    )

    secret_key = _generate_secret()

    encrypted = _encrypt_secret(
        secret_key,
        organization_id=organization_id,
        subscription_id=subscription_id,
    )

    _store_encrypted_secret(
        subscription,
        encrypted,
    )

    db.flush()

    return secret_key


def deactivate_webhook_subscription(
    db: Session,
    *,
    organization_id: uuid.UUID,
    subscription_id: uuid.UUID,
) -> WebhookSubscription:
    subscription = get_webhook_subscription(
        db,
        organization_id=organization_id,
        subscription_id=subscription_id,
    )

    subscription.is_active = False

    db.flush()

    return subscription