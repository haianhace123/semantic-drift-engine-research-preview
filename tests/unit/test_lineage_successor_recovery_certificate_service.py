from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.domain.lineage_revision import LineageRevisionIdentity
from src.services import lineage_successor_recovery_certificate_service as svc
from src.services import lineage_successor_recovery_preflight_service as a1

ORG = uuid.UUID('35cb7596-5265-4576-a971-1a254b62a98a')
SOURCE = uuid.UUID('68ab76d2-76b5-4e0e-b972-222fbf6eb1d7')
SNAPSHOT = uuid.UUID('8cb89d8a-d766-436f-98a4-ecebbdc8f013')
ADMIN = uuid.UUID('41b36106-615f-44e0-8452-d5d455b5102c')
CHUNK_OBS = uuid.UUID('2ca57bd6-36f4-4506-8f29-1801b8acfa91')
INDEX_OBS = uuid.UUID('62dba53b-43b7-44df-b8b5-50e2ba27ba31')
REF_SHA = 'a'*64
DIGEST = 'b'*64
TARGET = LineageRevisionIdentity(f'sde-pilot-source:{SOURCE}', f'sde-pilot-index:{SOURCE}', f'sde-pilot-index:{SOURCE}@{SNAPSHOT}')


def proof():
    return a1.SuccessorRecoveryPreflight(
        organization_id=ORG, source_id=SOURCE, snapshot_id=SNAPSHOT,
        target=TARGET, chunk_revision_external_id=f'sde-pilot-chunk:{SOURCE}:0:{DIGEST[:16]}',
        chunk_sha256=DIGEST, chunk_observation_id=CHUNK_OBS,
        index_observation_id=INDEX_OBS, binding_revision=1,
        witness_status='digest_match',
    )


def kwargs():
    return dict(authority_session_factory=lambda: None, organization_id=ORG,
                source_id=SOURCE, expected_source_url='https://example.github.io/pilot/',
                expected_snapshot_id=SNAPSHOT, expected_snapshot_sha256=REF_SHA,
                snapshot_root=Path('/trusted/raw'), issuer_user_id=ADMIN)


class Query:
    def __init__(self, result): self.result = result
    def filter(self, *args): return self
    def one_or_none(self): return self.result


class DB:
    def __init__(self, *, existing=None, role='admin'):
        self.existing = existing
        self.issuer = SimpleNamespace(role=role, organization_id=ORG)
        self.added = None
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0
        self.flushed = 0
        self.isolation = 'read committed'
    def get_bind(self): return SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
    def in_transaction(self): return False
    def execute(self, stmt): return SimpleNamespace(scalar_one=lambda: self.isolation)
    def query(self, cls):
        if cls is svc.User: return Query(self.issuer)
        if cls is svc.LineageSuccessorRecoveryCertificateRecord: return Query(self.existing)
        raise AssertionError('unplanned query')
    def add(self, row): self.added = row
    def flush(self): self.flushed += 1
    def commit(self): self.commits += 1
    def rollback(self): self.rollbacks += 1
    def close(self): self.closed += 1


def setup(monkeypatch, db, *, candidate=None):
    counts={'witness':0, 'lock':0,'fence':0}
    def witness(**kw):
        counts['witness'] += 1
        return candidate or proof()
    monkeypatch.setattr(svc, 'assess_verified_successor_recovery_candidate', witness)
    monkeypatch.setattr(svc, 'acquire_lineage_target_authority_lock', lambda *a, **kw: counts.__setitem__('lock', counts['lock']+1))
    monkeypatch.setattr(svc, '_fence_against_current_authority', lambda *a, **kw: counts.__setitem__('fence', counts['fence']+1))
    monkeypatch.setattr(svc, '_enforce_transaction', lambda db: None)
    values=kwargs();values['authority_session_factory']=lambda:db
    return values,counts


def test_valid_certificate_insert_never_grants_admission(monkeypatch):
    db=DB();values,c=setup(monkeypatch,db)
    result=svc.issue_successor_recovery_evidence_certificate(**values)
    assert result.created is True and result.grants_admission is False
    assert db.added.snapshot_id == SNAPSHOT
    assert db.added.grants_admission is False
    assert db.added.organization_id == ORG
    assert db.added.issued_by_user_id == ADMIN
    assert db.commits == 1 and db.closed == 1 and db.flushed == 1
    assert c == {'witness':1,'lock':1,'fence':1}


def _existing():
    return SimpleNamespace(id=uuid.uuid4(), source_id=SOURCE, snapshot_id=SNAPSHOT,
        snapshot_sha256=REF_SHA, target_source_external_id=TARGET.source_external_id,
        target_asset_external_id=TARGET.asset_external_id,
        target_revision_external_id=TARGET.revision_external_id,
        chunk_revision_external_id=proof().chunk_revision_external_id,
        chunk_content_sha256=DIGEST, chunk_observation_id=CHUNK_OBS,
        index_observation_id=INDEX_OBS, binding_revision=1,
        issued_by_user_id=ADMIN,evidence_kind='physical_successor_preflight_v1',
        grants_admission=False)


