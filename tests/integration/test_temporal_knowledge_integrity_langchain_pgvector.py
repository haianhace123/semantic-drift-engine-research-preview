from __future__ import annotations

import os
from uuid import uuid4

import pytest

from benchmarks.temporal_knowledge_integrity.langchain_lineage_adapter import (
    capture_langchain_index_materialization,
)
from src.integrations.langchain import (
    prepare_langchain_documents_for_indexing,
)


# Isolated PostgreSQL/PGVector acceptance. No global Redis-cleanup fixture.


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

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    @classmethod
    def _embed(cls, text: str) -> list[float]:
        vector = [0.0] * cls.dimensions

        for index, byte in enumerate(text.encode("utf-8")):
            vector[index % cls.dimensions] += float(byte)

        magnitude = sum(value * value for value in vector) ** 0.5

        if magnitude == 0.0:
            return vector

        return [value / magnitude for value in vector]


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


def _document(
    *,
    chunk_id: str = "chunk-policy",
    chunk_revision: str | None = "chunk-policy:revision-v1",
    snapshot_id: str = "snapshot-v1",
    source_id: str = "policy-source",
    text: str = "Refunds require approval.",
) -> Document:
    metadata = {
        "source_external_id": source_id,
        "snapshot_external_id": snapshot_id,
    }

    if chunk_revision is not None:
        metadata["chunk_revision_external_id"] = chunk_revision

    return Document(
        id=chunk_id,
        page_content=text,
        metadata=metadata,
    )


@pytest.fixture
def vector_store() -> PGVector:
    collection_name = f"sde_lineage_{uuid4().hex}"

    store = PGVector(
        embeddings=DeterministicEmbeddings(),
        connection=_database_url(),
        embedding_length=DeterministicEmbeddings.dimensions,
        collection_name=collection_name,
        use_jsonb=True,
        create_extension=False,
    )

    try:
        yield store
    finally:
        store.delete_collection()


def test_index_preparation_closes_provenance_write_boundary(
    vector_store: PGVector,
) -> None:
    document = _document(
        chunk_id="chunk-policy",
        chunk_revision="stale-revision",
        snapshot_id="snapshot-v2",
        text="Refunds require manager approval.",
    )

    prepared = prepare_langchain_documents_for_indexing(
        documents=[document],
    )

    expected_revision = prepared.provenance[0].revision_external_id
    assert expected_revision != "stale-revision"

    inserted_ids = vector_store.add_documents(
        list(prepared.documents)
    )

    assert inserted_ids == ["chunk-policy"]

    recovered = vector_store.similarity_search(
        "Refunds require manager approval.",
        k=1,
    )

    assert recovered[0].id == "chunk-policy"
    assert recovered[0].metadata[
        "chunk_revision_external_id"
    ] == expected_revision

    captured = capture_langchain_index_materialization(
        documents=recovered,
        index_external_id="support-index",
        index_revision_external_id="support-index:revision-v2",
        sequence=2,
    )

    assert captured.events[0].revision_external_id == expected_revision
    assert (
        captured.events[-1].dependencies[0].revision_external_id
        == expected_revision
    )


def test_pgvector_round_trip_preserves_exact_provenance(
    vector_store: PGVector,
) -> None:
    document = _document()

    inserted_ids = vector_store.add_documents([document])

    assert inserted_ids == ["chunk-policy"]

    recovered = vector_store.similarity_search(
        "Refunds require approval.",
        k=1,
    )

    assert len(recovered) == 1
    assert recovered[0].id == "chunk-policy"
    assert recovered[0].page_content == "Refunds require approval."
    assert recovered[0].metadata == {
        "source_external_id": "policy-source",
        "snapshot_external_id": "snapshot-v1",
        "chunk_revision_external_id": "chunk-policy:revision-v1",
    }

    captured = capture_langchain_index_materialization(
        documents=recovered,
        index_external_id="support-index",
        index_revision_external_id="support-index:revision-v1",
        sequence=1,
    )

    assert captured.events[0].asset_external_id == "chunk-policy"
    assert (
        captured.events[0].revision_external_id
        == "chunk-policy:revision-v1"
    )
    assert captured.events[-1].dependencies[0].revision_external_id == (
        "chunk-policy:revision-v1"
    )


def test_pgvector_storage_identity_is_not_causal_revision(
    vector_store: PGVector,
) -> None:
    document = _document(
        chunk_id="chunk-policy",
        chunk_revision="causal-revision-v7",
    )

    inserted_ids = vector_store.add_documents([document])

    assert inserted_ids == ["chunk-policy"]

    recovered = vector_store.similarity_search(
        "Refunds require approval.",
        k=1,
    )

    assert recovered[0].id == "chunk-policy"
    assert (
        recovered[0].metadata["chunk_revision_external_id"]
        == "causal-revision-v7"
    )
    assert (
        recovered[0].id
        != recovered[0].metadata["chunk_revision_external_id"]
    )


def test_pgvector_accepts_missing_revision_but_sde_fails_closed(
    vector_store: PGVector,
) -> None:
    document = _document(
        chunk_id="chunk-without-provenance",
        chunk_revision=None,
    )

    inserted_ids = vector_store.add_documents([document])

    assert inserted_ids == ["chunk-without-provenance"]

    recovered = vector_store.similarity_search(
        "Refunds require approval.",
        k=1,
    )

    assert len(recovered) == 1
    assert recovered[0].id == "chunk-without-provenance"
    assert "chunk_revision_external_id" not in recovered[0].metadata

    with pytest.raises(
        ValueError,
        match="chunk_revision_external_id",
    ):
        capture_langchain_index_materialization(
            documents=recovered,
            index_external_id="support-index",
            index_revision_external_id="support-index:revision-v1",
            sequence=1,
        )


def test_pgvector_preserves_historical_chunk_revisions(
    vector_store: PGVector,
) -> None:
    revision_v1 = _document(
        chunk_id="chunk-policy-v1",
        chunk_revision="chunk-policy:revision-v1",
        snapshot_id="snapshot-v1",
        text="Refunds require approval.",
    )
    revision_v2 = _document(
        chunk_id="chunk-policy-v2",
        chunk_revision="chunk-policy:revision-v2",
        snapshot_id="snapshot-v2",
        text="Refunds require manager approval.",
    )

    vector_store.add_documents([revision_v1])
    vector_store.add_documents([revision_v2])

    recovered_v1 = vector_store.similarity_search(
        "Refunds require approval.",
        k=2,
        filter={
            "chunk_revision_external_id": "chunk-policy:revision-v1",
        },
    )
    recovered_v2 = vector_store.similarity_search(
        "Refunds require manager approval.",
        k=2,
        filter={
            "chunk_revision_external_id": "chunk-policy:revision-v2",
        },
    )

    assert len(recovered_v1) == 1
    assert len(recovered_v2) == 1

    assert (
        recovered_v1[0].metadata["chunk_revision_external_id"]
        == "chunk-policy:revision-v1"
    )
    assert recovered_v1[0].metadata["snapshot_external_id"] == "snapshot-v1"

    assert (
        recovered_v2[0].metadata["chunk_revision_external_id"]
        == "chunk-policy:revision-v2"
    )
    assert recovered_v2[0].metadata["snapshot_external_id"] == "snapshot-v2"