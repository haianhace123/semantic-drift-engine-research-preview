from __future__ import annotations

import os
from uuid import uuid4

import pytest

from benchmarks.temporal_knowledge_integrity.langchain_lineage_adapter import (
    capture_langchain_consumer_binding,
    capture_langchain_index_materialization,
)
from benchmarks.temporal_knowledge_integrity.materialization_event import (
    build_lineage_from_projection,
    project_materialization_events,
)


if os.getenv("RUN_DB_INTEGRATION_TESTS") != "1":
    pytest.skip(
        "Database integration tests require RUN_DB_INTEGRATION_TESTS=1",
        allow_module_level=True,
    )


pytest.importorskip("langchain_core.documents")
pytest.importorskip("langchain_core.embeddings")
pytest.importorskip("langchain_postgres")


from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_postgres import PGVector

from src.core.config import settings


class DeterministicEmbeddings(Embeddings):
    dimensions = 8

    def embed_documents(
        self,
        texts: list[str],
    ) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(
        self,
        text: str,
    ) -> list[float]:
        return self._embed(text)

    @classmethod
    def _embed(
        cls,
        text: str,
    ) -> list[float]:
        vector = [0.0] * cls.dimensions

        for index, byte in enumerate(text.encode("utf-8")):
            vector[index % cls.dimensions] += float(byte)

        magnitude = sum(
            value * value
            for value in vector
        ) ** 0.5

        if magnitude == 0.0:
            return vector

        return [
            value / magnitude
            for value in vector
        ]


def _database_url() -> str:
    database_url = str(settings.DATABASE_URL)

    if database_url.startswith("postgresql+psycopg2://"):
        return database_url.replace(
            "postgresql+psycopg2://",
            "postgresql+psycopg://",
            1,
        )

    if database_url.startswith("postgresql://"):
        return database_url.replace(
            "postgresql://",
            "postgresql+psycopg://",
            1,
        )

    return database_url


@pytest.fixture
def vector_store() -> PGVector:
    store = PGVector(
        embeddings=DeterministicEmbeddings(),
        connection=_database_url(),
        embedding_length=DeterministicEmbeddings.dimensions,
        collection_name=f"sde_consumer_binding_{uuid4().hex}",
        use_jsonb=True,
        create_extension=False,
    )

    try:
        yield store
    finally:
        store.delete_collection()


def _document(
    *,
    storage_id: str,
    chunk_id: str,
    chunk_revision: str | None,
    snapshot_id: str,
    text: str,
) -> Document:
    metadata = {
        "source_external_id": "policy-source",
        "snapshot_external_id": snapshot_id,
        "chunk_external_id": chunk_id,
    }

    if chunk_revision is not None:
        metadata["chunk_revision_external_id"] = chunk_revision

    return Document(
        id=storage_id,
        page_content=text,
        metadata=metadata,
    )


def _retrieved_as_logical_documents(
    documents: list[Document],
) -> list[Document]:
    return [
        Document(
            id=document.metadata["chunk_external_id"],
            page_content=document.page_content,
            metadata=document.metadata,
        )
        for document in documents
    ]


def test_retrieved_revision_binds_consumer_to_exact_revision(
    vector_store: PGVector,
) -> None:
    vector_store.add_documents(
        [
            _document(
                storage_id="chunk-policy-v1",
                chunk_id="chunk-policy",
                chunk_revision="chunk-policy:revision-v1",
                snapshot_id="snapshot-v1",
                text="Refunds require approval.",
            )
        ]
    )

    retrieved = vector_store.similarity_search(
        "Refunds require approval.",
        k=1,
    )

    binding = capture_langchain_consumer_binding(
        retrieved_documents=_retrieved_as_logical_documents(
            retrieved,
        ),
        consumer_external_id="support-agent",
        consumer_revision_external_id="support-agent:run-1",
        consumer_asset_kind="agent",
        sequence=1,
        binding_snapshot_external_id="snapshot-v1",
    )

    assert binding.event.asset_kind == "agent"
    assert len(binding.event.dependencies) == 1

    dependency = binding.event.dependencies[0]

    assert dependency.asset_external_id == "chunk-policy"
    assert (
        dependency.revision_external_id
        == "chunk-policy:revision-v1"
    )
    assert dependency.dependency_type == "consumed"