def test_idempotent_replay_revalidates_witness_and_does_not_write(monkeypatch):
    db=DB(existing=_existing());values,c=setup(monkeypatch,db)
    result=svc.issue_successor_recovery_evidence_certificate(**values)
    assert not result.created and not result.grants_admission
    assert db.commits==1 and db.added is None and db.closed==1
    assert c['witness']==1 and c['fence']==1


def test_conflicting_replay_fails_closed(monkeypatch):
    old=_existing();old.chunk_content_sha256='9'*64
    db=DB(existing=old);values,c=setup(monkeypatch,db)
    with pytest.raises(svc.SuccessorCertificateDenied,match='immutable_evidence_conflict'):
        svc.issue_successor_recovery_evidence_certificate(**values)
    assert db.commits==0 and db.rollbacks==1 and db.closed==1


def test_unprivileged_user_denied(monkeypatch):
    db=DB(role='viewer');values,c=setup(monkeypatch,db)
    with pytest.raises(svc.SuccessorCertificateDenied,match='tenant_admin_required'):
        svc.issue_successor_recovery_evidence_certificate(**values)
    assert db.added is None and db.rollbacks==1


def test_physical_proof_must_be_real_digest_match(monkeypatch):
    candidate=SimpleNamespace(witness_status='absent',grants_admission=False)
    db=DB();values,c=setup(monkeypatch,db,candidate=candidate)
    with pytest.raises(svc.SuccessorCertificateDenied,match='physical_proof_insufficient'):
        svc.issue_successor_recovery_evidence_certificate(**values)
    assert db.closed==0 and c['lock']==0


def test_unexpected_grant_flag_cannot_be_persisted(monkeypatch):
    candidate=SimpleNamespace(witness_status='digest_match',grants_admission=True)
    db=DB();values,c=setup(monkeypatch,db,candidate=candidate)
    with pytest.raises(svc.SuccessorCertificateDenied,match='physical_proof_insufficient'):
        svc.issue_successor_recovery_evidence_certificate(**values)
    assert db.added is None


def test_final_authority_fence_failure_rolls_back(monkeypatch):
    db=DB();values,c=setup(monkeypatch,db)
    def fail(*a,**kw): raise svc.SuccessorCertificateDenied('changed_binding')
    monkeypatch.setattr(svc,'_fence_against_current_authority',fail)
    with pytest.raises(svc.SuccessorCertificateDenied,match='changed_binding'):
        svc.issue_successor_recovery_evidence_certificate(**values)
    assert db.commits==0 and db.rollbacks==1 and db.closed==1


def test_postgres_and_read_committed_required():
    db=DB()
    svc._enforce_transaction(db)
    db.isolation='repeatable read'
    with pytest.raises(svc.SuccessorCertificateDenied,match='read_committed_required'):
        svc._enforce_transaction(db)
    db.isolation='read committed'
    db.get_bind=lambda:SimpleNamespace(dialect=SimpleNamespace(name='sqlite'))
    with pytest.raises(svc.SuccessorCertificateDenied,match='postgres_required'):
        svc._enforce_transaction(db)


def test_exact_revision_authority_fence(monkeypatch):
    p=proof()
    ref=SimpleNamespace(snapshot_id=SNAPSHOT,
         chunk_revision_external_id=p.chunk_revision_external_id,
         chunk_content_sha256=DIGEST)
    facts=SimpleNamespace(reference=ref,target=TARGET,chunk_observation_id=CHUNK_OBS,
                          index_observation_id=INDEX_OBS,binding_revision=1)
    monkeypatch.setattr(svc,'_authority_facts',lambda *a,**kw:facts)
    svc._fence_against_current_authority(None, proof=p,organization_id=ORG,
        source_id=SOURCE,source_url='https://example.github.io/',snapshot_sha256=REF_SHA,
        snapshot_root=Path('/trusted/raw'))
    facts.binding_revision=2
    with pytest.raises(svc.SuccessorCertificateDenied,match='preflight_authority_fence_failed'):
        svc._fence_against_current_authority(None, proof=p,organization_id=ORG,
            source_id=SOURCE,source_url='https://example.github.io/',snapshot_sha256=REF_SHA,
            snapshot_root=Path('/trusted/raw'))


def test_no_witness_work_on_invalid_identity(monkeypatch):
    db=DB();values,c=setup(monkeypatch,db)
    values['organization_id']='not_uuid'
    with pytest.raises(TypeError,match='must be UUID'):
        svc.issue_successor_recovery_evidence_certificate(**values)
    assert c['witness']==0
