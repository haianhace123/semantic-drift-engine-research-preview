"""Public projection for persisted webhook delivery diagnostics.

Never pass raw exception messages through tenant API responses. Old records can
contain exception details created before this boundary was established.
"""

from __future__ import annotations

import re


_UNKNOWN_FAILURE = "Webhook delivery failed; detailed diagnostics are restricted."

# Only literal, application-owned messages from the transport and delivery
# implementation may be displayed. Unknown/historical error strings fail
# closed rather than attempting unreliable secret redaction with regex.
_SAFE_MESSAGES = frozenset({
    "Webhook payload is not JSON serializable.",
    "Webhook secret key is empty.",
    "Webhook timestamp must not be negative.",
    "Webhook delivery id is empty.",
    "Webhook delivery key is empty.",
    "Webhook hostname is invalid.",
    "Webhook hostname could not be resolved.",
    "Webhook hostname resolved to no addresses.",
    "Webhook hostname resolved to an invalid address.",
    "Webhook destination resolves to a non-public network address.",
    "Webhook URL is empty.",
    "Webhook URL is invalid.",
    "Webhook URL must use HTTPS.",
    "Webhook URL must not contain credentials.",
    "Webhook URL must contain a hostname.",
    "Webhook URL contains an invalid port.",
    "Webhook timeout must be greater than zero.",
    "Webhook transport request failed.",
    "Webhook secret storage representation is incomplete.",
    "Webhook destination no longer exists.",
    "Webhook destination is not active.",
    "Webhook destination URL is empty.",
    "Webhook preparation failed unexpectedly.",
    "Webhook transport failed unexpectedly.",
})
_HTTP_STATUS = re.compile(r"Webhook endpoint returned HTTP [1-5][0-9]{2}\.")


def public_alert_delivery_error(error: object | None) -> str | None:
    """Return a safe tenant-visible diagnostic without exposing arbitrary text."""
    if error is None:
        return None
    if not isinstance(error, str):
        return _UNKNOWN_FAILURE
    if error in _SAFE_MESSAGES or _HTTP_STATUS.fullmatch(error):
        return error
    return _UNKNOWN_FAILURE
