from __future__ import annotations

import os
import uuid
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from src.api import auth_routes
from src.core.database import SessionLocal
from src.core.security import (
    CSRF_COOKIE,
    hash_password,
    now_utc,
    token_hash,
)
from src.main import app
from src.models import (
    Organization,
    User,
)
from src.models.auth_security_event import AuthSecurityEvent


RUN_AUTH_INTEGRATION_TESTS = (
    os.getenv(
        "RUN_AUTH_INTEGRATION_TESTS",
        "",
    )
    == "1"
)


pytestmark = pytest.mark.skipif(
    not RUN_AUTH_INTEGRATION_TESTS,
    reason=(
        "Set RUN_AUTH_INTEGRATION_TESTS=1 "
        "to run authentication integration tests."
    ),
)


TEST_PASSWORD = (
    "AuthVerification!2026"
)


def _new_identity():
    suffix = uuid.uuid4().hex[:12]

    return {
        "email": (
            f"auth-verification-{suffix}"
            "@example.test"
        ),
        "slug": (
            f"auth-verification-{suffix}"
        ),
    }


def _create_user(
    *,
    verified: bool = False,
    verification_code: str | None = None,
    verification_expired: bool = False,
):
    identity = _new_identity()

    db = SessionLocal()

    try:
        organization = Organization(
            name=(
                "Auth verification integration"
            ),
            slug=identity["slug"],
            billing_email=identity["email"],
            subscription_tier="free",
            quota_urls=10,
            quota_requests=1000,
            is_active=True,
        )

        db.add(
            organization
        )

        db.flush()

        user = User(
            organization_id=organization.id,
            email=identity["email"],
            password_hash=hash_password(
                TEST_PASSWORD
            ),
            full_name=(
                "Auth Verification Test"
            ),
            role="user",
            is_active=True,
            email_verified=verified,
            login_attempts=0,
        )

        if verification_code is not None:
            user.verification_code_hash = (
                token_hash(
                    verification_code
                )
            )

            if verification_expired:
                user.verification_code_expires = (
                    now_utc()
                    - timedelta(minutes=1)
                )

            else:
                user.verification_code_expires = (
                    now_utc()
                    + timedelta(minutes=15)
                )

        db.add(
            user
        )

        db.commit()

        db.refresh(
            organization
        )

        db.refresh(
            user
        )

        return (
            organization.id,
            user.id,
            user.email,
        )

    finally:
        db.close()


def _delete_identity(
    organization_id,
) -> None:
    db = SessionLocal()

    try:
        db.query(
            Organization
        ).filter(
            Organization.id
            == organization_id
        ).delete(
            synchronize_session=False
        )

        db.commit()

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def _csrf(
    client: TestClient,
) -> str:
    response = client.get(
        "/api/auth/csrf"
    )

    assert (
        response.status_code
        == 200
    ), response.text

    token = client.cookies.get(
        CSRF_COOKIE
    )

    assert token

    return token


def _user_state(
    user_id,
) -> dict:
    db = SessionLocal()

    try:
        user = (
            db.query(User)
            .filter(
                User.id == user_id
            )
            .first()
        )

        assert user is not None

        return {
            "email_verified":
                user.email_verified,

            "verification_code_hash":
                user.verification_code_hash,

            "verification_code_expires":
                user.verification_code_expires,

            "verification_token":
                user.verification_token,

            "verification_token_expires":
                user.verification_token_expires,
        }

    finally:
        db.close()

def _auth_events(
    user_id,
):
    db = SessionLocal()

    try:
        events = (
            db.query(
                AuthSecurityEvent
            )
            .filter(
                AuthSecurityEvent.user_id
                == user_id
            )
            .order_by(
                AuthSecurityEvent.created_at.asc(),
                AuthSecurityEvent.event_type.asc(),
            )
            .all()
        )

        return [
            {
                "event_type":
                    event.event_type,

                "organization_id":
                    event.organization_id,

                "user_id":
                    event.user_id,

                "metadata_json":
                    event.metadata_json,
            }
            for event in events
        ]

    finally:
        db.close()

