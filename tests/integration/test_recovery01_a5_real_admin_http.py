"""Real HTTP admin-session certificate issuance backed by two disposable PostgreSQLs.

No FastAPI dependency overrides or issuer mocks. SDE API and the test client
are different Docker containers with identical test-only application secrets.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from sqlalchemy import create_engine, text

from src.core.database import SessionLocal
from src.core.security import (
    ACCESS_COOKIE, CSRF_COOKIE, create_access_token, token_hash,
)
from src.models.user_session import UserSession
from tests.integration.test_recovery01_a2_real_certificate import (
    AUTHORITY_URL, DOWNSTREAM_URL, _enforce_disposable,
    _seed_authority, _seed_downstream,
)
from tests.integration.test_recovery01_a4_3_real_http import _api_key, _request, _expect_block

ROOT = Path('/app/data/recovery_snapshots')
HTTP_ROOT = 'http://api:8000'
ISSUE_ROUTE = '/api/lineage/recovery-certificates'


def _guard():
    _enforce_disposable()
    assert os.getenv('SDE_LINEAGE_ACCEPTANCE_API_URL') == HTTP_ROOT
    assert os.getenv('DATABASE_URL') == AUTHORITY_URL
    assert os.getenv('SDE_RECOVERY_CERTIFICATE_ISSUANCE_ENABLED') == 'true'
    assert os.getenv('SDE_RECOVERY_CONTEXT_ADMISSION_ENABLED') == 'true'
    assert os.getenv('SDE_RECOVERY_SNAPSHOT_ROOT') == str(ROOT)
    assert json.loads(os.environ['SDE_DOWNSTREAM_DATABASE_URLS']) == {
        'recovery01-disposable': DOWNSTREAM_URL,
    }
    assert ROOT.is_dir()


def _seed_session(user_id):
    session_id = uuid.uuid4()
    with SessionLocal.begin() as db:
        db.add(UserSession(
            id=session_id,
            user_id=user_id,
            refresh_token_hash=hashlib.sha256(secrets.token_bytes(48)).hexdigest(),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            is_revoked=False,
        ))
    return session_id, create_access_token(str(user_id), session_id=session_id)


def _cookies(client, access_token, csrf):
    client.cookies.clear()
    if access_token is not None:
        client.cookies.set(ACCESS_COOKIE, access_token)
    if csrf is not None:
        client.cookies.set(CSRF_COOKIE, csrf)


def _issue(client, source, *, csrf='test-csrf-secret', body=None):
    headers = {'Content-Type': 'application/json'}
    if csrf is not None:
        headers['X-CSRF-Token'] = csrf
    return client.post(ISSUE_ROUTE, json=body or {'source_id': str(source)}, headers=headers)


def _payload(ids):
    ref = ids['ref']
    return {
        'source_id': str(ids['source']),
        'snapshot_id': str(ids['snapshot']),
        'chunk_external_id': ref.chunk_external_id,
        'chunk_revision_external_id': ref.chunk_revision_external_id,
        'index_revision_external_id': f'{ref.index_external_id}@{ref.snapshot_id}',
        'frozen_content_sha256': ref.chunk_content_sha256,
    }


def _count():
    with SessionLocal() as db:
        result = db.execute(text('SELECT count(*) FROM lineage_successor_recovery_certificates')).scalar_one()
        db.rollback()
        return result


def test_a5_real_http_admin_issues_only_evidence_and_admission_rechecks():
    _guard()
    root = ROOT / ('a5_issue_' + uuid.uuid4().hex)
    root.mkdir(mode=0o700)
    ids = _seed_authority(root)
    _seed_downstream(ids)
    _, admin_token = _seed_session(ids['admin'])
    _, viewer_token = _seed_session(ids['viewer'])
    _, foreign_admin_token = _seed_session(ids['other_admin'])
    api_key, _ = _api_key(ids['org'])
    payload = _payload(ids)
    with httpx.Client(base_url=HTTP_ROOT, timeout=30, trust_env=False) as client:
        # Exact physical bytes + materialization are insufficient without an issuer certificate.
        _expect_block(client, api_key, payload, label='a5_before_certificate')
        assert _count() == 0
        _cookies(client, None, None)
        assert _issue(client, ids['source']).status_code == 401
        assert _issue(client, ids['source'], body={'source_id':str(ids['source'])}).status_code == 401
        # Read API key grants no session/CSRF privilege for issuance.
        assert client.post(ISSUE_ROUTE, json={'source_id':str(ids['source'])},
                           headers={'X-API-Key': api_key}).status_code == 401
        _cookies(client, viewer_token, 'test-csrf-secret')
        assert _issue(client, ids['source']).status_code == 403
        _cookies(client, foreign_admin_token, 'test-csrf-secret')
        assert _issue(client, ids['source']).status_code == 409
        _cookies(client, admin_token, None)
        assert _issue(client, ids['source']).status_code == 403
        _cookies(client, admin_token, 'test-csrf-secret')
        assert _issue(client, ids['source'], csrf=None).status_code == 403
        assert _issue(client, ids['source'], csrf='different').status_code == 403
        invalid = _issue(client, ids['source'], body={
            'source_id':str(ids['source']), 'organization_id':str(ids['org'])
        })
        assert invalid.status_code == 422, invalid.text
        assert _count() == 0
        created = _issue(client, ids['source'])
        assert created.status_code == 200, (created.status_code, created.text[:600])
        assert created.headers.get('cache-control') == 'no-store, private'
        certificate = created.json()
        assert certificate['created'] is True
        assert certificate['grants_admission'] is False
        assert certificate['evidence_only'] is True
        assert _count() == 1
        replay = _issue(client, ids['source'])
        assert replay.status_code == 200, (replay.status_code, replay.text[:600])
        assert replay.json()['certificate_id'] == certificate['certificate_id']
        assert replay.json()['created'] is False
        assert _count() == 1
        with SessionLocal() as db:
            row = db.execute(text('''SELECT grants_admission, issued_by_user_id,
                   organization_id, snapshot_id FROM lineage_successor_recovery_certificates''')).one()
            assert row.grants_admission is False
            assert row.issued_by_user_id == ids['admin']
            assert row.organization_id == ids['org']
            assert row.snapshot_id == ids['snapshot']
            db.rollback()
        _cookies(client, None, None)
        allowed = _request(client, api_key, payload)
        assert allowed.status_code == 200, allowed.text[:500]
        assert allowed.json()['outcome'] == 'proceed'
        assert allowed.json()['certificate_id'] == certificate['certificate_id']
        # The same persisted evidence MUST stop admission if the physical bytes diverge.
        downstream = create_engine(DOWNSTREAM_URL)
        try:
            with downstream.begin() as db:
                count = db.execute(text('''UPDATE langchain_pg_embedding
                    SET document=:payload WHERE id=:chunk AND collection_id=:collection'''),
                    {'payload':'malicious-content-after-certificate',
                     'chunk':ids['ref'].chunk_external_id, 'collection':ids['collection']}).rowcount
                assert count == 1
            _expect_block(client, api_key, payload, label='a5_post_issue_mutated_pgvector')
        finally:
            downstream.dispose()
    print('PASS: A5 real admin session+CSRF HTTP -> persisted evidence -> admission reevaluation')


def test_a5_real_http_session_and_role_revocation_take_effect_immediately():
    _guard()
    root = ROOT / ('a5_revoke_' + uuid.uuid4().hex)
    root.mkdir(mode=0o700)
    ids = _seed_authority(root)
    _seed_downstream(ids)
    session_id, admin_token = _seed_session(ids['admin'])
    count_before = _count()
    with httpx.Client(base_url=HTTP_ROOT, timeout=30, trust_env=False) as client:
        _cookies(client, admin_token, 'test-csrf-secret')
        # Real DB updates are reflected through authenticated HTTP, no dependency override.
        with SessionLocal.begin() as db:
            assert db.execute(text('UPDATE users SET role=:role WHERE id=:id'),
                              {'role':'viewer', 'id':ids['admin']}).rowcount == 1
        assert _issue(client, ids['source']).status_code == 403
        with SessionLocal.begin() as db:
            db.execute(text('UPDATE users SET role=:role WHERE id=:id'),
                       {'role':'admin', 'id':ids['admin']})
        with SessionLocal.begin() as db:
            assert db.execute(text('''UPDATE user_sessions SET is_revoked=true,
                    revoked_at=now() WHERE id=:id'''), {'id':session_id}).rowcount == 1
        assert _issue(client, ids['source']).status_code == 401
        assert _count() == count_before
    print('PASS: A5 revoked role/session cannot issue evidence over HTTP')
