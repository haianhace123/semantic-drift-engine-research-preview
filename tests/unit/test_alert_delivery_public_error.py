"""Regression for the public AlertDelivery diagnostic projection."""

from src.domain.alert_delivery_public_error import public_alert_delivery_error


def test_no_error_stays_none():
    assert public_alert_delivery_error(None) is None


def test_safe_transport_status_is_preserved():
    assert public_alert_delivery_error("Webhook endpoint returned HTTP 503.") == (
        "Webhook endpoint returned HTTP 503."
    )


def test_known_operator_safe_message_is_preserved():
    assert public_alert_delivery_error("Webhook destination is not active.") == (
        "Webhook destination is not active."
    )


def test_historical_exception_with_secret_never_reaches_api():
    raw = "Webhook preparation failed: KeyError: SECRET_KEY=customer-key"
    projected = public_alert_delivery_error(raw)
    assert projected is not None
    assert "customer-key" not in projected
    assert "SECRET_KEY" not in projected


def test_untrusted_http_status_suffix_is_not_projected():
    raw = "Webhook endpoint returned HTTP 503. Authorization: Bearer secret"
    assert public_alert_delivery_error(raw) != raw


def test_non_string_error_not_exposed():
    assert "secret" not in public_alert_delivery_error({"token": "secret"})
