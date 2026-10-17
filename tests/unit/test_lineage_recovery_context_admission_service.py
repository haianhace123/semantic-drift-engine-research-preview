"""A4.1 request-bound decision. No HTTP, provider, model or DB writes."""
from __future__ import annotations

import uuid
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime, timezone, timedelta

import pytest

from src.services import lineage_recovery_context_admission_service as svc
from src.services.lineage_successor_recovery_preflight_service import SuccessorRecoveryPreflight
from src.domain.lineage_revision import LineageRevisionIdentity
from src.services.lineage_pilot_snapshot_reference_service import PilotSnapshotReference
from src.services.lineage_successor_recovery_preflight_service import _AuthorityFacts

ORG = uuid.UUID('b46e8a62-40b8-4d59-aea7-3713ad759eaa')
OTHER = uuid.UUID('85f2d6eb-4ae5-4e33-98b9-81ce851e5b5e')
KEY = uuid.UUID('985e8903-0fd7-4d78-b677-52ef3b85b02b')
SOURCE = uuid.UUID('b5f2da9f-f5ef-47e3-9a7c-6d6a1b295e24')
SNAP = uuid.UUID('17c81630-a33c-49fd-8d04-aeb17f28f569')
CHUNK = f'sde-pilot-chunk:{SOURCE}:0'
SOURCE_EXTERNAL = f'sde-pilot-source:{SOURCE}'
INDEX = f'sde-pilot-index:{SOURCE}'
INDEX_REV = f'{INDEX}@{SNAP}'
CHUNK_SHA = 'b' * 64
CHUNK_REV = f'{CHUNK}:{CHUNK_SHA[:16]}'
CHUNK_OBS = uuid.uuid4()
INDEX_OBS = uuid.uuid4()
CERT = uuid.uuid4()
URL = 'https://customer.github.io/recovery-policy/'


def sample():
    context = svc.FrozenSuccessorContext(
        source_id=SOURCE, snapshot_id=SNAP,
        chunk_external_id=CHUNK, chunk_revision_external_id=CHUNK_REV,
        index_revision_external_id=INDEX_REV, frozen_content_sha256=CHUNK_SHA,
    )
    ref = PilotSnapshotReference(
        organization_id=ORG, source_id=SOURCE, snapshot_id=SNAP,
        snapshot_sha256='a'*64, canonicalization_version='sde-pilot-main-text-v1',
        chunk_content_sha256=CHUNK_SHA,
        source_external_id=SOURCE_EXTERNAL, chunk_external_id=CHUNK,
        chunk_revision_external_id=CHUNK_REV, index_external_id=INDEX,
        collection_name=f'sde_pilot_{ORG.hex}_{SOURCE.hex}',
    )
    facts = _AuthorityFacts(
        reference=ref, target=LineageRevisionIdentity(SOURCE_EXTERNAL, INDEX, INDEX_REV),
        chunk_observation_id=CHUNK_OBS, index_observation_id=INDEX_OBS,
        binding_revision=1, connection_ref='trusted-tenant-connection',
    )
    evidence = svc._AuthorityEvidence(
        facts=facts, source_url=URL, certificate_id=CERT, certificate_digest=CHUNK_SHA,
        certificate_identity=(CERT, ORG, SOURCE, SNAP, CHUNK_SHA),
    )
    proof = SuccessorRecoveryPreflight(
        organization_id=ORG, source_id=SOURCE, snapshot_id=SNAP,
        target=facts.target, chunk_revision_external_id=CHUNK_REV,
        chunk_sha256=CHUNK_SHA, chunk_observation_id=CHUNK_OBS,
        index_observation_id=INDEX_OBS, binding_revision=1,
        witness_status='digest_match', grants_admission=False,
    )
    return context, evidence, proof


def invoke(monkeypatch, *, enabled=True, context=None, org=ORG, actor=KEY, before=None, after=None, proof=None):
    original_context, evidence, physical = sample()
    context = original_context if context is None else context
    before = evidence if before is None else before
    after = evidence if after is None else after
    proof = physical if proof is None else proof
    reads = []
    probes = []
    monkeypatch.setattr(svc, '_read_only', lambda factory, call: call(None))
    def get_current(db, **kwargs):
        reads.append(kwargs)
        if kwargs['context'] != original_context:
            raise ValueError('frozen input differs from server source')
        return before if len(reads) == 1 else after
    def get_physical(**kwargs):
        probes.append(kwargs)
        return proof
    monkeypatch.setattr(svc, '_current_authority', get_current)
    monkeypatch.setattr(svc, 'assess_verified_successor_recovery_candidate', get_physical)
    outcome = svc.decide_recovery_context_admission(
        enabled=enabled, authority_session_factory=lambda: None,
        snapshot_root=Path('/trusted'), organization_id=org,
        authenticated_api_key_id=actor, context=context,
    )
    return outcome, reads, probes


def test_disabled_default_does_not_touch_database(monkeypatch):
    output, reads, probes = invoke(monkeypatch, enabled=False)
    assert output == svc.RecoveryContextAdmissionDecision('block', 'unsupported')
    assert reads == [] and probes == []


