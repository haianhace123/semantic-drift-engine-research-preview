"""Fail-closed, single-chunk SDE pilot serving reference.

Bounded to the frozen sde-pilot-main-text-v1 canonicalization and the
source/index/chunk identities created by the pilot materializer. Not a general
LangChain verified-serving API, recovery grant, or cross-database atomic read.

The caller supplies trusted PostgreSQL Session factories, a read-only snapshot
volume mount, an authenticated tenant-scoped integrity client, a retriever, and
a model callback. No SDE/PGVector writes or external provider calls are made
by this module except the caller-controlled model invocation *after* checks.
"""
from __future__ import annotations

import hashlib
import hmac
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, TypeVar

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.integrations.integrity_client import IntegrityRevision
from src.integrations.langchain.context_admission import admit_langchain_context
from src.integrations.langchain.context_integrity import ContextIntegrityClientLike
from src.integrations.langchain.lineage import LangChainDocumentLike
from src.services.lineage_pgvector_physical_witness_service import (
    MAX_WITNESS_CONTENT_BYTES,
    inspect_pgvector_chunk_physical_witness,
)
from src.services.lineage_pilot_snapshot_reference_service import (
    CANONICALIZATION_VERSION,
    PilotSnapshotReference,
    load_pilot_snapshot_reference,
)


