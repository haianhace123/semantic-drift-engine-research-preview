"""Behavioral regression for organization suspension at credential issuance.

Runs on an isolated SQLite database; no Docker or production DB required.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException, Request, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.api import auth_routes as auth
from src.core.database import Base
from src.core.security import token_hash
from src.models import Organization, User, UserSession


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine,
        tables=[Organization.__table__, User.__table__, UserSession.__table__],
    )
    try:
        with Session(engine) as session:
            yield session
    finally:
        engine.dispose()


def identity(db: Session, *, org_active=True, user_active=True):
    organization = Organization(
        id=uuid.uuid4(),
        name="Issuance boundary test",
        slug="issuance-" + uuid.uuid4().hex,
        billing_email="issuance@example.test",
        is_active=org_active,
    )
    user = User(
        id=uuid.uuid4(),
        organization_id=organization.id,
        email="test-" + uuid.uuid4().hex + "@example.test",
        full_name="Session issuance test",
        role="admin",
        email_verified=True,
        is_active=user_active,
    )
    db.add_all([organization, user])
    db.commit()
    return organization, user


def request():
    return Request({"type": "http", "headers": [], "client": ("127.0.0.1", 10001)})


def seed_session(db: Session, user: User):
    record = UserSession(
        id=uuid.uuid4(),
        user_id=user.id,
        refresh_token_hash=token_hash("original-refresh"),
        expires_at=datetime.now(timezone.utc) + timedelta(days=1),
        is_revoked=False,
    )
    db.add(record)
    db.commit()
    return record


def test_suspended_org_cannot_create_session_or_set_cookies(db):
    _org, user = identity(db, org_active=False)
    response = Response()
    with pytest.raises(HTTPException) as exc:
        auth.set_auth_cookies(response, user, request(), db)
    assert exc.value.status_code == 401
    assert not any(header == b"set-cookie" for header, _ in response.raw_headers)
    assert db.query(UserSession).count() == 0


def test_active_org_can_create_session_and_set_cookies(db):
    _org, user = identity(db)
    response = Response()
    auth.set_auth_cookies(response, user, request(), db)
    assert db.query(UserSession).count() == 1
    assert any(header == b"set-cookie" for header, _ in response.raw_headers)


def test_suspended_org_cannot_rotate_refresh_or_set_cookies(db):
    org, user = identity(db)
    record = seed_session(db, user)
    original = record.refresh_token_hash
    org.is_active = False
    db.commit()
    response = Response()
    with pytest.raises(HTTPException) as exc:
        auth.rotate_auth_cookies(response, user, request(), db, record)
    assert exc.value.status_code == 401
    assert record.refresh_token_hash == original
    assert not any(header == b"set-cookie" for header, _ in response.raw_headers)


def test_active_org_can_rotate_refresh(db):
    _org, user = identity(db)
    record = seed_session(db, user)
    original = record.refresh_token_hash
    response = Response()
    auth.rotate_auth_cookies(response, user, request(), db, record)
    assert record.refresh_token_hash != original
    assert any(header == b"set-cookie" for header, _ in response.raw_headers)


@pytest.mark.parametrize("kind", ["disabled", "deleted"])
def test_disabled_or_deleted_user_cannot_issue_credentials(db, kind):
    _org, user = identity(db)
    if kind == "disabled":
        user.is_active = False
    else:
        user.deleted_at = datetime.now(timezone.utc)
    db.commit()
    response = Response()
    with pytest.raises(HTTPException) as exc:
        auth.set_auth_cookies(response, user, request(), db)
    assert exc.value.status_code == 401
    assert not any(header == b"set-cookie" for header, _ in response.raw_headers)
    assert db.query(UserSession).count() == 0


def test_suspending_one_tenant_does_not_prevent_other_tenant_sessions(db):
    _disabled, invalid_user = identity(db, org_active=False)
    _active, valid_user = identity(db, org_active=True)
    with pytest.raises(HTTPException):
        auth.set_auth_cookies(Response(), invalid_user, request(), db)
    auth.set_auth_cookies(Response(), valid_user, request(), db)
    assert db.query(UserSession).count() == 1


def test_reactivation_allows_new_sessions_again(db):
    org, user = identity(db, org_active=False)
    with pytest.raises(HTTPException):
        auth.set_auth_cookies(Response(), user, request(), db)
    org.is_active = True
    db.commit()
    auth.set_auth_cookies(Response(), user, request(), db)
    assert db.query(UserSession).count() == 1
