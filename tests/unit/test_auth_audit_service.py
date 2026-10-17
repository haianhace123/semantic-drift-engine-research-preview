from __future__ import annotations

import uuid

import pytest
from starlette.requests import Request

from src.models.auth_security_event import (
    AuthSecurityEvent,
)
from src.services.auth_audit_service import (
    AUTH_SECURITY_EVENT_TYPES,
    record_auth_security_event,
    sanitize_auth_metadata,
)


class FakeDB:
    def __init__(self):
        self.added = []
        self.flush_count = 0

    def add(
        self,
        value,
    ):
        self.added.append(
            value
        )

    def flush(
        self,
    ):
        self.flush_count += 1


def _request():
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/auth/login",
        "headers": [
            (
                b"user-agent",
                b"Auth Audit Test Agent",
            ),
        ],
        "client": (
            "203.0.113.10",
            54321,
        ),
        "server": (
            "testserver",
            80,
        ),
        "scheme": "http",
        "query_string": b"",
    }

    return Request(
        scope
    )


def test_event_types_are_expected():
    assert AUTH_SECURITY_EVENT_TYPES == {
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


def test_sanitize_auth_metadata_removes_secrets():
    metadata = {
        "reason":
            "invalid_password",

        "password":
            "NeverStoreMe",

        "refresh_token":
            "NeverStoreMe",

        "verification_code":
            "123456",

        "token":
            "NeverStoreMe",

        "api_key":
            "NeverStoreMe",

        "nested": {
            "safe":
                "yes",

            "password":
                "NeverStoreMe",
        },
    }

    result = sanitize_auth_metadata(
        metadata
    )

    assert result["reason"] == (
        "invalid_password"
    )

    assert "password" not in result
    assert "refresh_token" not in result
    assert "verification_code" not in result
    assert "token" not in result
    assert "api_key" not in result

    assert result["nested"] == {
        "safe": "yes"
    }


def test_sanitize_auth_metadata_handles_uuid():
    value = uuid.uuid4()

    result = sanitize_auth_metadata(
        {
            "session_id":
                value,
        }
    )

    assert result == {
        "session_id":
            str(value),
    }


def test_record_auth_security_event():
    db = FakeDB()

    organization_id = (
        uuid.uuid4()
    )

    user_id = uuid.uuid4()

    event = (
        record_auth_security_event(
            db,
            event_type="login_failed",
            organization_id=(
                organization_id
            ),
            user_id=user_id,
            request=_request(),
            metadata={
                "reason":
                    "invalid_password",

                "password":
                    "NeverStoreMe",
            },
        )
    )

    assert isinstance(
        event,
        AuthSecurityEvent,
    )

    assert (
        event.organization_id
        == organization_id
    )

    assert (
        event.user_id
        == user_id
    )

    assert (
        event.event_type
        == "login_failed"
    )

    assert (
        event.ip_address
        == "203.0.113.10"
    )

    assert (
        event.user_agent
        == "Auth Audit Test Agent"
    )

    assert event.metadata_json == {
        "reason":
            "invalid_password"
    }

    assert db.added == [
        event
    ]

    assert (
        db.flush_count == 1
    )


def test_record_without_request():
    db = FakeDB()

    event = (
        record_auth_security_event(
            db,
            event_type=(
                "password_changed"
            ),
            request=None,
        )
    )

    assert (
        event.ip_address
        is None
    )

    assert (
        event.user_agent
        is None
    )


def test_record_can_skip_flush():
    db = FakeDB()

    record_auth_security_event(
        db,
        event_type="login_success",
        flush=False,
    )

    assert (
        db.flush_count == 0
    )


def test_unknown_event_type_is_rejected():
    db = FakeDB()

    with pytest.raises(
        ValueError,
    ):
        record_auth_security_event(
            db,
            event_type=(
                "something_unknown"
            ),
        )

    assert db.added == []