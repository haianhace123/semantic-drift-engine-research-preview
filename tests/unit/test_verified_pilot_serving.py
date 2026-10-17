"""Verified single-chunk pilot orchestrator contract, without live DB/model.

The existing authority/PGVector service tests and isolated real PostgreSQL gates
are separate evidence; these tests target ordered consumer enforcement only.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any

import pytest

from experiments import verified_pilot_serving as module
from src.integrations.integrity_client import (
    ContextIntegrityAssessment,
    ContextIntegritySummary,
    IntegrityRevision,
    SDEIntegrityHTTPError,
    SDEIntegrityProtocolError,
    TargetIntegrityAssessment,
)
from src.integrations.langchain.context_admission import LangChainContextNotAdmittedError
from src.services.lineage_pgvector_physical_witness_service import PgVectorChunkWitness
from src.services.lineage_pilot_snapshot_reference_service import (
    CANONICALIZATION_VERSION,
    PilotSnapshotReference,
    PilotSnapshotReferenceError,
)

ORG = uuid.UUID("edc45ed4-1063-4b0a-8f5e-f0ca34e1d702")
SOURCE = uuid.UUID("f5e25ef6-8d23-4a65-9b74-4101804041f2")
SNAP = uuid.UUID("783140a8-e2af-42ad-8e1b-9f7d90f2e231")
URL = "https://product15-example.github.io/policy/"
TEXT = "Policy Rule v2"
DIGEST = hashlib.sha256(TEXT.encode("utf-8")).hexdigest()
SHA = "b" * 64


@dataclass
class Document:
    id: str | None
    metadata: dict[str, Any]
    page_content: str


def reference() -> PilotSnapshotReference:
    chunk = f"sde-pilot-chunk:{SOURCE}:0"
    return PilotSnapshotReference(
        organization_id=ORG,
        source_id=SOURCE,
        snapshot_id=SNAP,
        snapshot_sha256=SHA,
        canonicalization_version=CANONICALIZATION_VERSION,
        chunk_content_sha256=DIGEST,
        source_external_id=f"sde-pilot-source:{SOURCE}",
        chunk_external_id=chunk,
        chunk_revision_external_id=f"{chunk}:{DIGEST[:16]}",
        index_external_id=f"sde-pilot-index:{SOURCE}",
        collection_name=f"sde_pilot_{ORG.hex}_{SOURCE.hex}",
    )


def document() -> Document:
    ref = reference()
    return Document(
        id=ref.chunk_external_id,
        page_content=TEXT,
        metadata={
            "source_external_id": ref.source_external_id,
            "snapshot_external_id": str(ref.snapshot_id),
            "chunk_revision_external_id": ref.chunk_revision_external_id,
        },
    )


class FakeSession:
    def __init__(self, name, events, dialect="postgresql", preexisting=False):
        self.name = name
        self.events = events
        self.dialect = dialect
        self.preexisting = preexisting
        self.closed = False
        self.rolled_back = False

    def get_bind(self):
        return SimpleNamespace(dialect=SimpleNamespace(name=self.dialect))

    def in_transaction(self):
        return self.preexisting

    def execute(self, statement):
        self.events.append((self.name, "SET", str(statement)))

    def rollback(self):
        self.events.append((self.name, "rollback"))
        self.rolled_back = True

    def close(self):
        self.events.append((self.name, "close"))
        self.closed = True


class FakeClient:
    def __init__(self, *, status="not_affected", error=None, on_admit=None, events=None):
        self.status = status
        self.error = error
        self.on_admit = on_admit
        self.targets = None
        self.events = events if events is not None else []

    def query_context_integrity(self, targets):
        self.events.append(("http", "admission"))
        self.targets = tuple(targets)
        if self.on_admit:
            self.on_admit()
        if self.error:
            raise self.error
        count = len(targets)
        counts = {name: count if name == self.status else 0 for name in (
            "unknown", "not_affected", "affected", "verified"
        )}
        return ContextIntegrityAssessment(
            assessments=tuple(TargetIntegrityAssessment(target=t, status=self.status, evidence=()) for t in targets),
            summary=ContextIntegritySummary(
                target_count=count,
                unknown_count=counts["unknown"],
                not_affected_count=counts["not_affected"],
                affected_count=counts["affected"],
                verified_count=counts["verified"],
                has_unknown=self.status == "unknown",
                has_unresolved_impact=self.status == "affected",
            ),
        )


@pytest.fixture
def harness(monkeypatch, tmp_path):
    events = []
    authority_sessions = []
    physical_sessions = []
    state = {"refs": [reference(), reference()], "physical": PgVectorChunkWitness("digest_match", "content_matches_supplied_digest")}
    doc = document()
    calls = []
    client = FakeClient(events=events)

    def authority_factory():
        session = FakeSession(f"authority-{len(authority_sessions) + 1}", events)
        authority_sessions.append(session)
        return session

    def physical_factory():
        session = FakeSession("physical", events)
        physical_sessions.append(session)
        return session

    def load(session, **kwargs):
        events.append((session.name, "read_authority"))
        assert kwargs["expected_snapshot_id"] == SNAP
        return state["refs"].pop(0)

    def inspect(session, **kwargs):
        events.append((session.name, "read_physical"))
        assert kwargs["expected_content_sha256"] == DIGEST
        assert kwargs["collection_name"] == reference().collection_name
        assert kwargs["chunk_external_id"] == reference().chunk_external_id
        assert kwargs["snapshot_external_id"] == str(SNAP)
        return state["physical"]

    monkeypatch.setattr(module, "load_pilot_snapshot_reference", load)
    monkeypatch.setattr(module, "inspect_pgvector_chunk_physical_witness", inspect)

    def invoke_model(prompt):
        events.append(("model", "called"))
        calls.append(prompt)
        return "model answer"

    kwargs = dict(
        question="What is the rule?",
        retrieve=lambda question: [doc],
        client=client,
        invoke_model=invoke_model,
        authority_session_factory=authority_factory,
        physical_session_factory=physical_factory,
        snapshot_root=tmp_path,
        organization_id=ORG,
        source_id=SOURCE,
        expected_source_url=URL,
        expected_snapshot_id=SNAP,
        expected_snapshot_sha256=SHA,
    )
    return SimpleNamespace(kwargs=kwargs, doc=doc, client=client, state=state, calls=calls,
                           events=events, authority_sessions=authority_sessions, physical_sessions=physical_sessions)


def _blocked(harness, reason):
    with pytest.raises(module.VerifiedPilotServingBlocked) as exc:
        module.run_verified_pilot_model_call(**harness.kwargs)
    assert exc.value.reason == reason
    assert harness.calls == []


def test_verified_single_chunk_invokes_once_and_uses_exact_bytes(harness):
    result = module.run_verified_pilot_model_call(**harness.kwargs)
    assert result.output == "model answer"
    assert result.snapshot_id == SNAP
    assert result.chunk_content_sha256 == DIGEST
    assert result.admission_reason == "no_unresolved_impact"
    assert len(harness.calls) == 1
    assert TEXT in harness.calls[0]
    assert harness.events == [
        ("authority-1", "SET", "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"),
        ("authority-1", "read_authority"), ("authority-1", "rollback"), ("authority-1", "close"),
        ("physical", "SET", "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"),
        ("physical", "read_physical"), ("physical", "rollback"), ("physical", "close"),
        ("http", "admission"),
        ("authority-2", "SET", "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"),
        ("authority-2", "read_authority"), ("authority-2", "rollback"), ("authority-2", "close"),
        ("model", "called"),
    ]
    assert len(harness.authority_sessions) == 2
    assert harness.authority_sessions[0] is not harness.authority_sessions[1]
    assert all(item.closed and item.rolled_back for item in harness.authority_sessions + harness.physical_sessions)
    assert harness.client.targets == (
        IntegrityRevision(reference().source_external_id, reference().index_external_id, f"{reference().index_external_id}@{SNAP}"),
        IntegrityRevision(reference().source_external_id, reference().chunk_external_id, reference().chunk_revision_external_id),
    )


@pytest.mark.parametrize("status,reason", [
    ("unknown", "unknown_integrity"),
    ("affected", "unresolved_impact"),
    ("verified", "superseded_revision"),
])
def test_admission_blocks_before_model_and_fresh_read(harness, status, reason):
    harness.client.status = status
    with pytest.raises(LangChainContextNotAdmittedError) as exc:
        module.run_verified_pilot_model_call(**harness.kwargs)
    assert exc.value.reason == reason
    assert not harness.calls
    assert len(harness.authority_sessions) == 1
    assert all(item.closed for item in harness.authority_sessions + harness.physical_sessions)


@pytest.mark.parametrize("error", [
    TimeoutError("timeout"), SDEIntegrityHTTPError(503), SDEIntegrityProtocolError("malformed")
])
def test_transport_http_protocol_errors_fail_closed(harness, error):
    harness.client.error = error
    with pytest.raises(type(error)):
        module.run_verified_pilot_model_call(**harness.kwargs)
    assert not harness.calls
    assert len(harness.authority_sessions) == 1


@pytest.mark.parametrize("status", ["digest_mismatch", "identity_mismatch", "content_unavailable", "absent", "identity_match_only"])
def test_physical_bad_status_blocks_before_admission(harness, status):
    harness.state["physical"] = PgVectorChunkWitness(status, "test_refusal")
    _blocked(harness, "physical_" + status)
    assert harness.client.targets is None


def test_changed_prompt_bytes_with_matching_metadata_fail_closed(harness):
    harness.doc.page_content = "Policy Rule v1"
    _blocked(harness, "prompt_content_digest_mismatch")
    assert harness.client.targets is None
    assert not harness.physical_sessions


@pytest.mark.parametrize("change", [
    {"id": "other-chunk"},
    {"metadata": {"source_external_id": "foreign", "snapshot_external_id": str(SNAP), "chunk_revision_external_id": reference().chunk_revision_external_id}},
    {"metadata": {"source_external_id": reference().source_external_id, "snapshot_external_id": "old-snapshot", "chunk_revision_external_id": reference().chunk_revision_external_id}},
    {"metadata": {"source_external_id": reference().source_external_id, "snapshot_external_id": str(SNAP), "chunk_revision_external_id": "old-revision"}},
])
def test_bad_retrieved_provenance_blocks(harness, change):
    for key, value in change.items():
        setattr(harness.doc, key, value)
    _blocked(harness, "retrieved_provenance_mismatch")
    assert harness.client.targets is None


@pytest.mark.parametrize("documents", [[], [document(), document()]])
def test_zero_or_multiple_chunks_block_without_accessing_authority(harness, documents):
    harness.kwargs["retrieve"] = lambda question: documents
    _blocked(harness, "pilot_requires_exactly_one_chunk")
    assert not harness.authority_sessions


def test_fresh_snapshot_change_after_http_refused(harness):
    harness.state["refs"][1] = replace(reference(), chunk_content_sha256="f" * 64)
    _blocked(harness, "authority_changed_before_model")
    assert len(harness.authority_sessions) == 2


def test_authority_lookup_not_latest_blocks_without_model(harness, monkeypatch):
    def stale(session, **kwargs):
        raise PilotSnapshotReferenceError("Expected snapshot is not the latest")
    monkeypatch.setattr(module, "load_pilot_snapshot_reference", stale)
    with pytest.raises(PilotSnapshotReferenceError, match="not the latest"):
        module.run_verified_pilot_model_call(**harness.kwargs)
    assert not harness.calls


def test_mutated_raw_retrieval_during_http_cannot_change_prompt(harness):
    original = harness.doc
    harness.client.on_admit = lambda: setattr(original, "page_content", "tampered after snapshot")
    module.run_verified_pilot_model_call(**harness.kwargs)
    assert len(harness.calls) == 1
    assert TEXT in harness.calls[0]
    assert "tampered after snapshot" not in harness.calls[0]


def test_wrong_authority_identity_blocks(harness):
    harness.state["refs"][0] = replace(reference(), organization_id=uuid.uuid4())
    _blocked(harness, "authority_identity_mismatch")
    assert not harness.physical_sessions


def test_read_only_role_rejects_other_dialects(harness):
    factory = harness.kwargs["authority_session_factory"]
    def sqlite_factory():
        session = factory()
        session.dialect = "sqlite"
        return session
    harness.kwargs["authority_session_factory"] = sqlite_factory
    _blocked(harness, "untrusted_database_dialect")
    assert harness.authority_sessions[0].closed


def test_preexisting_transaction_rejected(harness):
    factory = harness.kwargs["authority_session_factory"]
    def already_active():
        session = factory()
        session.preexisting = True
        return session
    harness.kwargs["authority_session_factory"] = already_active
    _blocked(harness, "preexisting_db_transaction")


def test_invalid_question_skips_retrieval(harness):
    harness.kwargs["question"] = "  "
    with pytest.raises(ValueError):
        module.run_verified_pilot_model_call(**harness.kwargs)
    assert not harness.authority_sessions
    assert not harness.calls
