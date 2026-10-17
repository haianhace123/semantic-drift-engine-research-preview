from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from src.domain.lineage_observation import LineageObservation
from src.integrations.langchain import (
    acquire_langchain_chunk_provenance,
    capture_langchain_consumer_binding,
    capture_langchain_context_revisions,
    capture_langchain_index_materialization,
    prepare_langchain_documents_for_indexing,
)


@dataclass
class FakeDocument:
    page_content: str
    metadata: dict[str, Any]
    id: str | None = None


def _document(
    *,
    document_id: str | None,
    content: str = "chunk content",
    source_external_id: str = "source-1",
    snapshot_external_id: str = "snapshot-1",
    revision_external_id: str | None = "chunk-1@v1",
) -> FakeDocument:
    metadata: dict[str, Any] = {
        "source_external_id": source_external_id,
        "snapshot_external_id": snapshot_external_id,
    }

    if revision_external_id is not None:
        metadata["chunk_revision_external_id"] = (
            revision_external_id
        )

    return FakeDocument(
        id=document_id,
        page_content=content,
        metadata=metadata,
    )


def test_acquire_chunk_provenance_preserves_upstream_identity_across_change():
    previous = _document(
        document_id="chunk-1",
        content="old content",
    )
    current = _document(
        document_id="chunk-1",
        content="new content",
        snapshot_external_id="snapshot-2",
    )

    previous_provenance = acquire_langchain_chunk_provenance(
        documents=[previous],
    )[0]
    current_provenance = acquire_langchain_chunk_provenance(
        documents=[current],
        previous_documents=[previous],
    )[0]

    assert previous_provenance.asset_external_id == "chunk-1"
    assert current_provenance.asset_external_id == "chunk-1"
    assert current_provenance.identity_source == "upstream"
    assert (
        current_provenance.revision_external_id
        != previous_provenance.revision_external_id
    )


def test_acquire_chunk_provenance_only_recovers_missing_identity_by_exact_content():
    previous = _document(
        document_id="chunk-1",
        content="unchanged content",
    )
    unchanged = _document(
        document_id=None,
        content="unchanged content",
        snapshot_external_id="snapshot-2",
    )
    changed = _document(
        document_id=None,
        content="changed content",
        snapshot_external_id="snapshot-2",
    )

    unchanged_provenance = acquire_langchain_chunk_provenance(
        documents=[unchanged],
        previous_documents=[previous],
    )[0]
    changed_provenance = acquire_langchain_chunk_provenance(
        documents=[changed],
        previous_documents=[previous],
    )[0]

    assert unchanged_provenance.asset_external_id == "chunk-1"
    assert unchanged_provenance.identity_source == "exact_content"
    assert changed_provenance.asset_external_id != "chunk-1"
    assert changed_provenance.identity_source == "new"


def test_prepare_documents_for_indexing_attaches_exact_identity_in_place():
    previous = _document(
        document_id="chunk-1",
        content="unchanged content",
        snapshot_external_id="snapshot-1",
    )
    current = _document(
        document_id=None,
        content="unchanged content",
        snapshot_external_id="snapshot-2",
        revision_external_id="stale-revision",
    )

    prepared = prepare_langchain_documents_for_indexing(
        documents=[current],
        previous_documents=[previous],
    )

    assert prepared.documents == (current,)
    assert prepared.documents[0] is current
    assert prepared.provenance[0].identity_source == "exact_content"
    assert current.id == "chunk-1"
    assert current.metadata["snapshot_external_id"] == "snapshot-2"
    assert current.metadata["chunk_revision_external_id"] == (
        prepared.provenance[0].revision_external_id
    )
    assert current.metadata["chunk_revision_external_id"] != "stale-revision"


def test_prepare_documents_for_indexing_assigns_new_identity_when_needed():
    document = _document(
        document_id=None,
        content="new content",
        snapshot_external_id="snapshot-2",
        revision_external_id=None,
    )

    prepared = prepare_langchain_documents_for_indexing(
        documents=[document],
    )

    assert prepared.provenance[0].identity_source == "new"
    assert document.id == "source-1:snapshot-2:chunk:new:0"
    assert document.metadata["chunk_revision_external_id"] == (
        prepared.provenance[0].revision_external_id
    )


def test_prepare_documents_for_indexing_preserves_upstream_identity():
    document = _document(
        document_id="chunk-1",
        content="changed content",
        snapshot_external_id="snapshot-2",
        revision_external_id="old-revision",
    )

    prepared = prepare_langchain_documents_for_indexing(
        documents=[document],
    )

    assert prepared.provenance[0].identity_source == "upstream"
    assert document.id == "chunk-1"
    assert document.metadata["chunk_revision_external_id"] == (
        prepared.provenance[0].revision_external_id
    )


@dataclass(frozen=True)
class ImmutableDocument:
    page_content: str
    metadata: dict[str, Any]
    id: str | None = None


def test_prepare_documents_for_indexing_fails_closed_for_immutable_document():
    document = ImmutableDocument(
        id="chunk-1",
        page_content="content",
        metadata={
            "source_external_id": "source-1",
            "snapshot_external_id": "snapshot-1",
        },
    )

    with pytest.raises(
        ValueError,
        match="identity must be writable",
    ):
        prepare_langchain_documents_for_indexing(
            documents=[document],
        )

    assert "chunk_revision_external_id" not in document.metadata


