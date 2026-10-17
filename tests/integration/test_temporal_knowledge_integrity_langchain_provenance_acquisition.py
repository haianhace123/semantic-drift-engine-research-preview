from __future__ import annotations

import pytest

from benchmarks.temporal_knowledge_integrity.langchain_lineage_adapter import (
    acquire_langchain_chunk_provenance,
)


pytest.importorskip("langchain_core.documents")
pytest.importorskip("langchain_text_splitters")

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter


def _split(
    *,
    snapshot_id: str,
    text: str,
) -> list[Document]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=40,
        chunk_overlap=0,
        add_start_index=True,
    )

    return splitter.split_documents(
        [
            Document(
                id="policy-document",
                page_content=text,
                metadata={
                    "source_external_id": "policy-source",
                    "snapshot_external_id": snapshot_id,
                },
            )
        ]
    )


def _with_provenance(
    documents: list[Document],
    *,
    asset_ids: tuple[str, ...],
) -> list[Document]:
    assert len(documents) == len(asset_ids)

    return [
        Document(
            id=asset_id,
            page_content=document.page_content,
            metadata=document.metadata,
        )
        for document, asset_id in zip(
            documents,
            asset_ids,
            strict=True,
        )
    ]


def test_recursive_splitter_preserves_source_provenance_but_loses_parent_id() -> None:
    chunks = _split(
        snapshot_id="snapshot-v1",
        text=(
            "Alpha policy section. Refunds require approval. "
            "Escalations require review."
        ),
    )

    assert chunks
    assert all(chunk.id is None for chunk in chunks)
    assert all(
        chunk.metadata["source_external_id"] == "policy-source"
        for chunk in chunks
    )
    assert all(
        chunk.metadata["snapshot_external_id"] == "snapshot-v1"
        for chunk in chunks
    )
    assert all(
        isinstance(chunk.metadata["start_index"], int)
        for chunk in chunks
    )


def test_exact_unchanged_chunk_identity_is_adapter_recoverable() -> None:
    previous = _with_provenance(
        _split(
            snapshot_id="snapshot-v1",
            text=(
                "Alpha policy section. Refunds require approval. "
                "Escalations require review."
            ),
        ),
        asset_ids=(
            "policy-source:chunk:1",
            "policy-source:chunk:2",
        ),
    )

    current = _split(
        snapshot_id="snapshot-v2",
        text=(
            "Alpha policy section. Refunds require approval. "
            "Escalations require review."
        ),
    )

    acquired = acquire_langchain_chunk_provenance(
        documents=current,
        previous_documents=previous,
    )

    assert [item.asset_external_id for item in acquired] == [
        "policy-source:chunk:1",
        "policy-source:chunk:2",
    ]
    assert all(
        item.identity_source == "exact_content"
        for item in acquired
    )


def test_new_chunk_can_receive_new_adapter_identity() -> None:
    chunks = _split(
        snapshot_id="snapshot-v1",
        text="A newly observed policy statement.",
    )

    acquired = acquire_langchain_chunk_provenance(
        documents=chunks,
    )

    assert len(acquired) == 1
    assert acquired[0].identity_source == "new"
    assert acquired[0].asset_external_id == (
        "policy-source:snapshot-v1:chunk:new:0"
    )
    assert acquired[0].revision_external_id.startswith(
        "policy-source:snapshot-v1:chunk:new:0:"
    )


def test_mutated_chunk_without_upstream_identity_is_not_claimed_as_same_asset() -> None:
    previous = _with_provenance(
        _split(
            snapshot_id="snapshot-v1",
            text="Refunds require approval.",
        ),
        asset_ids=("policy-source:refund-rule",),
    )

    current = _split(
        snapshot_id="snapshot-v2",
        text="Refunds require manager approval.",
    )

    acquired = acquire_langchain_chunk_provenance(
        documents=current,
        previous_documents=previous,
    )

    assert acquired[0].identity_source == "new"
    assert (
        acquired[0].asset_external_id
        != previous[0].id
    )
    assert acquired[0].asset_external_id == (
        "policy-source:snapshot-v2:chunk:new:0"
    )


def test_upstream_identity_preserves_logical_asset_across_mutation() -> None:
    previous = Document(
        id="policy-source:refund-rule",
        page_content="Refunds require approval.",
        metadata={
            "source_external_id": "policy-source",
            "snapshot_external_id": "snapshot-v1",
        },
    )
    current = Document(
        id="policy-source:refund-rule",
        page_content="Refunds require manager approval.",
        metadata={
            "source_external_id": "policy-source",
            "snapshot_external_id": "snapshot-v2",
        },
    )

    previous_acquired = acquire_langchain_chunk_provenance(
        documents=[previous],
    )
    current_acquired = acquire_langchain_chunk_provenance(
        documents=[current],
        previous_documents=[previous],
    )

    assert (
        previous_acquired[0].asset_external_id
        == current_acquired[0].asset_external_id
    )
    assert (
        previous_acquired[0].revision_external_id
        != current_acquired[0].revision_external_id
    )
    assert current_acquired[0].identity_source == "upstream"