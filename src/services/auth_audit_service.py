"""Authentication security audit service."""

from __future__ import annotations

from typing import Any, Mapping
from uuid import UUID

from fastapi import Request
from sqlalchemy.orm import Session

from src.models.auth_security_event import AuthSecurityEvent


AUTH_SECURITY_EVENT_TYPES = frozenset(
    {
        "login_success",
        "login_failed",
        "account_locked",
        "password_changed",
        "session_revoked",
        "sessions_revoked_all",
        "account_deactivated",
        "email_verified",
        "verification_resent",
        "organization_owner_recovered",
    }
)


SENSITIVE_METADATA_KEYS = frozenset(
    {
        "password",
        "current_password",
        "new_password",
        "confirm_password",
        "password_hash",
        "refresh_token",
        "refresh_token_hash",
        "access_token",
        "token",
        "verification_token",
        "verification_code",
        "verification_code_hash",
        "otp",
        "reset_token",
        "api_key",
        "api_key_hash",
        "secret",
        "authorization",
        "cookie",
        "set_cookie",
    }
)


def _sanitize_metadata_value(
    value: Any,
) -> Any:
    """
    Convert metadata into JSON-safe primitive structures.

    Audit metadata must remain small and non-sensitive.
    """

    if value is None:
        return None

    if isinstance(
        value,
        (
            str,
            int,
            float,
            bool,
        ),
    ):
        return value

    if isinstance(
        value,
        UUID,
    ):
        return str(value)

    if isinstance(
        value,
        Mapping,
    ):
        return sanitize_auth_metadata(
            value
        )

    if isinstance(
        value,
        (
            list,
            tuple,
            set,
        ),
    ):
        return [
            _sanitize_metadata_value(
                item
            )
            for item in value
        ]

    return str(value)


def sanitize_auth_metadata(
    metadata: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """
    Remove known credential-like fields from audit metadata.

    Key matching is case-insensitive.
    """

    if not metadata:
        return {}

    sanitized: dict[str, Any] = {}

    for raw_key, value in metadata.items():
        key = str(
            raw_key
        )

        normalized_key = (
            key.strip().lower()
        )

        if (
            normalized_key
            in SENSITIVE_METADATA_KEYS
        ):
            continue

        sanitized[key] = (
            _sanitize_metadata_value(
                value
            )
        )

    return sanitized


def _request_ip(
    request: Request | None,
) -> str | None:
    if request is None:
        return None

    if request.client is None:
        return None

    return request.client.host


def _request_user_agent(
    request: Request | None,
) -> str | None:
    if request is None:
        return None

    user_agent = request.headers.get(
        "user-agent"
    )

    if not user_agent:
        return None

    return user_agent[:2000]


def record_auth_security_event(
    db: Session,
    *,
    event_type: str,
    organization_id: UUID | None = None,
    user_id: UUID | None = None,
    request: Request | None = None,
    metadata: Mapping[str, Any] | None = None,
    flush: bool = True,
) -> AuthSecurityEvent:
    """
    Add one authentication security event to the current transaction.

    This function intentionally does not commit. The caller owns the
    surrounding transaction so the audit event and security-sensitive
    state change can be committed atomically whenever possible.
    """

    if (
        event_type
        not in AUTH_SECURITY_EVENT_TYPES
    ):
        raise ValueError(
            "Unsupported authentication "
            f"security event type: {event_type}"
        )

    sanitized_metadata = (
        sanitize_auth_metadata(
            metadata
        )
    )

    event = AuthSecurityEvent(
        organization_id=organization_id,
        user_id=user_id,
        event_type=event_type,
        ip_address=_request_ip(
            request
        ),
        user_agent=_request_user_agent(
            request
        ),
        metadata_json=(
            sanitized_metadata
            if sanitized_metadata
            else None
        ),
    )

    db.add(
        event
    )

    if flush:
        db.flush()

    return event