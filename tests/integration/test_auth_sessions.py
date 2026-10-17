from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

from src.core.database import SessionLocal
from src.core.security import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    REFRESH_COOKIE,
    decode_token,
    hash_password,
    now_utc,
)
from src.main import app
from src.models import (
    Organization,
    User,
    UserSession,
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
    "AuthSessionTest!123"
)

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

                "ip_address":
                    event.ip_address,

                "user_agent":
                    event.user_agent,

                "metadata_json":
                    event.metadata_json,
            }
            for event in events
        ]

    finally:
        db.close()

def _new_test_identity():
    suffix = uuid.uuid4().hex[:12]

    return {
        "email": (
            f"auth-session-{suffix}"
            "@example.test"
        ),
        "slug": (
            f"auth-session-{suffix}"
        ),
    }


def _create_verified_user():
    identity = (
        _new_test_identity()
    )

    db = SessionLocal()

    try:
        organization = Organization(
            name="Auth session integration",
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
            full_name="Auth Session Test",
            role="user",
            is_active=True,
            email_verified=True,
            login_attempts=0,
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


def _delete_test_identity(
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

    assert response.status_code == 200

    token = client.cookies.get(
        CSRF_COOKIE
    )

    assert token

    return token


def test_csrf_bootstrap_returns_and_reuses_token():

    csrf_client = TestClient(
        app
    )

    first_response = csrf_client.get(
        "/api/auth/csrf"
    )

    assert first_response.status_code == 200

    token = csrf_client.cookies.get(
        CSRF_COOKIE
    )

    assert token
    assert first_response.json() == {
        "ok":
            True,

        "csrf_token":
            token
    }

    second_response = csrf_client.get(
        "/api/auth/csrf"
    )

    assert second_response.status_code == 200
    assert csrf_client.cookies.get(
        CSRF_COOKIE
    ) == token
    assert second_response.json() == {
        "ok":
            True,

        "csrf_token":
            token
    }


def _login(
    client: TestClient,
    *,
    email: str,
    remember_me: bool = False,
) -> str:
    csrf_token = _csrf(
        client
    )

    response = client.post(
        "/api/auth/login",
        headers={
            "X-CSRF-Token": csrf_token,
        },
        json={
            "email": email,
            "password": TEST_PASSWORD,
            "remember_me": remember_me,
        },
    )

    assert (
        response.status_code
        == 200
    ), response.text

    refresh = client.cookies.get(
        REFRESH_COOKIE
    )

    assert refresh

    return refresh

def _refresh_set_cookie(
    response,
) -> str:

    prefix = (
        f"{REFRESH_COOKIE}="
    )

    headers = (
        response.headers.get_list(
            "set-cookie"
        )
    )

    matches = [
        header
        for header in headers
        if header.startswith(
            prefix
        )
    ]

    assert len(matches) == 1

    return matches[0]


def _assert_access_token_rejected(
    access_token: str,
) -> None:
    client = TestClient(
        app
    )

    try:
        response = client.get(
            "/api/auth/sessions",
            headers={
                "Authorization":
                    f"Bearer {access_token}",
            },
        )

        assert (
            response.status_code
            == 401
        ), response.text

    finally:
        client.close()


def _refresh(
    client: TestClient,
):
    csrf_token = client.cookies.get(
        CSRF_COOKIE
    )

    assert csrf_token

    return client.post(
        "/api/auth/refresh",
        headers={
            "X-CSRF-Token": csrf_token,
        },
    )


def _logout(
    client: TestClient,
):
    csrf_token = client.cookies.get(
        CSRF_COOKIE
    )

    assert csrf_token

    return client.post(
        "/api/auth/logout",
        headers={
            "X-CSRF-Token": csrf_token,
        },
    )


def _active_sessions(
    user_id,
):
    db = SessionLocal()

    try:
        return (
            db.query(
                UserSession
            )
            .filter(
                UserSession.user_id
                == user_id,
                UserSession.is_revoked.is_(
                    False
                ),
                UserSession.revoked_at.is_(
                    None
                ),
            )
            .all()
        )

    finally:
        db.close()


def test_remember_me_cookie_persistence_survives_refresh():

    (
        organization_id,
        _user_id,
        email,
    ) = _create_verified_user()

    session_client = TestClient(
        app
    )

    persistent_client = TestClient(
        app
    )

    try:
        # ----------------------------------------------------
        # remember_me=False
        # ----------------------------------------------------

        csrf_token = _csrf(
            session_client
        )

        response = session_client.post(
            "/api/auth/login",
            headers={
                "X-CSRF-Token":
                    csrf_token,
            },
            json={
                "email": email,
                "password":
                    TEST_PASSWORD,
                "remember_me": False,
            },
        )

        assert (
            response.status_code
            == 200
        ), response.text

        refresh_cookie = (
            _refresh_set_cookie(
                response
            )
        )

        assert (
            "max-age="
            not in
            refresh_cookie.lower()
        )

        refresh_1 = (
            session_client.cookies.get(
                REFRESH_COOKIE
            )
        )

        assert refresh_1

        payload_1 = decode_token(
            refresh_1,
            "refresh",
        )

        assert (
            payload_1[
                "remember_me"
            ]
            is False
        )

        response = _refresh(
            session_client
        )

        assert (
            response.status_code
            == 200
        ), response.text

        rotated_cookie = (
            _refresh_set_cookie(
                response
            )
        )

        assert (
            "max-age="
            not in
            rotated_cookie.lower()
        )

        refresh_2 = (
            session_client.cookies.get(
                REFRESH_COOKIE
            )
        )

        assert refresh_2

        assert (
            refresh_2
            != refresh_1
        )

        payload_2 = decode_token(
            refresh_2,
            "refresh",
        )

        assert (
            payload_2[
                "remember_me"
            ]
            is False
        )

        # ----------------------------------------------------
        # remember_me=True
        # ----------------------------------------------------

        csrf_token = _csrf(
            persistent_client
        )

        response = persistent_client.post(
            "/api/auth/login",
            headers={
                "X-CSRF-Token":
                    csrf_token,
            },
            json={
                "email": email,
                "password":
                    TEST_PASSWORD,
                "remember_me": True,
            },
        )

        assert (
            response.status_code
            == 200
        ), response.text

        refresh_cookie = (
            _refresh_set_cookie(
                response
            )
        )

        assert (
            "max-age="
            in
            refresh_cookie.lower()
        )

        refresh_1 = (
            persistent_client.cookies.get(
                REFRESH_COOKIE
            )
        )

        assert refresh_1

        payload_1 = decode_token(
            refresh_1,
            "refresh",
        )

        assert (
            payload_1[
                "remember_me"
            ]
            is True
        )

        response = _refresh(
            persistent_client
        )

        assert (
            response.status_code
            == 200
        ), response.text

        rotated_cookie = (
            _refresh_set_cookie(
                response
            )
        )

        assert (
            "max-age="
            in
            rotated_cookie.lower()
        )

        refresh_2 = (
            persistent_client.cookies.get(
                REFRESH_COOKIE
            )
        )

        assert refresh_2

        assert (
            refresh_2
            != refresh_1
        )

        payload_2 = decode_token(
            refresh_2,
            "refresh",
        )

        assert (
            payload_2[
                "remember_me"
            ]
            is True
        )

    finally:
        session_client.close()
        persistent_client.close()

        _delete_test_identity(
            organization_id
        )


def test_multi_device_refresh_rotation_and_logout():
    (
        organization_id,
        user_id,
        email,
    ) = _create_verified_user()

    client_a = TestClient(
        app
    )

    client_b = TestClient(
        app
    )

    try:
        # ----------------------------------------------------
        # Device A login
        # ----------------------------------------------------

        refresh_a1 = _login(
            client_a,
            email=email,
        )

        sessions = _active_sessions(
            user_id
        )

        assert len(
            sessions
        ) == 1

        # ----------------------------------------------------
        # Device B login
        # ----------------------------------------------------

        refresh_b1 = _login(
            client_b,
            email=email,
        )

        assert (
            refresh_a1
            != refresh_b1
        )

        sessions = _active_sessions(
            user_id
        )

        assert len(
            sessions
        ) == 2

        # ----------------------------------------------------
        # Device A refresh rotates only A
        # ----------------------------------------------------

        response = _refresh(
            client_a
        )

        assert (
            response.status_code
            == 200
        ), response.text

        refresh_a2 = (
            client_a.cookies.get(
                REFRESH_COOKIE
            )
        )

        assert refresh_a2

        assert (
            refresh_a2
            != refresh_a1
        )

        sessions = _active_sessions(
            user_id
        )

        assert len(
            sessions
        ) == 2

        # ----------------------------------------------------
        # Replay old refresh A1 must fail
        # ----------------------------------------------------

        replay_client = TestClient(
            app
        )

        replay_csrf = _csrf(
            replay_client
        )

        replay_client.cookies.set(
            REFRESH_COOKIE,
            refresh_a1,
            path="/api/auth",
        )

        replay_response = (
            replay_client.post(
                "/api/auth/refresh",
                headers={
                    "X-CSRF-Token":
                        replay_csrf,
                },
            )
        )

        assert (
            replay_response.status_code
            == 401
        )

        # ----------------------------------------------------
        # Device B must still be valid after A replay
        # ----------------------------------------------------

        response = _refresh(
            client_b
        )

        assert (
            response.status_code
            == 200
        ), response.text

        refresh_b2 = (
            client_b.cookies.get(
                REFRESH_COOKIE
            )
        )

        assert refresh_b2

        assert (
            refresh_b2
            != refresh_b1
        )

        # ----------------------------------------------------
        # Logout B revokes only B
        # ----------------------------------------------------

        response = _logout(
            client_b
        )

        assert (
            response.status_code
            == 200
        ), response.text

        sessions = _active_sessions(
            user_id
        )

        assert len(
            sessions
        ) == 1

        # ----------------------------------------------------
        # Device A2 remains usable
        # ----------------------------------------------------

        response = _refresh(
            client_a
        )

        assert (
            response.status_code
            == 200
        ), response.text

        sessions = _active_sessions(
            user_id
        )

        assert len(
            sessions
        ) == 1

    finally:
        client_a.close()
        client_b.close()

        _delete_test_identity(
            organization_id
        )


def test_password_reset_style_revoke_all_sessions():
    (
        organization_id,
        user_id,
        email,
    ) = _create_verified_user()

    client_a = TestClient(
        app
    )

    client_b = TestClient(
        app
    )

    try:
        _login(
            client_a,
            email=email,
        )

        _login(
            client_b,
            email=email,
        )

        sessions = _active_sessions(
            user_id
        )

        assert len(
            sessions
        ) == 2

        from src.services.auth_session_service import (
            revoke_all_user_sessions,
        )

        db = SessionLocal()

        try:
            count = (
                revoke_all_user_sessions(
                    db,
                    user_id,
                )
            )

            db.commit()

            assert count == 2

        finally:
            db.close()

        sessions = _active_sessions(
            user_id
        )

        assert sessions == []

        # Both device refresh cookies must now be rejected.
        response_a = _refresh(
            client_a
        )

        response_b = _refresh(
            client_b
        )

        assert (
            response_a.status_code
            == 401
        )

        assert (
            response_b.status_code
            == 401
        )

    finally:
        client_a.close()
        client_b.close()

        _delete_test_identity(
            organization_id
        )

def test_session_management_api():
    (
        organization_id,
        user_id,
        email,
    ) = _create_verified_user()

    client_a = TestClient(
        app
    )

    client_b = TestClient(
        app
    )

    try:
        # ----------------------------------------------------
        # Create two independent browser sessions
        # ----------------------------------------------------

        _login(
            client_a,
            email=email,
        )

        _login(
            client_b,
            email=email,
        )

        access_b = client_b.cookies.get(
            ACCESS_COOKIE
        )

        assert access_b

        # ----------------------------------------------------
        # GET /sessions
        # ----------------------------------------------------

        response = client_a.get(
            "/api/auth/sessions"
        )

        assert (
            response.status_code
            == 200
        ), response.text

        payload = response.json()

        assert payload["count"] == 2
        assert len(
            payload["sessions"]
        ) == 2

        # Never expose refresh credentials.
        for session in payload[
            "sessions"
        ]:
            assert (
                "refresh_token_hash"
                not in session
            )

        current_sessions = [
            session
            for session
            in payload["sessions"]
            if session["is_current"]
        ]

        assert len(
            current_sessions
        ) == 1

        current_id = (
            current_sessions[0]["id"]
        )

        other_sessions = [
            session
            for session
            in payload["sessions"]
            if not session["is_current"]
        ]

        assert len(
            other_sessions
        ) == 1

        other_id = (
            other_sessions[0]["id"]
        )

        # ----------------------------------------------------
        # DELETE another session
        # ----------------------------------------------------

        csrf_token = (
            client_a.cookies.get(
                CSRF_COOKIE
            )
        )

        assert csrf_token

        response = client_a.delete(
            (
                "/api/auth/sessions/"
                f"{other_id}"
            ),
            headers={
                "X-CSRF-Token":
                    csrf_token,
            },
        )

        assert (
            response.status_code
            == 200
        ), response.text

        payload = response.json()

        assert (
            payload[
                "current_session_revoked"
            ]
            is False
        )

        assert (
            payload["session_id"]
            == other_id
        )

        events = _auth_events(
            user_id
        )

        revoked_events = [
            event
            for event in events
            if (
                event["event_type"]
                == "session_revoked"
            )
        ]

        assert (
            len(revoked_events)
            == 1
        )

        revoked_event = (
            revoked_events[0]
        )

        assert (
            revoked_event[
                "organization_id"
            ]
            == organization_id
        )

        assert (
            revoked_event["user_id"]
            == user_id
        )

        assert (
            revoked_event[
                "metadata_json"
            ]
            == {
                "session_id":
                    other_id,

                "current_session":
                    False,
            }
        )

        # Device B's access and refresh credentials are revoked.
        _assert_access_token_rejected(
            access_b
        )

        # Device B's refresh token has now been revoked.
        response = _refresh(
            client_b
        )

        assert (
            response.status_code
            == 401
        )

        # Device A remains valid.
        response = _refresh(
            client_a
        )

        assert (
            response.status_code
            == 200
        ), response.text

        # ----------------------------------------------------
        # Current session is still the only active one
        # ----------------------------------------------------

        response = client_a.get(
            "/api/auth/sessions"
        )

        assert (
            response.status_code
            == 200
        ), response.text

        payload = response.json()

        assert payload["count"] == 1

        assert (
            payload["sessions"][0][
                "is_current"
            ]
            is True
        )

        access_a = client_a.cookies.get(
            ACCESS_COOKIE
        )

        assert csrf_token
        assert access_a

        # ----------------------------------------------------
        # POST /sessions/revoke-all
        # ----------------------------------------------------

        csrf_token = (
            client_a.cookies.get(
                CSRF_COOKIE
            )
        )

        assert csrf_token

        response = client_a.post(
            (
                "/api/auth/sessions/"
                "revoke-all"
            ),
            headers={
                "X-CSRF-Token":
                    csrf_token,
            },
        )

        assert (
            response.status_code
            == 200
        ), response.text

        payload = response.json()

        assert (
            payload["revoked_count"]
            == 1
        )

        # Current refresh cookie should be cleared.
        assert (
            client_a.cookies.get(
                REFRESH_COOKIE
            )
            is None
        )

        # DB has no active sessions.
        assert (
            _active_sessions(
                user_id
            )
            == []
        )

        _assert_access_token_rejected(
            access_a
        )

    finally:
        client_a.close()
        client_b.close()

        _delete_test_identity(
            organization_id
        )

def test_cannot_revoke_another_users_session():
    (
        organization_a,
        _user_a,
        email_a,
    ) = _create_verified_user()

    (
        organization_b,
        _user_b,
        email_b,
    ) = _create_verified_user()

    client_a = TestClient(
        app
    )

    client_b = TestClient(
        app
    )

    try:
        _login(
            client_a,
            email=email_a,
        )

        _login(
            client_b,
            email=email_b,
        )

        response = client_b.get(
            "/api/auth/sessions"
        )

        assert (
            response.status_code
            == 200
        ), response.text

        victim_session_id = (
            response.json()[
                "sessions"
            ][0]["id"]
        )

        csrf_token = (
            client_a.cookies.get(
                CSRF_COOKIE
            )
        )

        assert csrf_token

        response = client_a.delete(
            (
                "/api/auth/sessions/"
                f"{victim_session_id}"
            ),
            headers={
                "X-CSRF-Token":
                    csrf_token,
            },
        )

        # Do not disclose whether another user's
        # session ID actually exists.
        assert (
            response.status_code
            == 404
        )

        # Victim's session must remain valid.
        response = _refresh(
            client_b
        )

        assert (
            response.status_code
            == 200
        ), response.text

    finally:
        client_a.close()
        client_b.close()

        _delete_test_identity(
            organization_a
        )

        _delete_test_identity(
            organization_b
        )

def test_change_password_revokes_all_sessions():
    (
        organization_id,
        user_id,
        email,
    ) = _create_verified_user()

    client_a = TestClient(app)
    client_b = TestClient(app)

    new_password = (
        "ChangedAuthPassword!2026"
    )

    try:
        # ----------------------------------------------------
        # Login on two devices
        # ----------------------------------------------------

        _login(
            client_a,
            email=email,
        )

        _login(
            client_b,
            email=email,
        )

        access_a = client_a.cookies.get(
            ACCESS_COOKIE
        )

        access_b = client_b.cookies.get(
            ACCESS_COOKIE
        )

        assert access_a
        assert access_b

        assert len(
            _active_sessions(user_id)
        ) == 2

        # ----------------------------------------------------
        # Wrong current password
        # ----------------------------------------------------

        csrf_token = (
            client_a.cookies.get(
                CSRF_COOKIE
            )
        )

        assert csrf_token

        response = client_a.put(
            "/api/auth/change-password",
            headers={
                "X-CSRF-Token":
                    csrf_token,
            },
            json={
                "current_password":
                    "WrongCurrentPassword!2026",

                "new_password":
                    new_password,

                "confirm_password":
                    new_password,
            },
        )

        assert (
            response.status_code
            == 401
        ), response.text

        payload = response.json()

        assert (
            payload["detail"]["error"]["code"]
            == "AUTH_013"
        )

        # Sessions must remain untouched after failure.
        assert len(
            _active_sessions(user_id)
        ) == 2

        # ----------------------------------------------------
        # Same password must be rejected
        # ----------------------------------------------------

        response = client_a.put(
            "/api/auth/change-password",
            headers={
                "X-CSRF-Token":
                    csrf_token,
            },
            json={
                "current_password":
                    TEST_PASSWORD,

                "new_password":
                    TEST_PASSWORD,

                "confirm_password":
                    TEST_PASSWORD,
            },
        )

        assert (
            response.status_code
            == 422
        ), response.text

        payload = response.json()

        assert (
            payload["detail"]["error"]["code"]
            == "AUTH_014"
        )

        assert len(
            _active_sessions(user_id)
        ) == 2

        # ----------------------------------------------------
        # Confirmation mismatch
        # ----------------------------------------------------

        response = client_a.put(
            "/api/auth/change-password",
            headers={
                "X-CSRF-Token":
                    csrf_token,
            },
            json={
                "current_password":
                    TEST_PASSWORD,

                "new_password":
                    new_password,

                "confirm_password":
                    "DifferentPassword!2026",
            },
        )

        assert (
            response.status_code
            == 422
        ), response.text

        # ----------------------------------------------------
        # Successful password change
        # ----------------------------------------------------

        response = client_a.put(
            "/api/auth/change-password",
            headers={
                "X-CSRF-Token":
                    csrf_token,
            },
            json={
                "current_password":
                    TEST_PASSWORD,

                "new_password":
                    new_password,

                "confirm_password":
                    new_password,
            },
        )

        assert (
            response.status_code
            == 200
        ), response.text

        payload = response.json()

        assert (
            payload["reauthenticate"]
            is True
        )

        _assert_access_token_rejected(
            access_a
        )

        _assert_access_token_rejected(
            access_b
        )

        events = _auth_events(
            user_id
        )

        password_events = [
            event
            for event in events
            if (
                event["event_type"]
                == "password_changed"
            )
        ]

        assert (
            len(password_events)
            == 1
        )

        password_event = (
            password_events[0]
        )

        assert (
            password_event[
                "organization_id"
            ]
            == organization_id
        )

        assert (
            password_event["user_id"]
            == user_id
        )

        assert (
            password_event[
                "metadata_json"
            ]
            == {
                "sessions_revoked":
                    2,
            }
        )

        metadata_keys = {
            str(key).lower()
            for key in (
                password_event[
                    "metadata_json"
                ]
                or {}
            )
        }

        assert (
            "password"
            not in metadata_keys
        )

        assert (
            "current_password"
            not in metadata_keys
        )

        assert (
            "new_password"
            not in metadata_keys
        )

        # All persisted refresh sessions are revoked.
        assert (
            _active_sessions(user_id)
            == []
        )

        # Current browser refresh cookie was cleared.
        assert (
            client_a.cookies.get(
                REFRESH_COOKIE
            )
            is None
        )

        # ----------------------------------------------------
        # Device B's old refresh token is now invalid
        # ----------------------------------------------------

        response = _refresh(
            client_b
        )

        assert (
            response.status_code
            == 401
        )

        # ----------------------------------------------------
        # Old password can no longer authenticate
        # ----------------------------------------------------

        old_client = TestClient(app)

        try:
            old_csrf = _csrf(
                old_client
            )

            response = old_client.post(
                "/api/auth/login",
                headers={
                    "X-CSRF-Token":
                        old_csrf,
                },
                json={
                    "email":
                        email,

                    "password":
                        TEST_PASSWORD,
                },
            )

            assert (
                response.status_code
                == 401
            )

            # ------------------------------------------------
            # New password authenticates successfully
            # ------------------------------------------------

            response = old_client.post(
                "/api/auth/login",
                headers={
                    "X-CSRF-Token":
                        old_csrf,
                },
                json={
                    "email":
                        email,

                    "password":
                        new_password,
                },
            )

            assert (
                response.status_code
                == 200
            ), response.text

        finally:
            old_client.close()

    finally:
        client_a.close()
        client_b.close()

        _delete_test_identity(
            organization_id
        )

def test_failed_login_locks_account_after_five_attempts():
    (
        organization_id,
        user_id,
        email,
    ) = _create_verified_user()

    client = TestClient(app)

    try:
        csrf_token = _csrf(
            client
        )

        for attempt in range(1, 5):
            response = client.post(
                "/api/auth/login",
                headers={
                    "X-CSRF-Token":
                        csrf_token,
                },
                json={
                    "email":
                        email,

                    "password":
                        "WrongPassword!2026",
                },
            )

            assert (
                response.status_code
                == 401
            ), (
                f"attempt={attempt} "
                f"response={response.text}"
            )

            assert (
                response.json()[
                    "detail"
                ]["error"]["code"]
                == "AUTH_001"
            )

        response = client.post(
            "/api/auth/login",
            headers={
                "X-CSRF-Token":
                    csrf_token,
            },
            json={
                "email":
                    email,

                "password":
                    "WrongPassword!2026",
            },
        )

        assert (
            response.status_code
            == 423
        ), response.text

        assert (
            response.json()[
                "detail"
            ]["error"]["code"]
            == "AUTH_003"
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
            assert user.locked_until is not None
            assert (
                user.locked_until
                > now_utc()
            )
            assert (
                user.login_attempts
                == 0
            )

        finally:
            db.close()

        response = client.post(
            "/api/auth/login",
            headers={
                "X-CSRF-Token":
                    csrf_token,
            },
            json={
                "email":
                    email,

                "password":
                    TEST_PASSWORD,
            },
        )

        assert (
            response.status_code
            == 423
        ), response.text

        assert (
            response.json()[
                "detail"
            ]["error"]["code"]
            == "AUTH_003"
        )

    finally:
        client.close()

        _delete_test_identity(
            organization_id
        )

def test_failed_login_and_lockout_are_audited():
    (
        organization_id,
        user_id,
        email,
    ) = _create_verified_user()

    client = TestClient(app)

    try:
        csrf_token = _csrf(
            client
        )

        for attempt in range(1, 5):
            response = client.post(
                "/api/auth/login",
                headers={
                    "X-CSRF-Token":
                        csrf_token,
                },
                json={
                    "email":
                        email,

                    "password":
                        "WrongPassword!2026",
                },
            )

            assert (
                response.status_code
                == 401
            ), response.text

        response = client.post(
            "/api/auth/login",
            headers={
                "X-CSRF-Token":
                    csrf_token,
            },
            json={
                "email":
                    email,

                "password":
                    "WrongPassword!2026",
            },
        )

        assert (
            response.status_code
            == 423
        ), response.text

        events = _auth_events(
            user_id
        )

        failed_events = [
            event
            for event in events
            if (
                event["event_type"]
                == "login_failed"
            )
        ]

        locked_events = [
            event
            for event in events
            if (
                event["event_type"]
                == "account_locked"
            )
        ]

        assert (
            len(failed_events)
            == 5
        )

        assert (
            len(locked_events)
            == 1
        )

        for event in failed_events:
            assert (
                event["organization_id"]
                == organization_id
            )

            assert (
                event["user_id"]
                == user_id
            )

            metadata = (
                event[
                    "metadata_json"
                ]
                or {}
            )

            assert (
                metadata["reason"]
                == "invalid_credentials"
            )

            # Credentials must never enter
            # security audit metadata.
            lowered = {
                str(key).lower()
                for key in metadata
            }

            assert (
                "password"
                not in lowered
            )

            assert (
                "token"
                not in lowered
            )

            assert (
                "refresh_token"
                not in lowered
            )

            assert (
                "verification_code"
                not in lowered
            )

        locked = locked_events[0]

        assert (
            locked["organization_id"]
            == organization_id
        )

        assert (
            locked["metadata_json"][
                "reason"
            ]
            == "failed_login_threshold"
        )

        assert (
            locked["metadata_json"][
                "lock_minutes"
            ]
            == 15
        )

    finally:
        client.close()

        _delete_test_identity(
            organization_id
        )

def test_successful_password_login_is_audited():
    (
        organization_id,
        user_id,
        email,
    ) = _create_verified_user()

    client = TestClient(app)

    try:
        csrf_token = _csrf(
            client
        )

        response = client.post(
            "/api/auth/login",
            headers={
                "X-CSRF-Token":
                    csrf_token,
            },
            json={
                "email":
                    email,

                "password":
                    TEST_PASSWORD,
            },
        )

        assert (
            response.status_code
            == 200
        ), response.text

        events = _auth_events(
            user_id
        )

        success_events = [
            event
            for event in events
            if (
                event["event_type"]
                == "login_success"
            )
        ]

        assert (
            len(success_events)
            == 1
        )

        event = success_events[0]

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
                    "password",
            }
        )

        assert (
            event["ip_address"]
            is not None
        )

        assert (
            event["user_agent"]
            is not None
        )

    finally:
        client.close()

        _delete_test_identity(
            organization_id
        )