# ============================================================
# VERIFY OTP
# ============================================================

def test_verify_otp_success():
    code = "123456"

    (
        organization_id,
        user_id,
        email,
    ) = _create_user(
        verification_code=code,
    )

    client = TestClient(
        app
    )

    try:
        csrf_token = _csrf(
            client
        )

        response = client.post(
            "/api/auth/verify-otp",
            headers={
                "X-CSRF-Token":
                    csrf_token,
            },
            json={
                "email":
                    email,

                "code":
                    code,
            },
        )

        assert (
            response.status_code
            == 200
        ), response.text

        state = _user_state(
            user_id
        )

        assert (
            state["email_verified"]
            is True
        )

        assert (
            state[
                "verification_code_hash"
            ]
            is None
        )

        assert (
            state[
                "verification_code_expires"
            ]
            is None
        )

        assert (
            state[
                "verification_token"
            ]
            is None
        )

        assert (
            state[
                "verification_token_expires"
            ]
            is None
        )

        events = _auth_events(
            user_id
        )

        verified_events = [
            event
            for event in events
            if (
                event["event_type"]
                == "email_verified"
            )
        ]

        assert (
            len(verified_events)
            == 1
        )

        event = verified_events[0]

        assert (
            event["organization_id"]
            == organization_id
        )

        assert (
            event["user_id"]
            == user_id
        )

        assert (
            event["metadata_json"]
            == {
                "method":
                    "otp",
            }
        )

    finally:
        client.close()

        _delete_identity(
            organization_id
        )


def test_verify_otp_invalid_code():
    (
        organization_id,
        user_id,
        email,
    ) = _create_user(
        verification_code="123456",
    )

    client = TestClient(
        app
    )

    try:
        csrf_token = _csrf(
            client
        )

        response = client.post(
            "/api/auth/verify-otp",
            headers={
                "X-CSRF-Token":
                    csrf_token,
            },
            json={
                "email":
                    email,

                "code":
                    "654321",
            },
        )

        assert (
            response.status_code
            == 400
        ), response.text

        payload = response.json()

        assert (
            payload[
                "detail"
            ]["error"]["code"]
            == "AUTH_010"
        )

        state = _user_state(
            user_id
        )

        assert (
            state["email_verified"]
            is False
        )

        assert (
            state[
                "verification_code_hash"
            ]
            is not None
        )

    finally:
        client.close()

        _delete_identity(
            organization_id
        )


def test_verify_otp_expired_code():
    (
        organization_id,
        user_id,
        email,
    ) = _create_user(
        verification_code="123456",
        verification_expired=True,
    )

    client = TestClient(
        app
    )

    try:
        csrf_token = _csrf(
            client
        )

        response = client.post(
            "/api/auth/verify-otp",
            headers={
                "X-CSRF-Token":
                    csrf_token,
            },
            json={
                "email":
                    email,

                "code":
                    "123456",
            },
        )

        assert (
            response.status_code
            == 400
        ), response.text

        assert (
            _user_state(
                user_id
            )["email_verified"]
            is False
        )

    finally:
        client.close()

        _delete_identity(
            organization_id
        )


def test_verify_email_code_legacy_alias():
    code = "246810"

    (
        organization_id,
        user_id,
        email,
    ) = _create_user(
        verification_code=code,
    )

    client = TestClient(
        app
    )

    try:
        csrf_token = _csrf(
            client
        )

        response = client.post(
            "/api/auth/verify-email-code",
            headers={
                "X-CSRF-Token":
                    csrf_token,
            },
            json={
                "email":
                    email,

                "code":
                    code,
            },
        )

        assert (
            response.status_code
            == 200
        ), response.text

        assert (
            _user_state(
                user_id
            )["email_verified"]
            is True
        )

    finally:
        client.close()

        _delete_identity(
            organization_id
        )


