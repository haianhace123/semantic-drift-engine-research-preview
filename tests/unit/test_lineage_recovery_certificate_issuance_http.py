"""Guarded admin-session issuance boundary; all database side effects are mocked.

No schema migration, PostgreSQL, Docker or provider call.
"""
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.security import CSRF_COOKIE, get_current_user
from src.api.routes import lineage_recovery_certificate_issuance as api
from src.services.lineage_recovery_certificate_source_service import (
    RecoverySourceAuthority, IssuanceSourceUnavailable,
)
from src.services.lineage_successor_recovery_certificate_service import (
    SuccessorCertificateDenied, SuccessorCertificateIssueResult,
)

ORG = uuid.uuid4()
SOURCE = uuid.uuid4()
SNAP = uuid.uuid4()
USER = uuid.uuid4()
URL = '/api/lineage/recovery-certificates'

@pytest.fixture
def setup(monkeypatch, tmp_path):
    app = FastAPI()
    app.include_router(api.router, prefix='/api/lineage')
    user = SimpleNamespace(id=USER, organization_id=ORG, role='admin')
    app.dependency_overrides[get_current_user] = lambda: user
    monkeypatch.setattr(api.settings, 'SDE_RECOVERY_CERTIFICATE_ISSUANCE_ENABLED', True)
    monkeypatch.setattr(api.settings, 'SDE_RECOVERY_SNAPSHOT_ROOT', str(tmp_path))
    source_calls, issue_calls = [], []
    def source(**kw):
        source_calls.append(kw)
        return RecoverySourceAuthority('https://example.github.io/refund/', SNAP, 'a'*64)
    def issue(**kw):
        issue_calls.append(kw)
        return SuccessorCertificateIssueResult(uuid.uuid4(), True)
    monkeypatch.setattr(api, 'load_current_recovery_source', source)
    monkeypatch.setattr(api, 'issue_successor_recovery_evidence_certificate', issue)
    client = TestClient(app)
    return client, user, source_calls, issue_calls, monkeypatch, tmp_path

def post(client, body=None, *, csrf=True, cookies=True, **kw):
    headers={'X-CSRF-Token':'token'} if csrf else {}
    if cookies:
        client.cookies.set(CSRF_COOKIE,'token')
    else:
        client.cookies.clear()
    return client.post(URL,json={'source_id':str(SOURCE)} if body is None else body,headers=headers,**kw)

def test_valid_admin_provides_only_server_owned_fields(setup):
    client,user,source_calls,issue_calls,_,_ = setup
    result=post(client)
    assert result.status_code==200, result.text
    assert result.json()['grants_admission'] is False
    assert result.json()['evidence_only'] is True
    assert result.headers['cache-control']=='no-store, private'
    assert len(issue_calls)==1 and len(source_calls)==1
    assert source_calls[0]['organization_id']==ORG
    assert source_calls[0]['source_id']==SOURCE
    assert issue_calls[0]['issuer_user_id']==USER
    assert issue_calls[0]['expected_source_url']=='https://example.github.io/refund/'
    assert issue_calls[0]['expected_snapshot_id']==SNAP
    assert issue_calls[0]['expected_snapshot_sha256']=='a'*64
    assert 'database_url' not in issue_calls[0]

def test_disabled_by_default_does_not_touch_storage(setup):
    client,user,source_calls,issue_calls,m,_=setup
    m.setattr(api.settings,'SDE_RECOVERY_CERTIFICATE_ISSUANCE_ENABLED',False)
    result=post(client)
    assert result.status_code==404 and not source_calls and not issue_calls

def test_read_only_api_key_cannot_authenticate_write(setup):
    client,user,source_calls,issue_calls,m,_=setup
    client.app.dependency_overrides.pop(get_current_user)
    result=client.post(URL,json={'source_id':str(SOURCE)},headers={'X-API-Key':'read-only'})
    assert result.status_code==401 and not issue_calls

@pytest.mark.parametrize('role', ['user','viewer',None])
def test_non_admin_forbidden(setup,role):
    client,user,source_calls,issue_calls,*_=setup
    user.role=role
    result=post(client)
    assert result.status_code==403 and not issue_calls

@pytest.mark.parametrize('csrf,cookies',[(False,True),(True,False)])
def test_csrf_required(setup,csrf,cookies):
    client,user,source_calls,issue_calls,*_=setup
    result=post(client,csrf=csrf,cookies=cookies)
    assert result.status_code==403 and not issue_calls

@pytest.mark.parametrize('extra',[
    {'organization_id':str(ORG)}, {'issuer_user_id':str(USER)},
    {'expected_source_url':'https://example.com/'},
    {'certificate_id':str(uuid.uuid4())}, {'database_url':'postgresql://evil'},
    {'snapshot_id':str(SNAP)},
])
def test_no_caller_authority_fields(setup,extra):
    client,user,source_calls,issue_calls,*_=setup
    data={'source_id':str(SOURCE)}; data.update(extra)
    assert post(client,body=data).status_code==422
    assert not issue_calls

def test_oversized_body_rejected(setup):
    client,user,source_calls,issue_calls,*_=setup
    client.cookies.set(CSRF_COOKIE,'token')
    r=client.post(URL,content=b'{'+b'A'*1025+b'}',headers={'Content-Type':'application/json','X-CSRF-Token':'token'})
    assert r.status_code==413 and not issue_calls

def test_missing_snapshot_root_fail_closed(setup):
    client,user,source_calls,issue_calls,m,_=setup
    m.setattr(api.settings,'SDE_RECOVERY_SNAPSHOT_ROOT','')
    assert post(client).status_code==503 and not issue_calls

def test_missing_source_is_409_with_no_details(setup):
    client,user,source_calls,issue_calls,m,_=setup
    def deny(**kw):raise IssuanceSourceUnavailable('other_tenant_unknown')
    m.setattr(api,'load_current_recovery_source',deny)
    r=post(client)
    assert r.status_code==409 and 'other_tenant' not in r.text and not issue_calls

def test_authority_conflict_is_409_without_internal_code(setup):
    client,user,source_calls,issue_calls,m,_=setup
    def deny(**kw):raise SuccessorCertificateDenied('immutable_evidence_conflict')
    m.setattr(api,'issue_successor_recovery_evidence_certificate',deny)
    r=post(client)
    assert r.status_code==409 and 'immutable_evidence' not in r.text

def test_unexpected_error_is_503_and_never_grants(setup):
    client,user,source_calls,issue_calls,m,_=setup
    def fail(**kw):raise RuntimeError('secret database connection')
    m.setattr(api,'issue_successor_recovery_evidence_certificate',fail)
    r=post(client)
    assert r.status_code==503 and 'secret' not in r.text
