from __future__ import annotations

import pytest

from benchmarks.temporal_knowledge_integrity.langchain_lineage_adapter import (
    capture_langchain_index_materialization,
)
from benchmarks.temporal_knowledge_integrity.lineage_adapter import (
    adapt_snapshot_lineage,
)
from benchmarks.temporal_knowledge_integrity.materialization_event import (
    build_lineage_from_projection,
    project_materialization_events,
)


pytest.importorskip("langchain_core.documents")

from langchain_core.documents import Document


CONTENT_HASH = "a" * 64


def _document(
    *,
    chunk_id: str | None = "chunk-policy",
    chunk_revision: str | None = "chunk-policy:revision-v1",
    snapshot_id: str | None = "snapshot-v1",
    source_id: str | None = "policy-source",
    text: str = "Refunds require approval.",
) -> Document:
    metadata: dict[str, str] = {}

    if source_id is not None:
        metadata["source_external_id"] = source_id

    if snapshot_id is not None:
        metadata["snapshot_external_id"] = snapshot_id

    if chunk_revision is not None:
        metadata["chunk_revision_external_id"] = chunk_revision

    return Document(
        id=chunk_id,
        page_content=text,
        metadata=metadata,
    )


def test_langchain_document_preserves_identity_and_revision_metadata() -> None:
    document = _document()

    serialized = document.model_dump()

    assert serialized["id"] == "chunk-policy"
    assert serialized["metadata"] == {
        "source_external_id": "policy-source",
        "snapshot_external_id": "snapshot-v1",
        "chunk_revision_external_id": "chunk-policy:revision-v1",
    }


def test_capture_requires_stable_chunk_identity() -> None:
    with pytest.raises(
        ValueError,
        match="stable logical chunk identity",
    ):
        capture_langchain_index_materialization(
            documents=[_document(chunk_id=None)],
            index_external_id="support-index",
            index_revision_external_id="support-index:revision-v1",
            sequence=1,
        )


def test_capture_requires_exact_chunk_revision() -> None:
    with pytest.raises(
        ValueError,
        match="chunk_revision_external_id",
    ):
        capture_langchain_index_materialization(
            documents=[_document(chunk_revision=None)],
            index_external_id="support-index",
            index_revision_external_id="support-index:revision-v1",
            sequence=1,
        )


def test_capture_rejects_mixed_snapshot_materialization() -> None:
    with pytest.raises(
        ValueError,
        match="multiple snapshot revisions",
    ):
        capture_langchain_index_materialization(
            documents=[
                _document(
                    chunk_id="chunk-a",
                    chunk_revision="chunk-a:revision-v1",
                    snapshot_id="snapshot-v1",
                ),
                _document(
                    chunk_id="chunk-b",
                    chunk_revision="chunk-b:revision-v2",
                    snapshot_id="snapshot-v2",
                ),
            ],
            index_external_id="support-index",
            index_revision_external_id="support-index:revision-v1",
            sequence=1,
        )


def test_capture_rejects_duplicate_chunk_identity() -> None:
    with pytest.raises(
        ValueError,
        match="duplicate chunk identity",
    ):
        capture_langchain_index_materialization(
            documents=[
                _document(
                    chunk_id="chunk-policy",
                    chunk_revision="chunk-policy:revision-v1",
                ),
                _document(
                    chunk_id="chunk-policy",
                    chunk_revision="chunk-policy:revision-v2",
                ),
            ],
            index_external_id="support-index",
            index_revision_external_id="support-index:revision-v1",
            sequence=1,
        )


def test_langchain_documents_reconstruct_exact_revision_lineage() -> None:
    captured = capture_langchain_index_materialization(
        documents=[
            _document(
                chunk_id="chunk-refunds",
                chunk_revision="chunk-refunds:revision-v7",
                text="Refunds require approval.",
            ),
            _document(
                chunk_id="chunk-escalation",
                chunk_revision="chunk-escalation:revision-v3",
                text="Escalations require manager review.",
            ),
        ],
        index_external_id="support-index",
        index_revision_external_id="support-index:revision-v11",
        sequence=11,
    )

    projection = project_materialization_events(captured.events)

    observation = build_lineage_from_projection(
        projection,
        content_hash=CONTENT_HASH,
    )

    lineage = adapt_snapshot_lineage(observation)

    assert lineage.source_node_id == "source:policy-source"
    assert lineage.snapshot_node_id == "snapshot:snapshot-v1"

    revision_dependencies = {
        (
            dependency.upstream_asset_node_id,
            dependency.upstream_revision_external_id,
            dependency.downstream_asset_node_id,
            dependency.downstream_revision_external_id,
            dependency.dependency_type,
        )
        for dependency in lineage.revision_dependencies
    }

    assert revision_dependencies == {
        (
            "chunk:chunk-escalation",
            "chunk-escalation:revision-v3",
            "index:support-index",
            "support-index:revision-v11",
            "indexed_from",
        ),
        (
            "chunk:chunk-refunds",
            "chunk-refunds:revision-v7",
            "index:support-index",
            "support-index:revision-v11",
            "indexed_from",
        ),
    }