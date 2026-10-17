from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.domain.lineage_revision import LineageRevisionIdentity
from src.services.lineage_pilot_snapshot_reference_service import PilotSnapshotReference
from src.services import lineage_successor_recovery_preflight_service as service

ORG = uuid.UUID('35cb7596-5265-4576-a971-1a254b62a98a')
SOURCE = uuid.UUID('68ab76d2-76b5-4e0e-b972-222fbf6eb1d7')
SNAPSHOT = uuid.UUID('8cb89d8a-d766-436f-98a4-ecebbdc8f013')
DIGEST = 'a' * 64


def reference() -> PilotSnapshotReference:
    return PilotSnapshotReference(
        organization_id=ORG, source_id=SOURCE, snapshot_id=SNAPSHOT,
        snapshot_sha256='b' * 64, canonicalization_version='sde-pilot-main-text-v1',
        chunk_content_sha256=DIGEST,
        source_external_id=f'sde-pilot-source:{SOURCE}',
        chunk_external_id=f'sde-pilot-chunk:{SOURCE}:0',
        chunk_revision_external_id=f'sde-pilot-chunk:{SOURCE}:0:{DIGEST[:16]}',
        index_external_id=f'sde-pilot-index:{SOURCE}',
        collection_name=f'sde_pilot_{ORG.hex}_{SOURCE.hex}',
    )


def test_authority_read_requires_exact_mat_observations_and_binding(monkeypatch):
    ref = reference()
    target = LineageRevisionIdentity(ref.source_external_id, ref.index_external_id,
        f'{ref.index_external_id}@{ref.snapshot_id}')
    chunk = SimpleNamespace(id=uuid.uuid4(), asset_kind='chunk',
        snapshot_external_id=str(SNAPSHOT), revision_external_id=ref.chunk_revision_external_id,
        dependencies=[])
    index = SimpleNamespace(id=uuid.uuid4(), asset_kind='index',
        snapshot_external_id=str(SNAPSHOT), revision_external_id=target.revision_external_id,
        dependencies=[SimpleNamespace(asset_external_id=ref.chunk_external_id,
            revision_external_id=ref.chunk_revision_external_id, dependency_type='indexed_from')])
    binding = SimpleNamespace(provider='pgvector', target_kind='langchain_pgvector_causal_chunk',
        configuration={'collection_name':ref.collection_name, 'connection_ref':'tenant-pg'},
        binding_revision=2)
    monkeypatch.setattr(service, 'load_pilot_snapshot_reference', lambda *a, **kw: ref)
    monkeypatch.setattr(service, 'assess_lineage_target_integrity',
        lambda *a, **kw: SimpleNamespace(status='unknown'))
    monkeypatch.setattr(service, '_latest_observation',
        lambda *a, asset_external_id, **kw: chunk if asset_external_id==ref.chunk_external_id else index)
    monkeypatch.setattr(service, 'load_current_lineage_execution_target_binding',
        lambda *a, **kw: binding)
    kwargs=dict(db=object(), organization_id=ORG, source_id=SOURCE,
        source_url='https://example.github.io/pilot/', snapshot_id=SNAPSHOT,
        snapshot_sha256='b'*64, snapshot_root=Path('/trusted/raw'))
    facts = service._authority_facts(**kwargs)
    assert facts.reference == ref
    assert facts.index_observation_id == index.id
    assert facts.binding_revision == 2
    assert facts.connection_ref == 'tenant-pg'

    # No semantic no_action fixture is permitted as a replacement for an
    # independently issued successor certificate.
    monkeypatch.setattr(service, 'assess_lineage_target_integrity',
        lambda *a, **kw: SimpleNamespace(status='not_affected'))
    with pytest.raises(service.SuccessorPreflightBlocked, match='successor_not_unknown'):
        service._authority_facts(**kwargs)

    monkeypatch.setattr(service, 'assess_lineage_target_integrity',
        lambda *a, **kw: SimpleNamespace(status='unknown'))
    index.dependencies[0].revision_external_id='spoofed'
    with pytest.raises(service.SuccessorPreflightBlocked, match='index_dependency_mismatch'):
        service._authority_facts(**kwargs)

    index.dependencies[0].revision_external_id=ref.chunk_revision_external_id
    binding.configuration['collection_name']='wrong_collection'
    with pytest.raises(service.SuccessorPreflightBlocked, match='binding_mismatch'):
        service._authority_facts(**kwargs)


