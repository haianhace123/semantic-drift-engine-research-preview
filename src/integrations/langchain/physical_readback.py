"""Optional point-in-time PGVector readback before lineage publication.

This verifies *only* the chunks submitted by this writer call and their exact
bytes/provenance. It is not a complete dependency manifest, durable index
attestation, tenant authorization, or source-event freshness certificate.
The caller must bind the writer to the correct, authorized collection.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from src.integrations.langchain.lineage import (
    CHUNK_REVISION_EXTERNAL_ID_KEY,
    SNAPSHOT_EXTERNAL_ID_KEY,
    SOURCE_EXTERNAL_ID_KEY,
    LangChainDocumentLike,
    capture_langchain_context_revisions,
)


class LangChainPhysicalReadbackError(RuntimeError):
    """The physical chunk bytes/provenance could not be verified."""


class LangChainDocumentReadback(Protocol):
    def get_by_ids(self, ids: list[str]) -> list[LangChainDocumentLike]: ...


@dataclass(frozen=True, slots=True)
class ExpectedPhysicalChunk:
    chunk_external_id: str
    revision_external_id: str
    source_external_id: str
    snapshot_external_id: str
    content: str


def _meta(metadata: Any, key: str) -> str | None:
    if not isinstance(metadata, Mapping):
        return None
    value = metadata.get(key)
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    return value


def snapshot_physical_readback_expectations(
    documents: Sequence[LangChainDocumentLike],
) -> tuple[ExpectedPhysicalChunk, ...]:
    """Snapshot facts from prepared writer inputs *before* physical I/O."""
    # The content-addressed revisions issued by SDE must match actual input
    # bytes; don't let malformed inputs be certified by a later matching row.
    capture_langchain_context_revisions(retrieved_documents=documents)
    expected: list[ExpectedPhysicalChunk] = []
    seen: set[str] = set()
    for document in documents:
        chunk = document.id
        if not isinstance(chunk, str) or not chunk or chunk != chunk.strip():
            raise ValueError("Prepared physical chunk ID must be canonical.")
        if chunk in seen:
            raise ValueError("Prepared physical chunk IDs must be unique.")
        seen.add(chunk)
        if not isinstance(document.page_content, str):
            raise ValueError("Prepared physical chunk content must be text.")
        source = _meta(document.metadata, SOURCE_EXTERNAL_ID_KEY)
        snapshot = _meta(document.metadata, SNAPSHOT_EXTERNAL_ID_KEY)
        revision = _meta(document.metadata, CHUNK_REVISION_EXTERNAL_ID_KEY)
        if not all((source, snapshot, revision)):
            raise ValueError("Prepared physical chunk provenance is incomplete.")
        expected.append(ExpectedPhysicalChunk(
            chunk_external_id=chunk,
            revision_external_id=revision,
            source_external_id=source,
            snapshot_external_id=snapshot,
            content=document.page_content,
        ))
    return tuple(expected)


def ensure_physical_readback_available(vector_store: object) -> None:
    if not callable(getattr(vector_store, "get_by_ids", None)):
        raise LangChainPhysicalReadbackError(
            "Strict PGVector readback requires a get_by_ids-capable, "
            "collection-scoped vector store; no physical write attempted."
        )


def verify_physical_readback(
    vector_store: LangChainDocumentReadback,
    *,
    expected: tuple[ExpectedPhysicalChunk, ...],
) -> None:
    """Reject omissions, duplicates, other IDs and mismatched physical bytes.

    A successful read is a *point-in-time* observation. The DB can change
    afterward, and a dishonest or wrongly bound reader cannot be authenticated
    by this function. No automatic retry or authority publication on mismatch.
    """
    ensure_physical_readback_available(vector_store)
    expected_by_id = {item.chunk_external_id: item for item in expected}
    try:
        actual = vector_store.get_by_ids(list(expected_by_id))
    except Exception as exc:
        raise LangChainPhysicalReadbackError(
            "PGVector physical readback failed; reconcile before retrying."
        ) from exc

    if not isinstance(actual, (list, tuple)) or len(actual) != len(expected):
        raise LangChainPhysicalReadbackError(
            "PGVector readback did not return exactly the expected chunks."
        )
    seen: set[str] = set()
    for document in actual:
        chunk_id = getattr(document, "id", None)
        if not isinstance(chunk_id, str) or chunk_id not in expected_by_id or chunk_id in seen:
            raise LangChainPhysicalReadbackError(
                "PGVector readback returned a duplicate or unexpected chunk ID."
            )
        seen.add(chunk_id)
        reference = expected_by_id[chunk_id]
        if (
            getattr(document, "page_content", None) != reference.content
            or _meta(getattr(document, "metadata", None), SOURCE_EXTERNAL_ID_KEY)
                != reference.source_external_id
            or _meta(getattr(document, "metadata", None), SNAPSHOT_EXTERNAL_ID_KEY)
                != reference.snapshot_external_id
            or _meta(getattr(document, "metadata", None), CHUNK_REVISION_EXTERNAL_ID_KEY)
                != reference.revision_external_id
        ):
            raise LangChainPhysicalReadbackError(
                "PGVector readback content or provenance differs from the prepared input."
            )