# ============================================================
# RESEND VERIFICATION
# ============================================================

def test_resend_unknown_email_is_generic():
    client = TestClient(
        app
    )

    try:
        csrf_token = _csrf(
            client
        )

        missing_email = (
            "missing-"
            f"{uuid.uuid4().hex[:12]}"
            "@example.test"
        )

        response = client.post(
            "/api/auth/resend-verification",
            headers={
                "X-CSRF-Token":
                    csrf_token,
            },
            json={
                "email":
                    missing_email,
            },
        )

        assert (
            response.status_code
            == 200
        ), response.text

        message = (
            response.json()[
                "message"
            ].lower()
        )

        assert (
            "not found"
            not in message
        )

        assert (
            "does not exist"
            not in message
        )

    finally:
        client.close()


def test_resend_verified_email_is_generic():
    (
        organization_id,
        _user_id,
        email,
    ) = _create_user(
        verified=True,
    )

    client = TestClient(
        app
    )

    try:
        csrf_token = _csrf(
            client
        )

        response = client.post(
            "/api/auth/resend-verification",
            headers={
                "X-CSRF-Token":
                    csrf_token,
            },
            json={
                "email":
                    email,
            },
        )

        assert (
            response.status_code
            == 200
        ), response.text

        message = (
            response.json()[
                "message"
            ].lower()
        )

        assert (
            "already verified"
            not in message
        )

    finally:
        client.close()

        _delete_identity(
            organization_id
        )


def test_resend_rotates_verification_material(
    monkeypatch,
):
    (
        organization_id,
        user_id,
        email,
    ) = _create_user(
        verification_code="123456",
    )

    before = _user_state(
        user_id
    )

    queued = []

    def fake_enqueue_email(
        *,
        to,
        subject,
        html,
    ):
        queued.append(
            {
                "to":
                    to,

                "subject":
                    subject,

                "html":
                    html,
            }
        )

    monkeypatch.setattr(
        auth_routes,
        "enqueue_email",
        fake_enqueue_email,
    )

    client = TestClient(
        app
    )

    try:
        csrf_token = _csrf(
            client
        )

        response = client.post(
            "/api/auth/resend-verification",
            headers={
                "X-CSRF-Token":
                    csrf_token,
            },
            json={
                "email":
                    email,
            },
        )

        assert (
            response.status_code
            == 200
        ), response.text

        assert len(
            queued
        ) == 1

        assert (
            queued[0]["to"]
            == email
        )

        after = _user_state(
            user_id
        )

        assert (
            after[
                "verification_code_hash"
            ]
            is not None
        )

        assert (
            after[
                "verification_code_hash"
            ]
            != before[
                "verification_code_hash"
            ]
        )

        assert (
            after[
                "verification_token"
            ]
            is not None
        )

        assert (
            after[
                "verification_token_expires"
            ]
            is not None
        )

        assert (
            after[
                "verification_code_expires"
            ]
            is not None
        )

        events = _auth_events(
            user_id
        )

        resent_events = [
            event
            for event in events
            if (
                event["event_type"]
                == "verification_resent"
            )
        ]

        assert (
            len(resent_events)
            == 1
        )

        event = resent_events[0]

        assert (
            event["organization_id"]
            == organization_id
        )

        assert (
            event["user_id"]
            == user_id
        )

        assert (
            event["metadata_json"]
            == {
                "method":
                    "email",
            }
        )

    finally:
        client.close()

        _delete_identity(
            organization_id
        )