def _arrange_probe(monkeypatch, *, second=None, witness='digest_match'):
    ref = reference()
    first = service._AuthorityFacts(
        reference=ref,
        target=LineageRevisionIdentity(ref.source_external_id, ref.index_external_id,
            f'{ref.index_external_id}@{SNAPSHOT}'),
        chunk_observation_id=uuid.uuid4(), index_observation_id=uuid.uuid4(),
        binding_revision=1, connection_ref='tenant-pg',
    )
    proof_reads=[]

    def authority(*a, **kw):
        proof_reads.append(1)
        return first if len(proof_reads)==1 else (first if second is None else second)

    monkeypatch.setattr(service, '_authority_facts', authority)
    monkeypatch.setattr(service, '_read_only', lambda factory, action: action(object()))
    monkeypatch.setattr(service, 'resolve_lineage_downstream_connection',
        lambda *, connection_ref: SimpleNamespace(connection_ref=connection_ref))
    class Engine:
        disposed = False
        def dispose(self): self.disposed = True
    engine = Engine()
    monkeypatch.setattr(service, 'create_lineage_downstream_session',
        lambda connection: (object(), engine))
    monkeypatch.setattr(service, 'inspect_pgvector_chunk_physical_witness',
        lambda *a, **kw: SimpleNamespace(status=witness,
            matches_reference_content=(witness=='digest_match')))
    return first, proof_reads, engine


def _assess():
    return service.assess_verified_successor_recovery_candidate(
        authority_session_factory=lambda: object(), organization_id=ORG, source_id=SOURCE,
        expected_source_url='https://example.github.io/pilot/',
        expected_snapshot_id=SNAPSHOT, expected_snapshot_sha256='b'*64,
        snapshot_root=Path('/trusted/raw'))


def test_verified_candidate_carries_facts_but_never_grants(monkeypatch):
    original, reads, engine = _arrange_probe(monkeypatch)
    result = _assess()
    assert result.snapshot_id == SNAPSHOT
    assert result.witness_status == 'digest_match'
    assert result.grants_admission is False
    assert result.chunk_observation_id == original.chunk_observation_id
    assert reads == [1, 1]
    assert engine.disposed


@pytest.mark.parametrize('physical_status', ['absent', 'identity_mismatch', 'content_unavailable', 'digest_mismatch'])
def test_physical_mismatch_blocks(monkeypatch, physical_status):
    original, reads, engine = _arrange_probe(monkeypatch, witness=physical_status)
    with pytest.raises(service.SuccessorPreflightBlocked, match='physical_'+physical_status):
        _assess()
    assert reads == [1]
    assert engine.disposed


def test_authority_changes_after_physical_read_block(monkeypatch):
    ref = reference()
    modified = service._AuthorityFacts(
        reference=ref,
        target=LineageRevisionIdentity(ref.source_external_id, ref.index_external_id,
            f'{ref.index_external_id}@{SNAPSHOT}'),
        chunk_observation_id=uuid.uuid4(), index_observation_id=uuid.uuid4(),
        binding_revision=2, connection_ref='new-binding',
    )
    original, reads, engine = _arrange_probe(monkeypatch, second=modified)
    with pytest.raises(service.SuccessorPreflightBlocked, match='authority_changed_after_physical_read'):
        _assess()
    assert reads == [1, 1]
    assert engine.disposed


def test_wrong_identity_rejected_before_any_database_work(monkeypatch):
    called=[]
    monkeypatch.setattr(service, '_read_only', lambda *a, **kw: called.append(1))
    with pytest.raises(TypeError, match='UUID'):
        service.assess_verified_successor_recovery_candidate(
            authority_session_factory=lambda: object(), organization_id='wrong',
            source_id=SOURCE, expected_source_url='https://example.github.io/',
            expected_snapshot_id=SNAPSHOT, expected_snapshot_sha256='b'*64,
            snapshot_root=Path('/tmp'))
    assert not called