def test_successor_frozen_digest_certificate_and_physical_all_match(monkeypatch):
    output, reads, probes = invoke(monkeypatch)
    assert output.outcome == 'proceed' and output.reason == 'verified_successor'
    assert output.certificate_id == CERT and output.snapshot_id == SNAP
    assert output.chunk_content_sha256 == CHUNK_SHA
    assert len(reads) == 2 and len(probes) == 1
    assert probes[0]['expected_source_url'] == URL
    assert probes[0]['organization_id'] == ORG


@pytest.mark.parametrize('tamper', [
    lambda c: replace(c, frozen_content_sha256='0'*64),
    lambda c: replace(c, frozen_content_sha256='B'*64),
    lambda c: replace(c, frozen_content_sha256='b'*63),
    lambda c: replace(c, snapshot_id=uuid.uuid4()),
    lambda c: replace(c, chunk_revision_external_id='untrusted'),
    lambda c: replace(c, index_revision_external_id='unexpected'),
    lambda c: replace(c, chunk_external_id=''),
])
def test_bad_or_changed_context_never_grants(monkeypatch, tamper):
    context, _, _ = sample()
    modified = tamper(context)
    output, reads, probes = invoke(monkeypatch, context=modified)
    assert output.outcome == 'block'
    assert probes == []



def test_wrong_physical_digest_blocks(monkeypatch):
    _, _, proof = sample()
    output, reads, probes = invoke(monkeypatch, proof=replace(proof, chunk_sha256='f'*64))
    assert output.outcome == 'block' and output.reason == 'verification_failed'
    assert len(reads) == 1


def test_foreign_organization_physical_proof_blocks(monkeypatch):
    _, _, proof = sample()
    output, _, _ = invoke(monkeypatch, proof=replace(proof, organization_id=OTHER))
    assert output.outcome == 'block'


def test_witness_is_not_admission_grant(monkeypatch):
    _, _, proof = sample()
    output, _, _ = invoke(monkeypatch, proof=replace(proof, grants_admission=True))
    assert output.outcome == 'block'


def test_snapshot_or_certificate_changes_after_witness_block(monkeypatch):
    _, authority, _ = sample()
    output, reads, probes = invoke(monkeypatch, after=replace(authority, certificate_id=uuid.uuid4()))
    assert output.outcome == 'block' and output.reason == 'not_current'
    assert len(reads) == 2 and len(probes) == 1


def test_authenticated_key_revoked_on_second_read_blocks(monkeypatch):
    _, authority, _ = sample()
    calls = 0
    monkeypatch.setattr(svc, '_read_only', lambda factory, fn: fn(None))
    def authority_read(db, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PermissionError('revoked')
        return authority
    monkeypatch.setattr(svc, '_current_authority', authority_read)
    monkeypatch.setattr(svc, 'assess_verified_successor_recovery_candidate', lambda **kw: sample()[2])
    output = svc.decide_recovery_context_admission(
        enabled=True, authority_session_factory=lambda: None, snapshot_root=Path('/trusted'),
        organization_id=ORG, authenticated_api_key_id=KEY, context=sample()[0],
    )
    assert output.outcome == 'block' and output.reason == 'denied'


def test_timeout_or_any_unexpected_error_blocks_without_details(monkeypatch):
    monkeypatch.setattr(svc, '_read_only', lambda factory, fn: (_ for _ in ()).throw(TimeoutError('private DSN')))
    output = svc.decide_recovery_context_admission(
        enabled=True, authority_session_factory=lambda: None, snapshot_root=Path('/trusted'),
        organization_id=ORG, authenticated_api_key_id=KEY, context=sample()[0],
    )
    assert output == svc.RecoveryContextAdmissionDecision('block', 'verification_failed')
    assert 'private DSN' not in str(output)


def test_reader_permission_and_expiration_is_server_owned():
    class Query:
        def __init__(self, result): self.result = result
        def filter(self, *args): return self
        def one_or_none(self): return self.result
    class Database:
        def __init__(self, result): self.result=result
        def query(self, model):
            assert model is svc.ApiKey
            return Query(self.result)
    current = datetime.now(timezone.utc)
    key = SimpleNamespace(permissions={'read': True}, expires_at=current+timedelta(minutes=10))
    assert svc._active_reader(Database(key), organization_id=ORG, api_key_id=KEY)
    assert not svc._active_reader(Database(replace_namespace(key, permissions={'read': False})), organization_id=ORG, api_key_id=KEY)
    assert not svc._active_reader(Database(replace_namespace(key, expires_at=current-timedelta(seconds=1))), organization_id=ORG, api_key_id=KEY)
    assert not svc._active_reader(Database(None), organization_id=ORG, api_key_id=KEY)


def replace_namespace(ns, **kwargs):
    return SimpleNamespace(**(vars(ns) | kwargs))


def test_issuer_must_still_be_active_admin_of_same_tenant():
    class Query:
        def __init__(self, result): self.result=result
        def filter(self,*args): return self
        def one_or_none(self): return self.result
    class DB:
        def __init__(self, result): self.result=result
        def query(self, model):
            assert model is svc.User
            return Query(self.result)
    assert svc._issuer_is_current_admin(DB(SimpleNamespace(role='admin')), organization_id=ORG, issuer_id=KEY)
    assert not svc._issuer_is_current_admin(DB(None), organization_id=ORG, issuer_id=KEY)