def test_resend_queue_failure_rolls_back(
    monkeypatch,
):
    (
        organization_id,
        user_id,
        email,
    ) = _create_user(
        verification_code="123456",
    )

    before = _user_state(
        user_id
    )

    def fail_enqueue_email(
        *,
        to,
        subject,
        html,
    ):
        raise RuntimeError(
            "simulated queue failure"
        )

    monkeypatch.setattr(
        auth_routes,
        "enqueue_email",
        fail_enqueue_email,
    )

    client = TestClient(
        app
    )

    try:
        csrf_token = _csrf(
            client
        )

        response = client.post(
            "/api/auth/resend-verification",
            headers={
                "X-CSRF-Token":
                    csrf_token,
            },
            json={
                "email":
                    email,
            },
        )

        # Queue failure must not reveal an unverified account's existence.
        assert response.status_code == 200, response.text
        assert "sent" not in response.json()["message"].lower()

        unknown = client.post(
            "/api/auth/resend-verification",
            headers={"X-CSRF-Token": csrf_token},
            json={"email": f"missing-{uuid.uuid4().hex}@example.test"},
        )
        assert unknown.status_code == response.status_code
        assert unknown.json() == response.json()

        after = _user_state(
            user_id
        )

        assert (
            after[
                "verification_code_hash"
            ]
            == before[
                "verification_code_hash"
            ]
        )

        assert (
            after[
                "verification_code_expires"
            ]
            == before[
                "verification_code_expires"
            ]
        )

        assert (
            after[
                "verification_token"
            ]
            == before[
                "verification_token"
            ]
        )

        assert (
            after[
                "verification_token_expires"
            ]
            == before[
                "verification_token_expires"
            ]
        )

        events = _auth_events(
            user_id
        )

        resent_events = [
            event
            for event in events
            if (
                event["event_type"]
                == "verification_resent"
            )
        ]

        assert (
            resent_events
            == []
        )

    finally:
        client.close()

        _delete_identity(
            organization_id
        )

def test_resend_verification_has_sixty_second_cooldown(
    monkeypatch,
):
    (
        organization_id,
        _user_id,
        email,
    ) = _create_user(
        verification_code="123456",
    )

    queued = []

    def fake_enqueue_email(
        *,
        to,
        subject,
        html,
    ):
        queued.append(
            {
                "to": to,
                "subject": subject,
                "html": html,
            }
        )

    monkeypatch.setattr(
        auth_routes,
        "enqueue_email",
        fake_enqueue_email,
    )

    client = TestClient(app)

    try:
        csrf_token = _csrf(
            client
        )

        # First resend is allowed.
        response = client.post(
            "/api/auth/resend-verification",
            headers={
                "X-CSRF-Token":
                    csrf_token,
            },
            json={
                "email": email,
            },
        )

        assert (
            response.status_code
            == 200
        ), response.text

        assert len(queued) == 1

        # Immediate second resend must hit
        # the 60-second cooldown.
        response = client.post(
            "/api/auth/resend-verification",
            headers={
                "X-CSRF-Token":
                    csrf_token,
            },
            json={
                "email": email,
            },
        )

        assert (
            response.status_code
            == 429
        ), response.text

        payload = response.json()

        assert (
            payload[
                "detail"
            ]["error"]["code"]
            == "AUTH_017"
        )

        details = (
            payload[
                "detail"
            ]["error"].get(
                "details"
            )
            or {}
        )

        retry_after = details.get(
            "retry_after"
        )

        assert retry_after is not None

        assert (
            0 < retry_after <= 60
        )

        # Cooldown rejection must not queue
        # another verification email.
        assert len(queued) == 1

    finally:
        client.close()

        _delete_identity(
            organization_id
        )

