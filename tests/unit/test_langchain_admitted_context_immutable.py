"""Counterfactual: admission must return the bytes it actually assessed."""
from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass
from types import MappingProxyType

import pytest

from src.integrations.integrity_client import (
    ContextIntegrityAssessment,
    ContextIntegritySummary,
    IntegrityRevision,
    TargetIntegrityAssessment,
)
from src.integrations.langchain.context_admission import admit_langchain_context
from src.integrations.langchain.lineage import prepare_langchain_documents_for_indexing


@dataclass
class Document:
    id: str
    page_content: str
    metadata: dict


SERVING = IntegrityRevision("policy", "index", "index-r1")


def retrieved(content="Refunds within 30 days."):
    doc = Document("refund", content, {
        "source_external_id": "policy",
        "snapshot_external_id": "snapshot-r1",
        "category": "refunds",
    })
    prepare_langchain_documents_for_indexing(documents=(doc,))
    return doc


class CleanClient:
    def __init__(self, *, during_query=None):
        self.calls = []
        self.during_query = during_query

    def query_context_integrity(self, targets):
        self.calls.append(tuple(targets))
        if self.during_query:
            self.during_query()
        return ContextIntegrityAssessment(
            assessments=tuple(
                TargetIntegrityAssessment(target=target, status="not_affected", evidence=())
                for target in targets
            ),
            summary=ContextIntegritySummary(
                target_count=len(targets),
                unknown_count=0,
                not_affected_count=len(targets),
                affected_count=0,
                verified_count=0,
                has_unknown=False,
                has_unresolved_impact=False,
            ),
        )


def admit(docs, client):
    return admit_langchain_context(
        client=client,
        serving_revision=SERVING,
        retrieved_documents=docs,
    )


def test_original_mutated_during_http_cannot_change_admitted_bytes_or_revision():
    doc = retrieved()
    original_revision = doc.metadata["chunk_revision_external_id"]
    client = CleanClient(during_query=lambda: (
        setattr(doc, "page_content", "Refunds within 365 days."),
        doc.metadata.update({"chunk_revision_external_id": "fake-revision"}),
    ))
    result = admit([doc], client)
    assert result.documents[0] is not doc
    assert result.documents[0].page_content == "Refunds within 30 days."
    assert result.documents[0].metadata["chunk_revision_external_id"] == original_revision
    assert client.calls[0][1].revision_external_id == original_revision


def test_original_mutated_after_http_cannot_change_admitted_context():
    doc = retrieved()
    result = admit([doc], CleanClient())
    doc.page_content = "Refunds within 365 days."
    doc.metadata["category"] = "poisoned"
    assert result.documents[0].page_content == "Refunds within 30 days."
    assert result.documents[0].metadata["category"] == "refunds"


def test_admitted_document_content_and_metadata_cannot_be_modified():
    doc = retrieved()
    frozen = admit([doc], CleanClient()).documents[0]
    assert isinstance(frozen.metadata, MappingProxyType)
    with pytest.raises(FrozenInstanceError):
        frozen.page_content = "unsafe"
    with pytest.raises(TypeError):
        frozen.metadata["source_external_id"] = "attacker"


def test_mixed_context_is_detached_and_identity_preserved():
    first = retrieved()
    second = retrieved("A second independent clause.")
    second.id = "second"
    prepare_langchain_documents_for_indexing(documents=(second,))
    result = admit([first, second], CleanClient())
    assert len(result.documents) == 2
    assert [d.page_content for d in result.documents] == [
        "Refunds within 30 days.", "A second independent clause.",
    ]


def test_modified_content_addressed_bytes_block_before_remote_query():
    doc = retrieved()
    doc.page_content = "Refunds within 365 days."
    client = CleanClient()
    with pytest.raises(ValueError, match="content.*revision|revision.*content"):
        admit([doc], client)
    assert client.calls == []


def test_invalid_content_type_blocked_before_remote_query_without_leak():
    doc = retrieved()
    doc.page_content = object()
    client = CleanClient()
    with pytest.raises(ValueError, match="content must be text"):
        admit([doc], client)
    assert client.calls == []
