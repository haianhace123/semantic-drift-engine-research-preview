"""Counterfactual checks for SDE-issued content-addressed chunk revisions."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib

import pytest

from experiments.guarded_langchain_consumer import run_guarded_model_call
from src.integrations.integrity_client import (
    ContextIntegrityAssessment,
    ContextIntegritySummary,
    IntegrityRevision,
    TargetIntegrityAssessment,
)
from src.integrations.langchain.lineage import (
    prepare_langchain_documents_for_indexing,
)


@dataclass
class Document:
    id: str | None
    page_content: str
    metadata: dict[str, str]


SERVING = IntegrityRevision("source-a", "index-a", "index-v1")


class CleanClient:
    def __init__(self) -> None:
        self.calls = []

    def query_context_integrity(self, targets):
        self.calls.append(tuple(targets))
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


def sde_document(content="Refunds within 30 days."):
    doc = Document(
        id="chunk-refund",
        page_content=content,
        metadata={"source_external_id": "source-a", "snapshot_external_id": "s1"},
    )
    prepare_langchain_documents_for_indexing(documents=(doc,))
    return doc


def run(doc):
    called = []
    client = CleanClient()
    result = run_guarded_model_call(
        question="Refund window?",
        retrieve=lambda _: (doc,),
        client=client,
        serving_revision=SERVING,
        invoke_model=lambda prompt: called.append(prompt) or "answer",
    )
    return result, called, client.calls


def test_sde_generated_unchanged_document_reaches_model():
    original = sde_document()
    result, called, queries = run(original)
    assert result.admitted_document_count == 1
    assert len(called) == 1
    assert "Refunds within 30 days." in called[0]
    assert queries[0][1].revision_external_id == original.metadata["chunk_revision_external_id"]


def test_modified_bytes_with_reused_sde_revision_must_never_reach_model():
    doc = sde_document()
    doc.page_content = "Refunds within 365 days."
    calls = []
    client = CleanClient()
    with pytest.raises(ValueError, match="content.*revision|revision.*content"):
        run_guarded_model_call(
            question="Refund window?",
            retrieve=lambda _: (doc,),
            client=client,
            serving_revision=SERVING,
            invoke_model=lambda prompt: calls.append(prompt) or "unsafe",
        )
    assert calls == []
    assert client.calls == []


def test_matching_digest_for_non_ascii_chunk_is_admitted():
    doc = sde_document("Hoàn tiền trong 7 ngày.")
    assert hashlib.sha256(doc.page_content.encode("utf-8")).hexdigest()[:16] in doc.metadata["chunk_revision_external_id"]
    result, prompts, _ = run(doc)
    assert result.admitted_document_count == 1
    assert "Hoàn tiền trong 7 ngày." in prompts[0]


def test_content_addressed_digest_mismatch_never_reaches_integrity_api():
    doc = sde_document()
    doc.metadata["chunk_revision_external_id"] = "chunk-refund:" + "0" * 16
    with pytest.raises(ValueError, match="content.*revision|revision.*content"):
        run(doc)


def test_content_addressed_digest_is_specific_to_chunk_identity():
    doc = sde_document()
    doc.id = "different-id"
    with pytest.raises(ValueError, match="content.*revision|revision.*content"):
        run(doc)


def test_legacy_opaque_revision_retains_identity_only_semantics():
    doc = Document(
        id="chunk-refund", page_content="External text.",
        metadata={"source_external_id": "source-a", "chunk_revision_external_id": "rev-v1"},
    )
    result, prompts, _ = run(doc)
    assert result.admitted_document_count == 1
    assert "External text." in prompts[0]


def test_one_poisoned_chunk_blocks_whole_context_before_model_and_http():
    trusted = sde_document()
    poisoned = sde_document("Second chunk")
    poisoned.id = "chunk-two"
    prepare_langchain_documents_for_indexing(documents=(poisoned,))
    poisoned.page_content = "Injected unsafe content"
    client = CleanClient()
    model_calls = []
    with pytest.raises(ValueError, match="content.*revision|revision.*content"):
        run_guarded_model_call(
            question="Refund window?",
            retrieve=lambda _: (trusted, poisoned),
            client=client,
            serving_revision=SERVING,
            invoke_model=lambda prompt: model_calls.append(prompt) or "unsafe",
        )
    assert model_calls == []
    assert client.calls == []


def test_asset_id_with_colons_uses_last_colon_for_digest():
    doc = Document(
        id="policy:refund:chunk",
        page_content="Text with colons in identity.",
        metadata={"source_external_id": "source-a", "snapshot_external_id": "s1"},
    )
    prepare_langchain_documents_for_indexing(documents=(doc,))
    result, prompts, _ = run(doc)
    assert result.admitted_document_count == 1
    assert "Text with colons" in prompts[0]