def test_verify_otp_rate_limit_uses_auth_018():
    (
        organization_id,
        _user_id,
        email,
    ) = _create_user(
        verification_code="123456",
    )

    client = TestClient(app)

    try:
        csrf_token = _csrf(
            client
        )

        # First five attempts are allowed through
        # the limiter and fail as invalid OTP.
        for attempt in range(1, 6):
            response = client.post(
                "/api/auth/verify-otp",
                headers={
                    "X-CSRF-Token":
                        csrf_token,
                },
                json={
                    "email":
                        email,

                    "code":
                        "654321",
                },
            )

            assert (
                response.status_code
                == 400
            ), (
                f"attempt={attempt} "
                f"response={response.text}"
            )

            assert (
                response.json()[
                    "detail"
                ]["error"]["code"]
                == "AUTH_010"
            )

        # Sixth request must be rejected
        # by the OTP-specific limiter.
        response = client.post(
            "/api/auth/verify-otp",
            headers={
                "X-CSRF-Token":
                    csrf_token,
            },
            json={
                "email":
                    email,

                "code":
                    "654321",
            },
        )

        assert (
            response.status_code
            == 429
        ), response.text

        payload = response.json()

        assert (
            payload[
                "detail"
            ]["error"]["code"]
            == "AUTH_018"
        )

        details = (
            payload[
                "detail"
            ]["error"].get(
                "details"
            )
            or {}
        )

        retry_after = details.get(
            "retry_after"
        )

        assert retry_after is not None

        assert (
            0 < retry_after <= 900
        )

    finally:
        client.close()

        _delete_identity(
            organization_id
        )

def test_verify_email_path_token_alias():
    raw_token = "canonical-verification-token"

    (
        organization_id,
        user_id,
        email,
    ) = _create_user(
        verification_code=None,
    )

    db = SessionLocal()

    try:
        user = (
            db.query(User)
            .filter(
                User.id == user_id
            )
            .first()
        )

        assert user is not None

        user.verification_token = (
            token_hash(
                raw_token
            )
        )

        user.verification_token_expires = (
            now_utc()
            + timedelta(hours=1)
        )

        db.commit()

    finally:
        db.close()

    client = TestClient(
        app,
        follow_redirects=False,
    )

    try:
        response = client.get(
            (
                "/api/auth/"
                f"verify-email/{raw_token}"
            )
        )

        assert response.status_code in (
            302,
            303,
            307,
            308,
        ), response.text

        state = _user_state(
            user_id
        )

        assert (
            state["email_verified"]
            is True
        )

        assert (
            state[
                "verification_token"
            ]
            is None
        )

        assert (
            state[
                "verification_token_expires"
            ]
            is None
        )

        events = _auth_events(
            user_id
        )

        verified_events = [
            event
            for event in events
            if (
                event["event_type"]
                == "email_verified"
            )
        ]

        assert (
            len(verified_events)
            == 1
        )

        event = verified_events[0]

        assert (
            event["organization_id"]
            == organization_id
        )

        assert (
            event["user_id"]
            == user_id
        )

        assert (
            event["metadata_json"]
            == {
                "method":
                    "link",
            }
        )

    finally:
        client.close()

        _delete_identity(
            organization_id
        )

# ============================================================
# EMAIL QUEUE ADMISSION AND AUTH STATE
# ============================================================


def test_signup_verification_queue_failure_is_truthful_and_recoverable(
    monkeypatch,
):
    email = f"signup-queue-{uuid.uuid4().hex[:16]}@example.test"
    organization_id = None

    def unavailable(*, to, subject, html):
        raise RuntimeError("simulated unavailable queue")

    monkeypatch.setattr(auth_routes, "enqueue_email", unavailable)
    client = TestClient(app)
    try:
        csrf_token = _csrf(client)
        response = client.post(
            "/api/auth/signup",
            headers={"X-CSRF-Token": csrf_token},
            json={
                "email": email,
                "password": TEST_PASSWORD,
                "confirm_password": TEST_PASSWORD,
                "full_name": "Queue Admission Test",
                "terms_accepted": True,
            },
        )
        assert response.status_code == 503, response.text
        error = response.json()["detail"]["error"]
        assert error["code"] == "AUTH_019"
        assert error["details"]["account_created"] is True
        assert "could not be queued" in error["message"]

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == email).one()
            assert user.email_verified is False
            assert user.verification_code_hash is not None
            organization_id = user.organization_id
        finally:
            db.close()

        # The failed signup can be recovered without signing up twice.
        published = []
        monkeypatch.setattr(
            auth_routes,
            "enqueue_email",
            lambda **kwargs: published.append(kwargs),
        )
        resend = client.post(
            "/api/auth/resend-verification",
            headers={"X-CSRF-Token": csrf_token},
            json={"email": email},
        )
        assert resend.status_code == 200, resend.text
        assert len(published) == 1
    finally:
        client.close()
        if organization_id is not None:
            _delete_identity(organization_id)


