"""Fail-closed operator recovery eligibility without a database connection."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from sde_owner_recovery_operator import RecoveryRefused, check_owner


def _legacy_identity():
    now = datetime.now(timezone.utc)
    user_id = uuid.uuid4()
    org_id = uuid.uuid4()
    email = "legacy-owner@example.invalid"
    org = SimpleNamespace(
        id=org_id,
        is_active=True,
        billing_email=email,
        name="legacy-owner's Workspace",
        slug="legacy-owner",
        created_at=now - timedelta(days=100),
    )
    user = SimpleNamespace(
        id=user_id,
        organization_id=org_id,
        email=email,
        role="user",
        is_active=True,
        deleted_at=None,
        email_verified=True,
        last_login=now - timedelta(minutes=1),
        google_id="google-provider-subject",
        github_id=None,
        password_hash=None,
        created_at=org.created_at + timedelta(seconds=1),
    )
    session = SimpleNamespace(
        user_id=user_id,
        is_revoked=False,
        revoked_at=None,
        created_at=now - timedelta(seconds=30),
        expires_at=now + timedelta(days=1),
    )
    return now, org, user, session


def test_fresh_oauth_identity_with_live_session_is_eligible():
    now, org, user, session = _legacy_identity()
    recovered, metadata = check_owner(org, [user], [], user.id, [session], now)
    assert recovered is user
    assert metadata["recent_active_session_observed"] is True


@pytest.mark.parametrize("case,reason", [
    ("missing_session", "recent_active_session_required"),
    ("revoked_session", "recent_active_session_required"),
    ("expired_session", "recent_active_session_required"),
    ("old_session", "recent_active_session_required"),
    ("future_session", "recent_active_session_required"),
    ("other_identity_session", "recent_active_session_required"),
    ("old_login", "recent_oauth_login_required"),
    ("future_login", "recent_oauth_login_required"),
    ("naive_login", "recent_oauth_login_required"),
])
def test_recovery_refuses_unsupported_identity_evidence(case, reason):
    now, org, user, session = _legacy_identity()
    sessions = [session]
    if case == "missing_session":
        sessions = []
    elif case == "revoked_session":
        session.is_revoked = True
    elif case == "expired_session":
        session.expires_at = now - timedelta(minutes=1)
    elif case == "old_session":
        session.created_at = now - timedelta(minutes=16)
    elif case == "future_session":
        session.created_at = now + timedelta(minutes=1)
    elif case == "other_identity_session":
        session.user_id = uuid.uuid4()
    elif case == "old_login":
        user.last_login = now - timedelta(minutes=16)
    elif case == "future_login":
        user.last_login = now + timedelta(minutes=1)
    elif case == "naive_login":
        user.last_login = now.replace(tzinfo=None)

    with pytest.raises(RecoveryRefused, match=reason):
        check_owner(org, [user], [], user.id, sessions, now)


def test_recovery_refuses_existing_integration_credentials():
    now, org, user, session = _legacy_identity()
    with pytest.raises(RecoveryRefused, match="unexpected_existing_integration_credentials"):
        check_owner(org, [user], [object()], user.id, [session], now)
