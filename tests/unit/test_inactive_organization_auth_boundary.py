"""Exercise production auth queries against an isolated in-memory SQL database.

This test checks organization suspension for user sessions and API keys without
requiring a running application, container or external PostgreSQL instance.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.core import security
from src.models import ApiKey, Organization, User, UserSession
from src.core.database import Base


@pytest.fixture
def auth_db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Organization.__table__,
            User.__table__,
            UserSession.__table__,
            ApiKey.__table__,
        ],
    )
    try:
        with Session(engine) as db:
            yield db
    finally:
        engine.dispose()


def _identity(db: Session, *, active: bool):
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    raw_key = "sk-" + uuid.uuid4().hex
    org = Organization(
        id=org_id,
        name="Security boundary organization",
        slug="auth-boundary-" + uuid.uuid4().hex,
        billing_email="auth-boundary@example.test",
        is_active=active,
    )
    user = User(
        id=user_id,
        organization_id=org_id,
        email="user-" + uuid.uuid4().hex + "@example.test",
        full_name="Boundary test",
        role="admin",
        is_active=True,
        email_verified=True,
    )
    session = UserSession(
        id=session_id,
        user_id=user_id,
        refresh_token_hash=security.token_hash(uuid.uuid4().hex),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        is_revoked=False,
    )
    key = ApiKey(
        organization_id=org_id,
        name="Boundary API key",
        key_hash=security.token_hash(raw_key),
        permissions={"read": True, "write": True},
        is_active=True,
    )
    db.add_all([org, user, session, key])
    db.commit()
    return org, user, session, key, raw_key


def _access(db: Session, monkeypatch, *, user: User, session: UserSession):
    monkeypatch.setattr(
        security,
        "decode_token",
        lambda _token, _type: {"sub": str(user.id), "sid": str(session.id)},
    )
    return security.authenticate_access_token("synthetic-token", db)


def test_active_organization_allows_existing_user_session(auth_db, monkeypatch):
    _org, user, session, _key, _raw_key = _identity(auth_db, active=True)
    assert _access(auth_db, monkeypatch, user=user, session=session).id == user.id


def test_inactive_organization_denies_existing_user_session(auth_db, monkeypatch):
    org, user, session, _key, _raw_key = _identity(auth_db, active=True)
    assert _access(auth_db, monkeypatch, user=user, session=session).id == user.id
    org.is_active = False
    auth_db.commit()
    with pytest.raises(security.AuthError) as error:
        _access(auth_db, monkeypatch, user=user, session=session)
    assert error.value.status_code == 401


def test_active_organization_allows_api_key(auth_db):
    _org, _user, _session, key, raw_key = _identity(auth_db, active=True)
    assert security.verify_api_key(raw_key, auth_db).id == key.id


def test_inactive_organization_denies_existing_api_key(auth_db):
    org, _user, _session, key, raw_key = _identity(auth_db, active=True)
    assert security.verify_api_key(raw_key, auth_db).id == key.id
    org.is_active = False
    auth_db.commit()
    assert security.verify_api_key(raw_key, auth_db) is None


def test_reactivation_restores_existing_credential_eligibility(auth_db, monkeypatch):
    org, user, session, key, raw_key = _identity(auth_db, active=False)
    assert security.verify_api_key(raw_key, auth_db) is None
    with pytest.raises(security.AuthError):
        _access(auth_db, monkeypatch, user=user, session=session)
    org.is_active = True
    auth_db.commit()
    assert _access(auth_db, monkeypatch, user=user, session=session).id == user.id
    assert security.verify_api_key(raw_key, auth_db).id == key.id


def test_suspending_one_organization_does_not_block_another(auth_db, monkeypatch):
    suspended, _user_a, _session_a, _key_a, raw_a = _identity(auth_db, active=True)
    _other, user_b, session_b, key_b, raw_b = _identity(auth_db, active=True)
    suspended.is_active = False
    auth_db.commit()
    assert security.verify_api_key(raw_a, auth_db) is None
    assert security.verify_api_key(raw_b, auth_db).id == key_b.id
    assert _access(auth_db, monkeypatch, user=user_b, session=session_b).id == user_b.id


def test_revoked_api_key_remains_denied_in_active_organization(auth_db):
    _org, _user, _session, key, raw_key = _identity(auth_db, active=True)
    key.is_active = False
    auth_db.commit()
    assert security.verify_api_key(raw_key, auth_db) is None


def test_access_token_subject_must_be_valid_uuid(auth_db, monkeypatch):
    monkeypatch.setattr(
        security,
        "decode_token",
        lambda _token, _type: {"sub": "not-a-uuid", "sid": str(uuid.uuid4())},
    )
    with pytest.raises(security.AuthError) as error:
        security.authenticate_access_token("synthetic-token", auth_db)
    assert error.value.status_code == 401