def test_signup_optional_welcome_queue_failure_does_not_mask_verification(
    monkeypatch,
):
    email = f"signup-welcome-{uuid.uuid4().hex[:16]}@example.test"
    organization_id = None
    published = []

    def enqueue(*, to, subject, html):
        published.append(subject)
        if len(published) == 2:
            raise RuntimeError("welcome queue unavailable")

    monkeypatch.setattr(auth_routes, "enqueue_email", enqueue)
    client = TestClient(app)
    try:
        csrf_token = _csrf(client)
        response = client.post(
            "/api/auth/signup",
            headers={"X-CSRF-Token": csrf_token},
            json={
                "email": email,
                "password": TEST_PASSWORD,
                "confirm_password": TEST_PASSWORD,
                "full_name": "Welcome Queue Test",
                "terms_accepted": True,
            },
        )
        assert response.status_code == 200, response.text
        assert "queued" in response.json()["message"].lower()
        assert len(published) == 2

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.email == email).one()
            organization_id = user.organization_id
        finally:
            db.close()
    finally:
        client.close()
        if organization_id is not None:
            _delete_identity(organization_id)


def test_forgot_password_queue_failure_preserves_old_token_and_masks_identity(
    monkeypatch,
):
    organization_id, user_id, email = _create_user(verified=True)
    old_digest = token_hash("existing-still-usable-reset-token")
    old_expiry = now_utc() + timedelta(minutes=25)
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).one()
        user.reset_token = old_digest
        user.reset_token_expires = old_expiry
        db.commit()
    finally:
        db.close()

    def unavailable(*, to, subject, html):
        raise RuntimeError("simulated queue unavailable")

    monkeypatch.setattr(auth_routes, "enqueue_email", unavailable)
    client = TestClient(app)
    try:
        csrf_token = _csrf(client)
        known = client.post(
            "/api/auth/forgot-password",
            headers={"X-CSRF-Token": csrf_token},
            json={"email": email},
        )
        missing = client.post(
            "/api/auth/forgot-password",
            headers={"X-CSRF-Token": csrf_token},
            json={"email": f"missing-{uuid.uuid4().hex}@example.test"},
        )
        assert known.status_code == missing.status_code == 200
        assert known.json() == missing.json()
        assert "sent" not in known.json()["message"].lower()

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == user_id).one()
            assert user.reset_token == old_digest
            assert user.reset_token_expires == old_expiry
        finally:
            db.close()
    finally:
        client.close()
        _delete_identity(organization_id)


def test_forgot_password_success_persists_queued_reset_token(monkeypatch):
    import re
    from urllib.parse import unquote

    organization_id, user_id, email = _create_user(verified=True)
    published = []
    monkeypatch.setattr(
        auth_routes,
        "enqueue_email",
        lambda **kwargs: published.append(kwargs),
    )
    client = TestClient(app)
    try:
        csrf_token = _csrf(client)
        response = client.post(
            "/api/auth/forgot-password",
            headers={"X-CSRF-Token": csrf_token},
            json={"email": email},
        )
        assert response.status_code == 200, response.text
        assert len(published) == 1
        match = re.search(r"\?token=([^\s\"<>&]+)", published[0]["html"])
        assert match is not None
        digest = token_hash(unquote(match.group(1)))

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == user_id).one()
            assert user.reset_token == digest
            assert user.reset_token_expires > now_utc()
        finally:
            db.close()
    finally:
        client.close()
        _delete_identity(organization_id)
