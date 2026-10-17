from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest

import src.services.webhook_subscription_service as service
from src.models.webhook_subscription import WebhookSubscription
from src.services.webhook_secret_encryption_service import (
    EncryptedWebhookSecret,
)
from src.services.webhook_delivery_transport_service import (
    PermanentWebhookDeliveryError,
    TransientWebhookDeliveryError,
)


def test_normalize_events_uses_legacy_default() -> None:
    assert service._normalize_events(
        None
    ) == [
        "high_risk"
    ]


def test_normalize_events_preserves_order_and_removes_duplicates() -> None:
    assert service._normalize_events(
        [
            "pattern",
            "high_risk",
            "pattern",
            "anomaly",
        ]
    ) == [
        "pattern",
        "high_risk",
        "anomaly",
    ]


@pytest.mark.parametrize(
    "event",
    [
        "",
        " ",
        "unknown",
        "risk",
        "alert",
    ],
)
def test_normalize_events_rejects_invalid_values(
    event: str,
) -> None:
    with pytest.raises(
        service.WebhookSubscriptionValidationError
    ):
        service._normalize_events(
            [event]
        )


def test_normalize_events_rejects_empty_list() -> None:
    with pytest.raises(
        service.WebhookSubscriptionValidationError,
        match="At least one webhook event",
    ):
        service._normalize_events(
            []
        )


def test_normalize_events_accepts_wildcard_alone() -> None:
    assert service._normalize_events(
        ["*"]
    ) == ["*"]


def test_normalize_events_rejects_wildcard_combination() -> None:
    with pytest.raises(
        service.WebhookSubscriptionValidationError,
        match="cannot be combined",
    ):
        service._normalize_events(
            [
                "*",
                "pattern",
            ]
        )


def test_normalize_events_accepts_complete_contract() -> None:
    events = [
        "high_risk",
        "medium_risk",
        "pattern",
        "anomaly",
        "alert.created",
    ]

    assert service._normalize_events(
        events
    ) == events


def test_validate_url_returns_transport_validated_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validate = MagicMock(
        return_value=(
            "https://example.com/webhook"
        )
    )

    monkeypatch.setattr(
        service,
        "validate_webhook_url",
        validate,
    )

    result = service._validate_url(
        "  https://example.com/webhook  "
    )

    assert result == (
        "https://example.com/webhook"
    )

    validate.assert_called_once_with(
        "https://example.com/webhook"
    )


def test_validate_url_rejects_empty_value() -> None:
    with pytest.raises(
        service.WebhookSubscriptionValidationError,
        match="Webhook URL is required",
    ):
        service._validate_url(
            " "
        )


@pytest.mark.parametrize(
    "transport_error",
    [
        PermanentWebhookDeliveryError(
            "Private webhook destinations are not allowed."
        ),
        TransientWebhookDeliveryError(
            "Webhook destination DNS lookup failed."
        ),
    ],
)
def test_validate_url_maps_transport_errors_to_validation_error(
    monkeypatch: pytest.MonkeyPatch,
    transport_error: Exception,
) -> None:
    validate = MagicMock(
        side_effect=transport_error
    )

    monkeypatch.setattr(
        service,
        "validate_webhook_url",
        validate,
    )

    with pytest.raises(
        service.WebhookSubscriptionValidationError,
        match=str(
            transport_error
        ),
    ):
        service._validate_url(
            "https://example.com/webhook"
        )