def test_prepare_documents_for_indexing_rolls_back_prior_document_on_failure():
    writable = _document(
        document_id="chunk-1",
        revision_external_id="original-revision",
    )
    original_metadata = writable.metadata
    immutable = ImmutableDocument(
        id="chunk-2",
        page_content="second content",
        metadata={
            "source_external_id": "source-1",
            "snapshot_external_id": "snapshot-1",
        },
    )

    with pytest.raises(ValueError):
        prepare_langchain_documents_for_indexing(
            documents=[writable, immutable],
        )

    assert writable.id == "chunk-1"
    assert writable.metadata is original_metadata
    assert writable.metadata["chunk_revision_external_id"] == (
        "original-revision"
    )


def test_capture_index_materialization_returns_exact_domain_observations():
    first = _document(
        document_id="chunk-1",
        revision_external_id="chunk-1@v1",
    )
    second = _document(
        document_id="chunk-2",
        content="second chunk",
        revision_external_id="chunk-2@v4",
    )

    result = capture_langchain_index_materialization(
        documents=[first, second],
        index_external_id="index-1",
        index_revision_external_id="index-1@v7",
        sequence=7,
    )

    assert len(result.observations) == 3
    assert all(
        isinstance(observation, LineageObservation)
        for observation in result.observations
    )

    first_observation, second_observation, index_observation = (
        result.observations
    )

    assert first_observation.observation_kind == "materialization"
    assert first_observation.asset_external_id == "chunk-1"
    assert first_observation.revision_external_id == "chunk-1@v1"

    assert second_observation.asset_external_id == "chunk-2"
    assert second_observation.revision_external_id == "chunk-2@v4"

    assert index_observation.observation_kind == "materialization"
    assert index_observation.asset_kind == "index"
    assert index_observation.asset_external_id == "index-1"
    assert index_observation.revision_external_id == "index-1@v7"
    assert [
        (
            dependency.asset_external_id,
            dependency.revision_external_id,
            dependency.dependency_type,
        )
        for dependency in index_observation.dependencies
    ] == [
        ("chunk-1", "chunk-1@v1", "indexed_from"),
        ("chunk-2", "chunk-2@v4", "indexed_from"),
    ]


def test_capture_consumer_binding_preserves_exact_retrieved_revisions():
    historical = _document(
        document_id="chunk-1",
        snapshot_external_id="snapshot-1",
        revision_external_id="chunk-1@v1",
    )
    current = _document(
        document_id="chunk-1",
        content="newer content",
        snapshot_external_id="snapshot-2",
        revision_external_id="chunk-1@v2",
    )

    result = capture_langchain_consumer_binding(
        retrieved_documents=[historical, current],
        consumer_external_id="agent-1",
        consumer_revision_external_id="agent-1@run-9",
        consumer_asset_kind="agent",
        binding_snapshot_external_id="snapshot-2",
        sequence=9,
    )

    observation = result.observation

    assert isinstance(observation, LineageObservation)
    assert observation.observation_kind == "consumption"
    assert observation.asset_kind == "agent"
    assert [
        (
            dependency.asset_external_id,
            dependency.revision_external_id,
            dependency.dependency_type,
        )
        for dependency in observation.dependencies
    ] == [
        ("chunk-1", "chunk-1@v1", "consumed"),
        ("chunk-1", "chunk-1@v2", "consumed"),
    ]


@pytest.mark.parametrize(
    ("capture", "expected_message"),
    [
        (
            lambda: capture_langchain_index_materialization(
                documents=[
                    _document(
                        document_id="chunk-1",
                        revision_external_id=None,
                    )
                ],
                index_external_id="index-1",
                index_revision_external_id="index-1@v1",
                sequence=1,
            ),
            "chunk_revision_external_id",
        ),
        (
            lambda: capture_langchain_consumer_binding(
                retrieved_documents=[
                    _document(
                        document_id="chunk-1",
                        revision_external_id=None,
                    )
                ],
                consumer_external_id="workflow-1",
                consumer_revision_external_id="workflow-1@v1",
                consumer_asset_kind="workflow",
                binding_snapshot_external_id="snapshot-1",
                sequence=1,
            ),
            "chunk_revision_external_id",
        ),
    ],
)
def test_capture_fails_closed_without_exact_chunk_revision(
    capture,
    expected_message,
):
    with pytest.raises(ValueError, match=expected_message):
        capture()

def test_capture_context_revisions_preserves_order_deduplicates_and_supports_multiple_sources():
    first = _document(
        document_id="chunk-1",
        revision_external_id="chunk-1@v3",
    )
    duplicate = _document(
        document_id="chunk-1",
        revision_external_id="chunk-1@v3",
    )
    second_source = _document(
        document_id="chunk-9",
        revision_external_id="chunk-9@v2",
    )
    second_source.metadata["source_external_id"] = "source-b"

    revisions = capture_langchain_context_revisions(
        retrieved_documents=[first, duplicate, second_source],
    )

    assert [
        (
            revision.source_external_id,
            revision.asset_external_id,
            revision.revision_external_id,
        )
        for revision in revisions
    ] == [
        ("source-1", "chunk-1", "chunk-1@v3"),
        ("source-b", "chunk-9", "chunk-9@v2"),
    ]


@pytest.mark.parametrize(
    "document",
    [
        _document(
            document_id=None,
            revision_external_id="chunk-1@v1",
        ),
        _document(
            document_id="chunk-1",
            revision_external_id=None,
        ),
    ],
)
def test_capture_context_revisions_fails_closed_without_exact_identity(document):
    with pytest.raises(ValueError):
        capture_langchain_context_revisions(
            retrieved_documents=[document],
        )


def test_capture_context_revisions_rejects_empty_retrieval():
    with pytest.raises(
        ValueError,
        match="at least one retrieved document",
    ):
        capture_langchain_context_revisions(
            retrieved_documents=[],
        )