class VerifiedPilotServingBlocked(RuntimeError):
    """The pilot has not proved that the exact prompt chunk is safe to serve."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"SDE verified pilot serving refused: {reason}.")


@dataclass(frozen=True, slots=True)
class _FrozenDocument:
    id: str
    metadata: Mapping[str, Any]
    page_content: str


@dataclass(frozen=True, slots=True)
class VerifiedPilotModelResult:
    output: str
    snapshot_id: uuid.UUID
    chunk_content_sha256: str
    admission_reason: str


T = TypeVar("T")


def _read_only_postgres(factory: Callable[[], Session], action: Callable[[Session], T]) -> T:
    """Execute a bounded read using a fresh, read-only PostgreSQL transaction."""
    session = factory()
    try:
        bind = session.get_bind()
        if session.in_transaction():
            raise VerifiedPilotServingBlocked("preexisting_db_transaction")
        if bind.dialect.name != "postgresql":
            raise VerifiedPilotServingBlocked("untrusted_database_dialect")
        # First statement: creates a new REPEATABLE READ / READ ONLY transaction.
        # Do not call Session.execute or begin a transaction in the factory.
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        return action(session)
    finally:
        try:
            session.rollback()
        finally:
            session.close()


def _snapshot_document(document: LangChainDocumentLike) -> _FrozenDocument:
    if not isinstance(document.id, str) or not document.id:
        raise VerifiedPilotServingBlocked("invalid_document_identity")
    if not isinstance(document.page_content, str) or not document.page_content:
        raise VerifiedPilotServingBlocked("invalid_document_content")
    if not isinstance(document.metadata, Mapping):
        raise VerifiedPilotServingBlocked("invalid_document_metadata")
    # Capture metadata and content once, before any authority/HTTP operations.
    copied = dict(document.metadata)
    return _FrozenDocument(
        id=document.id,
        metadata=MappingProxyType(copied),
        page_content=document.page_content,
    )


def _verify_reference_identity(
    reference: PilotSnapshotReference,
    *,
    organization_id: uuid.UUID,
    source_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    snapshot_sha256: str,
) -> None:
    if (
        reference.organization_id != organization_id
        or reference.source_id != source_id
        or reference.snapshot_id != snapshot_id
        or reference.snapshot_sha256 != snapshot_sha256
        or reference.canonicalization_version != CANONICALIZATION_VERSION
    ):
        raise VerifiedPilotServingBlocked("authority_identity_mismatch")


def _verify_prompt_document(
    document: _FrozenDocument,
    reference: PilotSnapshotReference,
) -> None:
    if (
        document.id != reference.chunk_external_id
        or document.metadata.get("source_external_id") != reference.source_external_id
        or document.metadata.get("snapshot_external_id") != str(reference.snapshot_id)
        or document.metadata.get("chunk_revision_external_id")
        != reference.chunk_revision_external_id
    ):
        raise VerifiedPilotServingBlocked("retrieved_provenance_mismatch")
    encoded = document.page_content.encode("utf-8")
    if not encoded or len(encoded) > MAX_WITNESS_CONTENT_BYTES:
        raise VerifiedPilotServingBlocked("retrieved_content_out_of_bounds")
    digest = hashlib.sha256(encoded).hexdigest()
    if not hmac.compare_digest(digest, reference.chunk_content_sha256):
        raise VerifiedPilotServingBlocked("prompt_content_digest_mismatch")


def run_verified_pilot_model_call(
    *,
    question: str,
    retrieve: Callable[[str], Sequence[LangChainDocumentLike]],
    client: ContextIntegrityClientLike,
    invoke_model: Callable[[str], str],
    authority_session_factory: Callable[[], Session],
    physical_session_factory: Callable[[], Session],
    snapshot_root: Path,
    organization_id: uuid.UUID,
    source_id: uuid.UUID,
    expected_source_url: str,
    expected_snapshot_id: uuid.UUID,
    expected_snapshot_sha256: str,
) -> VerifiedPilotModelResult:
    """Permit one model call only for matching authority, physical and prompt bytes.

    The final authority lookup starts a NEW transaction after physical proof and
    authenticated integrity admission. This detects authority advancement that
    precedes the final recheck. It does NOT provide a lock/lease against an
    authority change immediately after recheck, or prove all index chunks.
    """
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question must be a non-empty string")
    if not isinstance(organization_id, uuid.UUID) or not isinstance(source_id, uuid.UUID):
        raise TypeError("organization_id and source_id must be UUID")
    if not isinstance(expected_snapshot_id, uuid.UUID):
        raise TypeError("expected_snapshot_id must be UUID")
    if not isinstance(snapshot_root, Path):
        raise TypeError("snapshot_root must be Path")

    documents = tuple(retrieve(question))
    if len(documents) != 1:
        raise VerifiedPilotServingBlocked("pilot_requires_exactly_one_chunk")
    document = _snapshot_document(documents[0])

    def authority_read(session: Session) -> PilotSnapshotReference:
        return load_pilot_snapshot_reference(
            session,
            organization_id=organization_id,
            source_id=source_id,
            expected_source_url=expected_source_url,
            expected_snapshot_id=expected_snapshot_id,
            expected_snapshot_sha256=expected_snapshot_sha256,
            snapshot_root=snapshot_root,
        )

    original = _read_only_postgres(authority_session_factory, authority_read)
    _verify_reference_identity(
        original,
        organization_id=organization_id,
        source_id=source_id,
        snapshot_id=expected_snapshot_id,
        snapshot_sha256=expected_snapshot_sha256,
    )
    _verify_prompt_document(document, original)

    witness = _read_only_postgres(
        physical_session_factory,
        lambda session: inspect_pgvector_chunk_physical_witness(
            session,
            organization_id=original.organization_id,
            source_external_id=original.source_external_id,
            index_external_id=original.index_external_id,
            collection_name=original.collection_name,
            chunk_external_id=original.chunk_external_id,
            chunk_revision_external_id=original.chunk_revision_external_id,
            snapshot_external_id=str(original.snapshot_id),
            expected_content_sha256=original.chunk_content_sha256,
        ),
    )
    if not witness.matches_reference_content:
        raise VerifiedPilotServingBlocked("physical_" + witness.status)

    serving_revision = IntegrityRevision(
        source_external_id=original.source_external_id,
        asset_external_id=original.index_external_id,
        revision_external_id=f"{original.index_external_id}@{original.snapshot_id}",
    )
    admitted = admit_langchain_context(
        client=client,
        serving_revision=serving_revision,
        retrieved_documents=(document,),
        # Deliberately do NOT allow unknown=proceed.
    )
    if (
        len(admitted.documents) != 1
        or admitted.documents[0].id != document.id
        or admitted.documents[0].page_content != document.page_content
        or dict(admitted.documents[0].metadata) != dict(document.metadata)
    ):
        raise VerifiedPilotServingBlocked("admission_context_identity_mismatch")

    # Fresh transaction AFTER HTTP admission and the physical read. Never
    # reuse the first REPEATABLE READ snapshot, which would conceal updates.
    current = _read_only_postgres(authority_session_factory, authority_read)
    if current != original:
        raise VerifiedPilotServingBlocked("authority_changed_before_model")
    _verify_reference_identity(
        current,
        organization_id=organization_id,
        source_id=source_id,
        snapshot_id=expected_snapshot_id,
        snapshot_sha256=expected_snapshot_sha256,
    )
    _verify_prompt_document(document, current)

    # Use only the snapshotted, independently verified document bytes.
    prompt = (
        "Answer the question using only the supplied verified pilot context.\n"
        f"Question: {question}\n"
        f"Verified context:\n{admitted.documents[0].page_content}"
    )
    output = invoke_model(prompt)
    return VerifiedPilotModelResult(
        output=output,
        snapshot_id=current.snapshot_id,
        chunk_content_sha256=current.chunk_content_sha256,
        admission_reason=admitted.admission.reason,
    )
