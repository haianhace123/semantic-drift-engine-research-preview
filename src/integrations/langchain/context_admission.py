"""Request-time admission boundary for external LangChain consumers.

A successful HTTP integrity query is not itself permission to serve context.
The caller must use the returned admitted documents only after this gate passes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from src.integrations.integrity_admission import (
    IntegrityAdmissionDecision,
    IntegrityAdmissionPolicy,
    evaluate_integrity_admission,
)
from src.integrations.integrity_client import (
    ContextIntegrityAssessment,
    IntegrityRevision,
)
from src.integrations.langchain.context_integrity import (
    ContextIntegrityClientLike,
    query_langchain_context_integrity,
)
from src.integrations.langchain.lineage import LangChainDocumentLike


class LangChainContextNotAdmittedError(RuntimeError):
    """No retrieved content has been admitted for downstream generation."""

    def __init__(self, *, reason: str) -> None:
        self.reason = reason
        super().__init__(f"SDE context admission blocked: {reason}.")


@dataclass(frozen=True, slots=True)
class FrozenLangChainDocument:
    """Shallow-metadata, immutable content snapshot used by admission and model."""

    id: str | None
    metadata: Mapping[str, Any]
    page_content: str


def _freeze_documents(
    documents: Sequence[LangChainDocumentLike],
) -> tuple[FrozenLangChainDocument, ...]:
    snapshots: list[FrozenLangChainDocument] = []
    for document in documents:
        # Fail before querying the authority. Never include untrusted content
        # or metadata values in errors or diagnostic strings.
        document_id = document.id
        content = document.page_content
        metadata = document.metadata
        if not isinstance(document_id, str) or not document_id.strip():
            raise ValueError("Retrieved document must have a stable chunk identity.")
        if not isinstance(content, str):
            raise ValueError("Retrieved document content must be text.")
        if not isinstance(metadata, Mapping):
            raise ValueError("Retrieved document metadata must be a mapping.")
        snapshots.append(
            FrozenLangChainDocument(
                id=document_id,
                page_content=content,
                metadata=MappingProxyType(dict(metadata)),
            )
        )
    return tuple(snapshots)


@dataclass(frozen=True, slots=True)
class AdmittedLangChainContext:
    """Only these snapshotted bytes, not original retrieval objects, are admitted."""

    documents: tuple[FrozenLangChainDocument, ...]
    assessment: ContextIntegrityAssessment
    admission: IntegrityAdmissionDecision


def admit_langchain_context(
    *,
    client: ContextIntegrityClientLike,
    serving_revision: IntegrityRevision,
    retrieved_documents: Sequence[LangChainDocumentLike],
    policy: IntegrityAdmissionPolicy | None = None,
) -> AdmittedLangChainContext:
    """Freeze retrieved bytes before checking exact revisions for model use.

    For SDE-issued content-addressed chunk revisions, the captured document
    bytes must also match the recorded content digest before an HTTP query.
    Opaque third-party revision identities do not have this local byte check.
    Unknown integrity is blocked unless the caller deliberately chooses a
    different policy. Transport, HTTP and protocol errors propagate; no
    fallback context is returned. This is a request-time decision, not an
    irrevocable attestation or a globally consistent read of all writers.
    Metadata is shallow-frozen: nested, non-provenance values are not deep-copied.
    """
    documents = _freeze_documents(tuple(retrieved_documents))
    assessment = query_langchain_context_integrity(
        client=client,
        serving_revision=serving_revision,
        retrieved_documents=documents,
    )
    decision = evaluate_integrity_admission(
        assessment,
        policy=policy if policy is not None else IntegrityAdmissionPolicy(unknown="block"),
    )
    if decision.outcome != "proceed":
        raise LangChainContextNotAdmittedError(reason=decision.reason)
    return AdmittedLangChainContext(
        documents=documents,
        assessment=assessment,
        admission=decision,
    )