def test_historical_retrieval_binds_historical_revision_after_newer_exists(
    vector_store: PGVector,
) -> None:
    vector_store.add_documents(
        [
            _document(
                storage_id="chunk-policy-v1",
                chunk_id="chunk-policy",
                chunk_revision="chunk-policy:revision-v1",
                snapshot_id="snapshot-v1",
                text="Refunds require approval.",
            ),
            _document(
                storage_id="chunk-policy-v2",
                chunk_id="chunk-policy",
                chunk_revision="chunk-policy:revision-v2",
                snapshot_id="snapshot-v2",
                text="Refunds require manager approval.",
            ),
        ]
    )

    retrieved_v1 = vector_store.similarity_search(
        "Refunds require approval.",
        k=1,
        filter={
            "chunk_revision_external_id": "chunk-policy:revision-v1",
        },
    )

    binding = capture_langchain_consumer_binding(
        retrieved_documents=_retrieved_as_logical_documents(
            retrieved_v1,
        ),
        consumer_external_id="support-agent",
        consumer_revision_external_id="support-agent:historical-run",
        consumer_asset_kind="agent",
        sequence=2,
        binding_snapshot_external_id="snapshot-v2",
    )
    assert binding.event.snapshot_external_id == "snapshot-v2"
    dependency = binding.event.dependencies[0]

    assert (
        dependency.revision_external_id
        == "chunk-policy:revision-v1"
    )
    assert (
        dependency.revision_external_id
        != "chunk-policy:revision-v2"
    )


def test_consumer_binding_accepts_exact_revisions_from_multiple_snapshots(
    vector_store: PGVector,
) -> None:
    vector_store.add_documents(
        [
            _document(
                storage_id="chunk-refunds-v1",
                chunk_id="chunk-refunds",
                chunk_revision="chunk-refunds:revision-v1",
                snapshot_id="snapshot-v1",
                text="Refunds require approval.",
            ),
            _document(
                storage_id="chunk-escalation-v3",
                chunk_id="chunk-escalation",
                chunk_revision="chunk-escalation:revision-v3",
                snapshot_id="snapshot-v3",
                text="Escalations require manager review.",
            ),
        ]
    )

    retrieved_v1 = vector_store.similarity_search(
        "Refunds require approval.",
        k=1,
        filter={
            "chunk_revision_external_id": (
                "chunk-refunds:revision-v1"
            ),
        },
    )
    retrieved_v3 = vector_store.similarity_search(
        "Escalations require manager review.",
        k=1,
        filter={
            "chunk_revision_external_id": (
                "chunk-escalation:revision-v3"
            ),
        },
    )

    binding = capture_langchain_consumer_binding(
        retrieved_documents=_retrieved_as_logical_documents(
            [*retrieved_v1, *retrieved_v3],
        ),
        consumer_external_id="support-agent",
        consumer_revision_external_id="support-agent:run-mixed",
        consumer_asset_kind="agent",
        binding_snapshot_external_id="snapshot-v4",
        sequence=4,
    )

    dependencies = {
        (
            dependency.asset_external_id,
            dependency.revision_external_id,
        )
        for dependency in binding.event.dependencies
    }

    assert dependencies == {
        (
            "chunk-refunds",
            "chunk-refunds:revision-v1",
        ),
        (
            "chunk-escalation",
            "chunk-escalation:revision-v3",
        ),
    }
    assert binding.event.snapshot_external_id == "snapshot-v4"


def test_mixed_snapshot_consumer_binding_reconstructs_exact_revision_lineage(
    vector_store: PGVector,
) -> None:
    revision_v1 = _document(
        storage_id="chunk-refunds-v1",
        chunk_id="chunk-refunds",
        chunk_revision="chunk-refunds:revision-v1",
        snapshot_id="snapshot-v1",
        text="Refunds require approval.",
    )
    revision_v3 = _document(
        storage_id="chunk-escalation-v3",
        chunk_id="chunk-escalation",
        chunk_revision="chunk-escalation:revision-v3",
        snapshot_id="snapshot-v3",
        text="Escalations require manager review.",
    )

    vector_store.add_documents(
        [
            revision_v1,
            revision_v3,
        ]
    )

    retrieved_v1 = vector_store.similarity_search(
        "Refunds require approval.",
        k=1,
        filter={
            "chunk_revision_external_id": (
                "chunk-refunds:revision-v1"
            ),
        },
    )
    retrieved_v3 = vector_store.similarity_search(
        "Escalations require manager review.",
        k=1,
        filter={
            "chunk_revision_external_id": (
                "chunk-escalation:revision-v3"
            ),
        },
    )

    logical_v1 = _retrieved_as_logical_documents(
        retrieved_v1,
    )
    logical_v3 = _retrieved_as_logical_documents(
        retrieved_v3,
    )

    index_v1 = capture_langchain_index_materialization(
        documents=logical_v1,
        index_external_id="support-index-v1",
        index_revision_external_id="support-index:revision-v1",
        sequence=1,
    )
    index_v3 = capture_langchain_index_materialization(
        documents=logical_v3,
        index_external_id="support-index-v3",
        index_revision_external_id="support-index:revision-v3",
        sequence=3,
    )

    binding = capture_langchain_consumer_binding(
        retrieved_documents=[
            *logical_v1,
            *logical_v3,
        ],
        consumer_external_id="support-agent",
        consumer_revision_external_id="support-agent:run-mixed",
        consumer_asset_kind="agent",
        binding_snapshot_external_id="snapshot-v4",
        sequence=4,
    )

    events = (
        *index_v1.events,
        *index_v3.events,
        binding.event,
    )

    projection = project_materialization_events(
        events,
        current_snapshot_external_id="snapshot-v4",
    )
    observation = build_lineage_from_projection(
        projection,
        content_hash="a" * 64,
    )

    consumer_dependencies = {
        (
            dependency.upstream_asset_external_id,
            dependency.upstream_revision_external_id,
            dependency.downstream_asset_external_id,
            dependency.downstream_revision_external_id,
            dependency.dependency_type,
        )
        for dependency in observation.revision_dependencies
        if dependency.downstream_asset_external_id
        == "support-agent"
    }

    assert consumer_dependencies == {
        (
            "chunk-refunds",
            "chunk-refunds:revision-v1",
            "support-agent",
            "support-agent:run-mixed",
            "consumed",
        ),
        (
            "chunk-escalation",
            "chunk-escalation:revision-v3",
            "support-agent",
            "support-agent:run-mixed",
            "consumed",
        ),
    }

