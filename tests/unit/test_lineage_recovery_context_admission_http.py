"""A4.2 HTTP boundary; exercises real X-API-Key dependencies with a fake DB.

No Docker, production backend or model calls.
"""
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core import security
from src.core.database import get_db
from src.api.routes import lineage_recovery_context_admission as api
from src.services.lineage_recovery_context_admission_service import RecoveryContextAdmissionDecision

ORG = uuid.uuid4()
KEY = uuid.uuid4()
SOURCE = uuid.uuid4()
SNAP = uuid.uuid4()
URL = '/api/lineage/recovery-context-admission/query'


class FakeDB:
    commits = 0
    def commit(self): self.commits += 1


@pytest.fixture
def boundary(monkeypatch, tmp_path):
    app = FastAPI()
    app.include_router(api.router, prefix='/api/lineage')
    db = FakeDB()
    app.dependency_overrides[get_db] = lambda: db
    key = SimpleNamespace(id=KEY, organization_id=ORG, permissions={'read': True},
                          is_active=True, last_used_at=None)
    monkeypatch.setattr(security, 'verify_api_key', lambda raw, session: key if raw == 'valid' else None)
    monkeypatch.setattr(api.settings, 'SDE_RECOVERY_CONTEXT_ADMISSION_ENABLED', False)
    monkeypatch.setattr(api.settings, 'SDE_RECOVERY_SNAPSHOT_ROOT', '')
    return TestClient(app), key, db, monkeypatch, tmp_path


def payload():
    return {'source_id': str(SOURCE), 'snapshot_id': str(SNAP),
            'chunk_external_id':'chunk', 'chunk_revision_external_id':'rev',
            'index_revision_external_id':'index', 'frozen_content_sha256':'a'*64}


def post(client, obj=None, key='valid'):
    return client.post(URL, json=payload() if obj is None else obj,
                       headers={'X-API-Key':key} if key is not None else {})


def test_missing_key_is_401(boundary):
    client, *_ = boundary
    r = post(client, key=None)
    assert r.status_code == 401
    assert 'certificate' not in r.text


def test_invalid_key_is_401(boundary):
    client, *_ = boundary
    assert post(client, key='bad').status_code == 401


def test_read_permission_is_required(boundary):
    client, key, *_ = boundary
    key.permissions = {'read': False}
    assert post(client).status_code == 403


def test_default_off_is_block_and_never_calls_service(boundary):
    client, key, db, monkeypatch, _ = boundary
    monkeypatch.setattr(api, 'decide_recovery_context_admission', lambda **kw: (_ for _ in ()).throw(RuntimeError('should not run')))
    r = post(client)
    assert r.status_code == 200
    assert r.json() == {'outcome':'block','reason':'unsupported','snapshot_id':None,
                        'certificate_id':None, 'chunk_content_sha256':None,
                        'request_scoped':True}
    assert r.headers.get('cache-control') == 'no-store, private'
    assert db.commits == 1  # Existing auth dependency updates last_used_at.


@pytest.mark.parametrize('change', [
    {'organization_id':str(ORG)}, {'certificate_id':str(uuid.uuid4())},
    {'database_url':'postgresql://evil'}, {'issued_by_user_id':str(uuid.uuid4())},
    {'frozen_content_sha256':'A'*64}, {'frozen_content_sha256':'0'*63},
    {'chunk_external_id':'x'*513}, {'chunks':[{}]},
])
def test_untrusted_fields_or_bad_inputs_rejected(boundary, change):
    client, *_ = boundary
    p = payload();p.update(change)
    r = post(client, p)
    assert r.status_code == 422
    assert r.json()['detail'] == 'Invalid recovery admission request'


def test_oversized_chunked_upload_blocked(boundary):
    client, *_ = boundary
    def chunks():
        yield b'{"source_id":"'
        yield b'A'*9000
    r=client.post(URL, content=chunks(), headers={'X-API-Key':'valid','Content-Type':'application/json'})
    assert r.status_code == 413


def test_api_forwards_only_server_tenant_and_key(boundary):
    client, key, db, monkeypatch, root = boundary
    monkeypatch.setattr(api.settings, 'SDE_RECOVERY_CONTEXT_ADMISSION_ENABLED', True)
    monkeypatch.setattr(api.settings, 'SDE_RECOVERY_SNAPSHOT_ROOT', str(root))
    calls=[]
    def fake_decide(**kwargs):
        calls.append(kwargs)
        return RecoveryContextAdmissionDecision('proceed','verified_successor',snapshot_id=SNAP,
                                                 certificate_id=uuid.uuid4(),chunk_content_sha256='a'*64)
    monkeypatch.setattr(api, 'decide_recovery_context_admission', fake_decide)
    r=post(client)
    assert r.status_code == 200, r.text
    assert r.json()['outcome']=='proceed' and r.json()['reason']=='verified_successor'
    assert r.headers['cache-control']=='no-store, private'
    assert len(calls)==1
    assert calls[0]['organization_id']==ORG and calls[0]['authenticated_api_key_id']==KEY
    assert calls[0]['context'].source_id == SOURCE
    assert calls[0]['context'].snapshot_id == SNAP
    assert calls[0]['context'].frozen_content_sha256=='a'*64
    assert calls[0]['snapshot_root']==root
    assert calls[0]['authority_session_factory']==api.SessionLocal


def test_invalid_server_root_blocks_without_backend(boundary):
    client, key, db, monkeypatch, root = boundary
    monkeypatch.setattr(api.settings, 'SDE_RECOVERY_CONTEXT_ADMISSION_ENABLED', True)
    monkeypatch.setattr(api.settings, 'SDE_RECOVERY_SNAPSHOT_ROOT', 'relative/path')
    monkeypatch.setattr(api, 'decide_recovery_context_admission', lambda **kw: (_ for _ in ()).throw(RuntimeError('should not run')))
    assert post(client).json()['reason']=='unsupported'


def test_service_failure_does_not_leak_sensitive_data(boundary):
    client, key, db, monkeypatch, root = boundary
    monkeypatch.setattr(api.settings, 'SDE_RECOVERY_CONTEXT_ADMISSION_ENABLED', True)
    monkeypatch.setattr(api.settings, 'SDE_RECOVERY_SNAPSHOT_ROOT', str(root))
    monkeypatch.setattr(api, 'decide_recovery_context_admission', lambda **kw: (_ for _ in ()).throw(RuntimeError('private database password')))
    r=post(client)
    assert r.status_code==200 and r.json()['reason']=='verification_failed'
    assert 'private' not in r.text


def test_lineage_parent_router_includes_endpoint():
    from src.api.routes.lineage import router
    assert any(getattr(route, 'path', '') == URL and 'POST' in getattr(route, 'methods', set()) for route in router.routes)


def test_non_json_content_type_rejected(boundary):
    client, *_ = boundary
    r=client.post(URL, content=b'{}', headers={'X-API-Key':'valid','Content-Type':'text/plain'})
    assert r.status_code == 415
