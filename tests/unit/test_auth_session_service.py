from __future__ import annotations

import uuid
from datetime import timedelta

from src.core.security import now_utc, token_hash
from src.models import UserSession
from src.services.auth_session_service import (
    create_session,
    get_session_by_refresh_token,
    get_user_session,
    list_active_user_sessions,
    revoke_all_user_sessions,
    revoke_other_user_sessions,
    revoke_session,
    rotate_session,
    session_is_expired,
)


class FakeQuery:
    def __init__(self, result=None, results=None):
        self.result = result
        self.results = results or []

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def first(self):
        return self.result

    def all(self):
        return self.results


class FakeDB:
    def __init__(self):
        self.added = []
        self.flush_count = 0
        self.query_result = None
        self.query_results = []

    def add(self, value):
        self.added.append(value)

    def flush(self):
        self.flush_count += 1

    def query(self, model):
        assert model is UserSession

        return FakeQuery(
            result=self.query_result,
            results=self.query_results,
        )


def test_create_session_hashes_refresh_token():
    db = FakeDB()

    user_id = uuid.uuid4()
    expires_at = now_utc() + timedelta(days=30)

    session = create_session(
        db,
        user_id=user_id,
        refresh_token="refresh-secret",
        expires_at=expires_at,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )

    assert session.user_id == user_id
    assert (
        session.refresh_token_hash
        == token_hash("refresh-secret")
    )
    assert session.expires_at == expires_at
    assert session.ip_address == "127.0.0.1"
    assert session.user_agent == "pytest"
    assert session.is_revoked is False

    assert db.added == [session]
    assert db.flush_count == 1


def test_get_session_by_refresh_token():
    db = FakeDB()

    expected = UserSession(
        user_id=uuid.uuid4(),
        refresh_token_hash=token_hash("refresh-secret"),
        expires_at=now_utc() + timedelta(days=30),
    )

    db.query_result = expected

    result = get_session_by_refresh_token(
        db,
        "refresh-secret",
    )

    assert result is expected


def test_rotate_session_changes_hash():
    db = FakeDB()

    session = UserSession(
        user_id=uuid.uuid4(),
        refresh_token_hash=token_hash("old-token"),
        expires_at=now_utc() + timedelta(days=30),
    )

    new_expiry = now_utc() + timedelta(days=60)

    rotate_session(
        db,
        session=session,
        new_refresh_token="new-token",
        new_expires_at=new_expiry,
        ip_address="10.0.0.1",
        user_agent="new-agent",
    )

    assert (
        session.refresh_token_hash
        == token_hash("new-token")
    )
    assert session.expires_at == new_expiry
    assert session.ip_address == "10.0.0.1"
    assert session.user_agent == "new-agent"
    assert db.flush_count == 1


def test_revoke_session():
    db = FakeDB()

    session = UserSession(
        user_id=uuid.uuid4(),
        refresh_token_hash=token_hash("token"),
        expires_at=now_utc() + timedelta(days=30),
        is_revoked=False,
    )

    revoke_session(
        db,
        session,
    )

    assert session.is_revoked is True
    assert session.revoked_at is not None
    assert db.flush_count == 1


def test_revoke_all_user_sessions():
    db = FakeDB()

    sessions = [
        UserSession(
            user_id=uuid.uuid4(),
            refresh_token_hash=token_hash("token-1"),
            expires_at=now_utc() + timedelta(days=30),
            is_revoked=False,
        ),
        UserSession(
            user_id=uuid.uuid4(),
            refresh_token_hash=token_hash("token-2"),
            expires_at=now_utc() + timedelta(days=30),
            is_revoked=False,
        ),
    ]

    db.query_results = sessions

    count = revoke_all_user_sessions(
        db,
        uuid.uuid4(),
    )

    assert count == 2

    assert all(
        session.is_revoked
        for session in sessions
    )

    assert all(
        session.revoked_at is not None
        for session in sessions
    )

    assert db.flush_count == 1


def test_session_is_expired():
    expired = UserSession(
        user_id=uuid.uuid4(),
        refresh_token_hash=token_hash("expired"),
        expires_at=now_utc() - timedelta(seconds=1),
    )

    active = UserSession(
        user_id=uuid.uuid4(),
        refresh_token_hash=token_hash("active"),
        expires_at=now_utc() + timedelta(days=1),
    )

    assert session_is_expired(expired) is True
    assert session_is_expired(active) is False

def test_list_active_user_sessions():
    db = FakeDB()

    user_id = uuid.uuid4()

    first = UserSession(
        user_id=user_id,
        refresh_token_hash=token_hash(
            "active-1"
        ),
        expires_at=(
            now_utc()
            + timedelta(days=1)
        ),
        is_revoked=False,
    )

    second = UserSession(
        user_id=user_id,
        refresh_token_hash=token_hash(
            "active-2"
        ),
        expires_at=(
            now_utc()
            + timedelta(days=2)
        ),
        is_revoked=False,
    )

    db.query_results = [
        first,
        second,
    ]

    sessions = (
        list_active_user_sessions(
            db,
            user_id,
        )
    )

    assert sessions == [
        first,
        second,
    ]


def test_get_user_session():
    db = FakeDB()

    user_id = uuid.uuid4()

    expected = UserSession(
        id=uuid.uuid4(),
        user_id=user_id,
        refresh_token_hash=token_hash(
            "user-session"
        ),
        expires_at=(
            now_utc()
            + timedelta(days=1)
        ),
        is_revoked=False,
    )

    db.query_result = expected

    result = get_user_session(
        db,
        user_id=user_id,
        session_id=expected.id,
    )

    assert result is expected


def test_get_user_session_returns_none():
    db = FakeDB()

    result = get_user_session(
        db,
        user_id=uuid.uuid4(),
        session_id=uuid.uuid4(),
    )

    assert result is None


def test_revoke_other_user_sessions():
    db = FakeDB()

    user_id = uuid.uuid4()
    keep_session_id = uuid.uuid4()

    session_a = UserSession(
        id=uuid.uuid4(),
        user_id=user_id,
        refresh_token_hash=token_hash(
            "other-a"
        ),
        expires_at=(
            now_utc()
            + timedelta(days=1)
        ),
        is_revoked=False,
    )

    session_b = UserSession(
        id=uuid.uuid4(),
        user_id=user_id,
        refresh_token_hash=token_hash(
            "other-b"
        ),
        expires_at=(
            now_utc()
            + timedelta(days=1)
        ),
        is_revoked=False,
    )

    db.query_results = [
        session_a,
        session_b,
    ]

    count = revoke_other_user_sessions(
        db,
        user_id=user_id,
        keep_session_id=keep_session_id,
    )

    assert count == 2

    assert session_a.is_revoked is True
    assert session_b.is_revoked is True

    assert session_a.revoked_at is not None
    assert session_b.revoked_at is not None

    assert db.flush_count == 1


def test_revoke_other_user_sessions_empty():
    db = FakeDB()

    count = revoke_other_user_sessions(
        db,
        user_id=uuid.uuid4(),
        keep_session_id=uuid.uuid4(),
    )

    assert count == 0
    assert db.flush_count == 0