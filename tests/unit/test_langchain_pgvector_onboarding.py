from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

import pytest

import src.integrations.langchain.onboarding as onboarding


@dataclass
class Document:
    id: str | None
    page_content: str
    metadata: dict[str, Any] = field(default_factory=dict)


def make_document(
    chunk_id: str = "source-a:chunk-a", source: str = "source-a"
) -> Document:
    return Document(
        id=chunk_id,
        page_content="A refund policy",
        metadata={
            "source_external_id": source,
            "snapshot_external_id": "snapshot-1",
        },
    )


_DEFAULT = object()


class FakeWriter:
    def __init__(self, acknowledged_ids: object = _DEFAULT, failure=None):
        self.calls: list[tuple[list[Document], list[str]]] = []
        self.acknowledged_ids = acknowledged_ids
        self.failure = failure

    def add_documents(self, documents, *, ids):
        self.calls.append((documents, ids))
        if self.failure is not None:
            raise self.failure
        return ids if self.acknowledged_ids is _DEFAULT else self.acknowledged_ids


def indexing_args(writer, documents=None):
    return {
        "vector_store": writer,
        "authority_db": object(),
        "organization_id": uuid.uuid4(),
        "source_external_id": "source-a",
        "documents": [make_document()] if documents is None else documents,
        "index_external_id": "index-a",
        "index_revision_external_id": "index-a-r1",
        "sequence": 1,
        # Historical writer tests intentionally exercise ID-ACK legacy mode.
        "require_physical_readback": False,
    }


def test_indexing_writes_before_staging_exact_revision(monkeypatch):
    staged = []
    writer = FakeWriter()

    def stage(db, *, organization_id, observation):
        assert len(writer.calls) == 1
        staged.append(observation)

    monkeypatch.setattr(onboarding, "_stage_materialization", stage)
    document = make_document()
    result = onboarding.index_langchain_documents_with_lineage(
        **indexing_args(writer, [document])
    )

    assert result.document_ids == ("source-a:chunk-a",)
    assert [observation.asset_kind for observation in staged] == ["chunk", "index"]
    dependency = staged[-1].dependencies[0]
    assert dependency.dependency_type == "indexed_from"
    assert dependency.revision_external_id == document.metadata[
        "chunk_revision_external_id"
    ]
    assert writer.calls[0][1] == ["source-a:chunk-a"]


@pytest.mark.parametrize(
    "acknowledged",
    [[], ["other-id"], ["source-a:chunk-a", "source-a:chunk-a"], 0, None, [None]],
)
def test_partial_or_wrong_physical_write_never_stages_lineage(
    monkeypatch, acknowledged
):
    staged = []
    monkeypatch.setattr(
        onboarding,
        "_stage_materialization",
        lambda *args, **kwargs: staged.append(kwargs),
    )
    with pytest.raises(onboarding.LangChainPhysicalWriteMismatchError):
        onboarding.index_langchain_documents_with_lineage(
            **indexing_args(FakeWriter(acknowledged_ids=acknowledged))
        )
    assert staged == []


def test_physical_write_error_never_stages_lineage(monkeypatch):
    staged = []
    monkeypatch.setattr(
        onboarding,
        "_stage_materialization",
        lambda *args, **kwargs: staged.append(kwargs),
    )
    with pytest.raises(OSError, match="write failed"):
        onboarding.index_langchain_documents_with_lineage(
            **indexing_args(FakeWriter(failure=OSError("write failed")))
        )
    assert staged == []


@pytest.mark.parametrize("case", ["empty", "other-source", "duplicate-id"])
def test_invalid_batch_rejected_before_physical_write(monkeypatch, case):
    batches = {
        "empty": [],
        "other-source": [make_document(source="other-source")],
        "duplicate-id": [make_document("same"), make_document("same")],
    }
    staged = []
    monkeypatch.setattr(
        onboarding,
        "_stage_materialization",
        lambda *args, **kwargs: staged.append(kwargs),
    )
    writer = FakeWriter()
    with pytest.raises(ValueError):
        onboarding.index_langchain_documents_with_lineage(
            **indexing_args(writer, batches[case])
        )
    assert writer.calls == []
    assert staged == []


def test_authority_publication_failure_propagates_without_commit(monkeypatch):
    staged = []
    writer = FakeWriter()

    def stage(*args, **kwargs):
        staged.append(kwargs["observation"])
        raise RuntimeError("publication failed")

    monkeypatch.setattr(onboarding, "_stage_materialization", stage)
    with pytest.raises(RuntimeError, match="publication failed"):
        onboarding.index_langchain_documents_with_lineage(
            **indexing_args(writer)
        )
    assert len(writer.calls) == 1
    assert len(staged) == 1


def test_binding_registration_contains_only_collection_selector(monkeypatch):
    published = []

    def stage(db, *, organization_id, binding):
        published.append(binding)
        return "registered"

    monkeypatch.setattr(onboarding, "_stage_execution_binding", stage)
    result = onboarding.register_langchain_pgvector_causal_target(
        authority_db=object(),
        organization_id=uuid.uuid4(),
        source_external_id="source-a",
        index_external_id="index-a",
        connection_ref="downstream",
        collection_name="collection",
        binding_revision=1,
    )

    assert result == "registered"
    binding = published[0]
    assert binding.provider == "pgvector"
    assert binding.target_kind == "langchain_pgvector_causal_chunk"
    assert dict(binding.configuration) == {
        "connection_ref": "downstream",
        "collection_name": "collection",
    }
    assert binding.identity.source_external_id == "source-a"
    assert binding.identity.asset_external_id == "index-a"


@pytest.mark.parametrize(
    "organization_id,binding_revision,error",
    [(uuid.uuid4(), 0, ValueError), ("tenant-a", 1, TypeError)],
)
def test_invalid_registration_never_reaches_authority(
    monkeypatch, organization_id, binding_revision, error
):
    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected authority DB write")

    monkeypatch.setattr(onboarding, "_stage_execution_binding", forbidden)
    with pytest.raises(error):
        onboarding.register_langchain_pgvector_causal_target(
            authority_db=object(),
            organization_id=organization_id,
            source_external_id="source-a",
            index_external_id="index-a",
            connection_ref="downstream",
            collection_name="collection",
            binding_revision=binding_revision,
        )