def test_create_webhook_subscription_persists_validated_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = MagicMock()
    organization_id = uuid.uuid4()

    monkeypatch.setattr(
        service,
        "_validate_url",
        MagicMock(
            return_value=(
                "https://example.com/webhook"
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_generate_secret",
        MagicMock(
            return_value="generated-secret"
        ),
    )

    encrypt_secret = MagicMock(
        return_value=EncryptedWebhookSecret(
            version=1,
            nonce=b"0123456789ab",
            ciphertext=b"encrypted-secret",
        )
    )

    monkeypatch.setattr(
        service,
        "_encrypt_secret",
        encrypt_secret,
    )

    result = (
        service.create_webhook_subscription(
            db,
            organization_id=organization_id,
            url="https://example.com/webhook",
            events=[
                "pattern",
                "anomaly",
            ],
        )
    )

    assert isinstance(
        result.subscription,
        WebhookSubscription,
    )

    assert (
        result.subscription.organization_id
        == organization_id
    )

    assert result.subscription.url == (
        "https://example.com/webhook"
    )

    assert result.subscription.events == [
        "pattern",
        "anomaly",
    ]

    assert (
        result.subscription.secret_ciphertext
        == b"encrypted-secret"
    )

    assert (
        result.subscription.secret_nonce
        == b"0123456789ab"
    )

    assert (
        result.subscription.secret_encryption_version
        == 1
    )

    assert result.subscription.is_active is True

    assert result.secret_key == (
        "generated-secret"
    )

    encrypt_secret.assert_called_once_with(
        "generated-secret",
        organization_id=organization_id,
        subscription_id=result.subscription.id,
    )

    db.add.assert_called_once_with(
        result.subscription
    )

    db.flush.assert_called_once_with()


def test_create_webhook_subscription_defaults_to_high_risk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = MagicMock()

    monkeypatch.setattr(
        service,
        "_validate_url",
        MagicMock(
            return_value=(
                "https://example.com/webhook"
            )
        ),
    )

    monkeypatch.setattr(
        service,
        "_generate_secret",
        MagicMock(
            return_value="generated-secret"
        ),
    )

    encrypt_secret = MagicMock(
        return_value=EncryptedWebhookSecret(
            version=1,
            nonce=b"0123456789ab",
            ciphertext=b"encrypted-secret",
        )
    )

    monkeypatch.setattr(
        service,
        "_encrypt_secret",
        encrypt_secret,
    )

    result = (
        service.create_webhook_subscription(
            db,
            organization_id=uuid.uuid4(),
            url="https://example.com/webhook",
        )
    )

    assert result.subscription.events == [
        "high_risk"
    ]

    encrypt_secret.assert_called_once_with(
        "generated-secret",
        organization_id=result.subscription.organization_id,
        subscription_id=result.subscription.id,
    )


def test_get_webhook_subscription_returns_tenant_owned_row() -> None:
    db = MagicMock()
    organization_id = uuid.uuid4()
    subscription_id = uuid.uuid4()

    subscription = WebhookSubscription(
        id=subscription_id,
        organization_id=organization_id,
        url="https://example.com/webhook",
        events=["high_risk"],
        secret_ciphertext=b"encrypted-secret",
        secret_nonce=b"0123456789ab",
        secret_encryption_version=1,
        is_active=True,
    )

    db.query.return_value.filter.return_value.first.return_value = (
        subscription
    )

    result = service.get_webhook_subscription(
        db,
        organization_id=organization_id,
        subscription_id=subscription_id,
    )

    assert result is subscription

    db.query.assert_called_once_with(
        WebhookSubscription
    )


def test_get_webhook_subscription_raises_when_row_is_not_visible() -> None:
    db = MagicMock()

    db.query.return_value.filter.return_value.first.return_value = (
        None
    )

    with pytest.raises(
        service.WebhookSubscriptionNotFoundError,
        match="Webhook subscription not found",
    ):
        service.get_webhook_subscription(
            db,
            organization_id=uuid.uuid4(),
            subscription_id=uuid.uuid4(),
        )


def test_list_webhook_subscriptions_returns_query_result() -> None:
    db = MagicMock()
    organization_id = uuid.uuid4()

    first = MagicMock(
        spec=WebhookSubscription
    )

    second = MagicMock(
        spec=WebhookSubscription
    )

    query = db.query.return_value
    filtered = query.filter.return_value
    ordered = filtered.order_by.return_value

    ordered.all.return_value = [
        first,
        second,
    ]

    result = service.list_webhook_subscriptions(
        db,
        organization_id=organization_id,
    )

    assert result == [
        first,
        second,
    ]

    db.query.assert_called_once_with(
        WebhookSubscription
    )


def test_rotate_webhook_secret_replaces_existing_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = MagicMock()
    organization_id = uuid.uuid4()
    subscription_id = uuid.uuid4()

    subscription = WebhookSubscription(
        id=subscription_id,
        organization_id=organization_id,
        url="https://example.com/webhook",
        events=["high_risk"],
        secret_ciphertext=b"encrypted-secret",
        secret_nonce=b"0123456789ab",
        secret_encryption_version=1,
        is_active=True,
    )

    get_subscription = MagicMock(
        return_value=subscription
    )

    monkeypatch.setattr(
        service,
        "get_webhook_subscription",
        get_subscription,
    )

    monkeypatch.setattr(
        service,
        "_generate_secret",
        MagicMock(
            return_value="new-secret"
        ),
    )

    encrypt_secret = MagicMock(
        return_value=EncryptedWebhookSecret(
            version=1,
            nonce=b"abcdefghijkl",
            ciphertext=b"rotated-ciphertext",
        )
    )

    monkeypatch.setattr(
        service,
        "_encrypt_secret",
        encrypt_secret,
    )

    result = service.rotate_webhook_secret(
        db,
        organization_id=organization_id,
        subscription_id=subscription_id,
    )

    assert result == "new-secret"

    assert (
        subscription.secret_ciphertext
        == b"rotated-ciphertext"
    )

    assert (
        subscription.secret_nonce
        == b"abcdefghijkl"
    )

    assert (
        subscription.secret_encryption_version
        == 1
    )

    get_subscription.assert_called_once_with(
        db,
        organization_id=organization_id,
        subscription_id=subscription_id,
    )

    encrypt_secret.assert_called_once_with(
        "new-secret",
        organization_id=organization_id,
        subscription_id=subscription_id,
    )

    db.flush.assert_called_once_with()


def test_deactivate_webhook_subscription_soft_disables_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = MagicMock()
    organization_id = uuid.uuid4()
    subscription_id = uuid.uuid4()

    subscription = WebhookSubscription(
        id=subscription_id,
        organization_id=organization_id,
        url="https://example.com/webhook",
        events=["high_risk"],
        secret_ciphertext=b"encrypted-secret",
        secret_nonce=b"0123456789ab",
        secret_encryption_version=1,
        is_active=True,
    )

    get_subscription = MagicMock(
        return_value=subscription
    )

    monkeypatch.setattr(
        service,
        "get_webhook_subscription",
        get_subscription,
    )

    result = (
        service.deactivate_webhook_subscription(
            db,
            organization_id=organization_id,
            subscription_id=subscription_id,
        )
    )

    assert result is subscription
    assert subscription.is_active is False

    get_subscription.assert_called_once_with(
        db,
        organization_id=organization_id,
        subscription_id=subscription_id,
    )

    db.flush.assert_called_once_with()