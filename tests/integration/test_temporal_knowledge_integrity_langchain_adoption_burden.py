from __future__ import annotations

import pytest

from src.integrations.langchain import (
    acquire_langchain_chunk_provenance,
    capture_langchain_consumer_binding,
    capture_langchain_index_materialization,
    prepare_langchain_documents_for_indexing,
)


pytest.importorskip("langchain_core.documents")

from langchain_core.documents import Document


SOURCE_EXTERNAL_ID = "policy-source"
SNAPSHOT_V1 = "snapshot-v1"
SNAPSHOT_V2 = "snapshot-v2"


def _document(
    *,
    document_id: str | None,
    text: str,
    snapshot_external_id: str,
) -> Document:
    return Document(
        id=document_id,
        page_content=text,
        metadata={
            "source_external_id": SOURCE_EXTERNAL_ID,
            "snapshot_external_id": snapshot_external_id,
        },
    )



def test_stable_upstream_identity_survives_semantic_change() -> None:
    previous_document = _document(
        document_id="chunk-refund-policy",
        text="Refunds are available within 30 days.",
        snapshot_external_id=SNAPSHOT_V1,
    )
    changed_document = _document(
        document_id="chunk-refund-policy",
        text="Refunds are available within 14 days.",
        snapshot_external_id=SNAPSHOT_V2,
    )

    previous = acquire_langchain_chunk_provenance(
        documents=[previous_document],
    )
    changed = acquire_langchain_chunk_provenance(
        documents=[changed_document],
        previous_documents=[previous_document],
    )

    assert previous[0].asset_external_id == "chunk-refund-policy"
    assert changed[0].asset_external_id == "chunk-refund-policy"
    assert previous[0].identity_source == "upstream"
    assert changed[0].identity_source == "upstream"
    assert (
        previous[0].revision_external_id
        != changed[0].revision_external_id
    )


def test_missing_upstream_identity_only_recovers_unchanged_content() -> None:
    previous_document = _document(
        document_id="chunk-refund-policy",
        text="Refunds are available within 30 days.",
        snapshot_external_id=SNAPSHOT_V1,
    )
    unchanged_document = _document(
        document_id=None,
        text="Refunds are available within 30 days.",
        snapshot_external_id=SNAPSHOT_V2,
    )
    changed_document = _document(
        document_id=None,
        text="Refunds are available within 14 days.",
        snapshot_external_id=SNAPSHOT_V2,
    )

    unchanged = acquire_langchain_chunk_provenance(
        documents=[unchanged_document],
        previous_documents=[previous_document],
    )
    changed = acquire_langchain_chunk_provenance(
        documents=[changed_document],
        previous_documents=[previous_document],
    )

    assert unchanged[0].asset_external_id == "chunk-refund-policy"
    assert unchanged[0].identity_source == "exact_content"

    assert changed[0].identity_source == "new"
    assert (
        changed[0].asset_external_id
        != "chunk-refund-policy"
    )


def test_three_causal_boundaries_preserve_exact_lineage() -> None:
    raw_documents = [
        _document(
            document_id="chunk-refund-policy",
            text="Refunds are available within 14 days.",
            snapshot_external_id=SNAPSHOT_V2,
        ),
        _document(
            document_id="chunk-escalation-policy",
            text="Escalations require manager approval.",
            snapshot_external_id=SNAPSHOT_V2,
        ),
    ]

    prepared = prepare_langchain_documents_for_indexing(
        documents=raw_documents,
    )
    provenance = prepared.provenance
    materialized_documents = prepared.documents

    materialization = capture_langchain_index_materialization(
        documents=materialized_documents,
        index_external_id="support-index",
        index_revision_external_id="support-index@v2",
        sequence=2,
    )

    retrieved_documents = [
        materialized_documents[0],
    ]

    consumption = capture_langchain_consumer_binding(
        retrieved_documents=retrieved_documents,
        consumer_external_id="support-agent",
        consumer_revision_external_id="support-agent@run-42",
        consumer_asset_kind="agent",
        binding_snapshot_external_id=SNAPSHOT_V2,
        sequence=42,
    )

    assert len(materialization.observations) == 3

    index_event = materialization.observations[-1]

    assert index_event.asset_external_id == "support-index"
    assert {
        (
            dependency.asset_external_id,
            dependency.revision_external_id,
            dependency.dependency_type,
        )
        for dependency in index_event.dependencies
    } == {
        (
            provenance[0].asset_external_id,
            provenance[0].revision_external_id,
            "indexed_from",
        ),
        (
            provenance[1].asset_external_id,
            provenance[1].revision_external_id,
            "indexed_from",
        ),
    }

    assert consumption.observation.asset_external_id == "support-agent"
    assert consumption.observation.asset_kind == "agent"
    assert consumption.observation.revision_external_id == "support-agent@run-42"
    assert len(consumption.observation.dependencies) == 1

    dependency = consumption.observation.dependencies[0]

    assert dependency.asset_external_id == provenance[0].asset_external_id
    assert (
        dependency.revision_external_id
        == provenance[0].revision_external_id
    )
    assert dependency.dependency_type == "consumed"