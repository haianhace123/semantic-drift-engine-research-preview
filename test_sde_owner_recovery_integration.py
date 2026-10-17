"""PostgreSQL acceptance for an operator-only legacy-owner recovery.

Run ONLY in the disposable sde_integration database; this file refuses the
normal SDE database and does not use the pilot organization or credentials.
"""
from __future__ import annotations

import importlib.util
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DataError
from src.core.database import SessionLocal, engine
from src.models.organization import Organization
from src.models.user import User
from src.models.user_session import UserSession
from src.models.auth_security_event import AuthSecurityEvent


SCRIPT = Path('/app/sde_owner_recovery_operator.py')


def _require_disposable_database():
    with engine.connect() as conn:
        database = conn.execute(text('SELECT current_database()')).scalar_one()
    assert database == 'sde_integration', f'Refusing unsafe test database: {database}'
    assert engine.url.host == 'db', 'Refusing non-isolated database host'


def _load_operator():
    spec = importlib.util.spec_from_file_location('sde_owner_recovery_operator_for_test', SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def isolated_owner():
    _require_disposable_database()
    suffix = uuid.uuid4().hex[:16]
    org_id = uuid.uuid4()
    user_id = uuid.uuid4()
    email = f'owner-recovery-{suffix}@example.invalid'
    slug = f'owner-recovery-{suffix}'
    with SessionLocal() as db:
        org = Organization(
            id=org_id,
            name=f"{slug}'s Workspace",
            slug=slug,
            billing_email=email,
            is_active=True,
            subscription_tier='free',
        )
        db.add(org)
        db.flush()
        user = User(
            id=user_id,
            organization_id=org_id,
            email=email,
            full_name='Disposable Owner',
            password_hash=None,
            google_id='disposable-provider-' + suffix,
            github_id=None,
            role='user',
            is_active=True,
            email_verified=True,
            last_login=datetime.now(timezone.utc),
        )
        db.add(user)
        db.flush()
        now = datetime.now(timezone.utc)
        db.add(UserSession(
            id=uuid.uuid4(),
            user_id=user_id,
            refresh_token_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            created_at=now,
            last_used_at=now,
            expires_at=now + timedelta(days=1),
            is_revoked=False,
        ))
        db.commit()

    yield org_id, user_id

    with SessionLocal() as db:
        # Explicit removal avoids relying on orphan cascades for the audit row.
        db.query(AuthSecurityEvent).filter(
            AuthSecurityEvent.organization_id == org_id
        ).delete(synchronize_session=False)
        db.query(User).filter(User.organization_id == org_id).delete(synchronize_session=False)
        db.query(Organization).filter(Organization.id == org_id).delete(synchronize_session=False)
        db.commit()


def _invoke(monkeypatch, module, org_id, user_id, execute=False):
    args = [
        str(SCRIPT), '--organization-id', str(org_id),
        '--expected-user-id', str(user_id),
    ]
    if execute:
        args += [
            '--execute',
            '--operator-attests-ownership',
            '--approval-reference', 'TEST-ISOLATED-ONLY',
        ]
    monkeypatch.setattr(sys, 'argv', args)
    return module.main()


def test_success_commits_one_audited_role_change(isolated_owner, monkeypatch, capsys):
    org_id, user_id = isolated_owner
    module = _load_operator()
    assert _invoke(monkeypatch, module, org_id, user_id) == 0
    with SessionLocal() as db:
        assert db.query(User).filter(User.id == user_id).one().role == 'user'
        assert db.query(AuthSecurityEvent).filter(
            AuthSecurityEvent.organization_id == org_id
        ).count() == 0

    assert _invoke(monkeypatch, module, org_id, user_id, execute=True) == 0
    with SessionLocal() as db:
        assert db.query(User).filter(User.id == user_id).one().role == 'admin'
        events = db.query(AuthSecurityEvent).filter(
            AuthSecurityEvent.organization_id == org_id,
            AuthSecurityEvent.user_id == user_id,
            AuthSecurityEvent.event_type == module.AUDIT_TYPE,
        ).all()
        assert len(events) == 1
        assert events[0].metadata_json['approval_reference'] == 'TEST-ISOLATED-ONLY'
        assert events[0].metadata_json['previous_role'] == 'user'
        assert events[0].metadata_json['new_role'] == 'admin'

    # Non-repeatability: an already-recovered owner must not be recovered again.
    with pytest.raises(module.RecoveryRefused, match='user_not_eligible_for_owner_recovery'):
        _invoke(monkeypatch, module, org_id, user_id, execute=True)
    with SessionLocal() as db:
        assert db.query(AuthSecurityEvent).filter(
            AuthSecurityEvent.organization_id == org_id,
            AuthSecurityEvent.event_type == module.AUDIT_TYPE,
        ).count() == 1


def test_database_audit_insert_failure_rolls_back_role_change(isolated_owner, monkeypatch):
    org_id, user_id = isolated_owner
    module = _load_operator()

    # PostgreSQL rejects a VARCHAR(64) audit event type longer than 64 chars.
    # This exercises the actual flush failure, after user.role is changed in
    # the ORM, and ensures the whole transaction is rolled back.
    monkeypatch.setattr(module, 'AUDIT_TYPE', 'X' * 80)
    with pytest.raises(DataError):
        _invoke(monkeypatch, module, org_id, user_id, execute=True)

    with SessionLocal() as db:
        assert db.query(User).filter(User.id == user_id).one().role == 'user'
        assert db.query(AuthSecurityEvent).filter(
            AuthSecurityEvent.organization_id == org_id,
        ).count() == 0


@pytest.mark.parametrize('invalid_evidence', [
    'session_missing',
    'session_revoked',
    'session_expired',
    'session_stale',
    'login_stale',
])
def test_recovery_rejects_stale_or_missing_session_evidence(
    isolated_owner, monkeypatch, invalid_evidence,
):
    org_id, user_id = isolated_owner
    module = _load_operator()
    with SessionLocal() as db:
        user = db.query(User).filter(User.id == user_id).one()
        session = db.query(UserSession).filter(UserSession.user_id == user_id).one()
        if invalid_evidence == 'session_missing':
            db.delete(session)
        elif invalid_evidence == 'session_revoked':
            session.is_revoked = True
            session.revoked_at = datetime.now(timezone.utc)
        elif invalid_evidence == 'session_expired':
            session.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        elif invalid_evidence == 'session_stale':
            session.created_at = datetime.now(timezone.utc) - timedelta(minutes=16)
        else:
            user.last_login = datetime.now(timezone.utc) - timedelta(minutes=16)
        db.commit()

    with pytest.raises(module.RecoveryRefused, match='recent_'):
        _invoke(monkeypatch, module, org_id, user_id, execute=True)

    with SessionLocal() as db:
        assert db.query(User).filter(User.id == user_id).one().role == 'user'
        assert db.query(AuthSecurityEvent).filter(
            AuthSecurityEvent.organization_id == org_id
        ).count() == 0