def test_consumer_binding_preserves_multiple_revisions_of_same_asset() -> None:
    documents = [
        Document(
            id="chunk-policy",
            page_content="Refunds require approval.",
            metadata={
                "source_external_id": "policy-source",
                "snapshot_external_id": "snapshot-v1",
                "chunk_revision_external_id": "chunk-policy:revision-v1",
            },
        ),
        Document(
            id="chunk-policy",
            page_content="Refunds require manager approval.",
            metadata={
                "source_external_id": "policy-source",
                "snapshot_external_id": "snapshot-v2",
                "chunk_revision_external_id": "chunk-policy:revision-v2",
            },
        ),
    ]

    binding = capture_langchain_consumer_binding(
        retrieved_documents=documents,
        consumer_external_id="support-agent",
        consumer_revision_external_id="support-agent:run-mixed-revisions",
        consumer_asset_kind="agent",
        binding_snapshot_external_id="snapshot-v2",
        sequence=2,
    )

    dependencies = {
        (
            dependency.asset_external_id,
            dependency.revision_external_id,
        )
        for dependency in binding.event.dependencies
    }

    assert dependencies == {
        (
            "chunk-policy",
            "chunk-policy:revision-v1",
        ),
        (
            "chunk-policy",
            "chunk-policy:revision-v2",
        ),
    }


def test_consumer_binding_rejects_non_consumer_asset_kind() -> None:
    documents = [
        Document(
            id="chunk-policy",
            page_content="Refunds require approval.",
            metadata={
                "source_external_id": "policy-source",
                "snapshot_external_id": "snapshot-v1",
                "chunk_revision_external_id": "chunk-policy:revision-v1",
            },
        ),
    ]

    with pytest.raises(
        ValueError,
        match="Consumer asset kind",
    ):
        capture_langchain_consumer_binding(
            retrieved_documents=documents,
            consumer_external_id="support-index",
            consumer_revision_external_id="support-index:revision-v1",
            consumer_asset_kind="index",
            binding_snapshot_external_id="snapshot-v1",
            sequence=1,
        )


def test_missing_retrieval_revision_provenance_fails_closed(
    vector_store: PGVector,
) -> None:
    vector_store.add_documents(
        [
            _document(
                storage_id="chunk-without-revision",
                chunk_id="chunk-policy",
                chunk_revision=None,
                snapshot_id="snapshot-v1",
                text="Refunds require approval.",
            )
        ]
    )

    retrieved = vector_store.similarity_search(
        "Refunds require approval.",
        k=1,
    )

    assert len(retrieved) == 1
    assert (
        "chunk_revision_external_id"
        not in retrieved[0].metadata
    )

    with pytest.raises(
        ValueError,
        match="chunk_revision_external_id",
    ):
        capture_langchain_consumer_binding(
            retrieved_documents=_retrieved_as_logical_documents(
                retrieved,
            ),
            consumer_external_id="support-agent",
            consumer_revision_external_id="support-agent:run-1",
            consumer_asset_kind="agent",
            sequence=1,
            binding_snapshot_external_id="snapshot-v1",
        )


def test_consumer_binding_rejects_multiple_sources() -> None:
    documents = [
        Document(
            id="chunk-a",
            page_content="Alpha",
            metadata={
                "source_external_id": "source-a",
                "snapshot_external_id": "snapshot-a",
                "chunk_revision_external_id": "chunk-a:revision-v1",
            },
        ),
        Document(
            id="chunk-b",
            page_content="Beta",
            metadata={
                "source_external_id": "source-b",
                "snapshot_external_id": "snapshot-b",
                "chunk_revision_external_id": "chunk-b:revision-v1",
            },
        ),
    ]

    with pytest.raises(
        ValueError,
        match="multiple source identities",
    ):
        capture_langchain_consumer_binding(
            retrieved_documents=documents,
            consumer_external_id="support-agent",
            consumer_revision_external_id="support-agent:run-1",
            consumer_asset_kind="agent",
            binding_snapshot_external_id="snapshot-current",
            sequence=1,
        )
