"""P2.8B: physical readback must precede both local and HTTP lineage publication."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from typing import Any

import pytest

import src.integrations.langchain.onboarding as local
import src.integrations.langchain.remote_onboarding as remote
from src.integrations.langchain.physical_readback import LangChainPhysicalReadbackError


@dataclass
class Document:
    id: str | None
    page_content: str
    metadata: dict[str, Any]


def docs():
    return [
        Document("refund-1", "Refund within 30 days 📘", {"source_external_id": "source-a", "snapshot_external_id": "snap-1"}),
        Document("refund-2", "Exceptions for damaged goods", {"source_external_id": "source-a", "snapshot_external_id": "snap-1"}),
    ]


class Writer:
    def __init__(self, transform=None, mutation=None, error=None):
        self.transform = transform
        self.mutation = mutation
        self.error = error
        self.writes = 0
        self.reads = 0
        self.documents: list[Document] = []

    def add_documents(self, documents, *, ids):
        self.writes += 1
        self.documents = [Document(x.id, x.page_content, dict(x.metadata)) for x in documents]
        if self.mutation:
            self.mutation(documents)
            self.documents = [Document(x.id, x.page_content, dict(x.metadata)) for x in documents]
        return ids

    def get_by_ids(self, ids):
        self.reads += 1
        if self.error:
            raise self.error
        materialized = [Document(x.id, x.page_content, dict(x.metadata)) for x in self.documents]
        return self.transform(materialized) if self.transform else materialized


class LocalPublication:
    def __init__(self, monkeypatch):
        self.staged = []
        monkeypatch.setattr(local, "_stage_materialization", self._stage)

    def _stage(self, db, *, organization_id, observation):
        self.staged.append(observation)


class RemotePublication:
    def __init__(self):
        self.staged = []

    def publish_lineage_observation(self, observation):
        self.staged.append(observation)
        return object()


def call_local(writer, publisher, require_physical_readback=True):
    return local.index_langchain_documents_with_lineage(
        vector_store=writer, authority_db=object(), organization_id=uuid.uuid4(),
        source_external_id="source-a", documents=docs(), index_external_id="index-a",
        index_revision_external_id="index-r1", sequence=1,
        require_physical_readback=require_physical_readback,
    )


def call_remote(writer, publisher, require_physical_readback=True):
    return remote.index_langchain_documents_with_remote_lineage(
        vector_store=writer, lineage_client=publisher,
        source_external_id="source-a", documents=docs(), index_external_id="index-a",
        index_revision_external_id="index-r1", sequence=1,
        require_physical_readback=require_physical_readback,
    )


@pytest.mark.parametrize("kind", ["local", "remote"])
def test_exact_physical_readback_precedes_lineage_publication(monkeypatch, kind):
    writer = Writer(transform=lambda value: list(reversed(value)))
    pub = LocalPublication(monkeypatch) if kind == "local" else RemotePublication()
    call = call_local if kind == "local" else call_remote
    result = call(writer, pub)
    assert writer.reads == 1
    assert writer.writes == 1
    assert result.point_in_time_readback_verified is True
    assert [x.asset_kind for x in pub.staged] == ["chunk", "chunk", "index"]


@pytest.mark.parametrize("kind", ["local", "remote"])
def test_legacy_mode_does_not_pretend_to_have_readback(monkeypatch, kind):
    writer = Writer()
    pub = LocalPublication(monkeypatch) if kind == "local" else RemotePublication()
    call = call_local if kind == "local" else call_remote
    result = call(writer, pub, require_physical_readback=False)
    assert result.point_in_time_readback_verified is False
    assert writer.reads == 0
    assert len(pub.staged) == 3


@pytest.mark.parametrize("kind", ["local", "remote"])
def test_no_readback_capability_blocks_before_physical_write(monkeypatch, kind):
    class NoReader:
        writes = 0

        def add_documents(self, documents, *, ids):
            self.writes += 1
            return ids

    writer = NoReader()
    pub = LocalPublication(monkeypatch) if kind == "local" else RemotePublication()
    call = call_local if kind == "local" else call_remote
    with pytest.raises(LangChainPhysicalReadbackError):
        call(writer, pub)
    assert writer.writes == 0
    assert pub.staged == []


def _alter(result, defect):
    if defect == "missing":
        return result[:-1]
    if defect == "duplicate":
        return [result[0], result[0]]
    if defect == "wrong_id":
        return [replace(result[0], id="tenant-other"), result[1]]
    if defect == "wrong_content":
        return [replace(result[0], page_content="Refund within 365 days 📘"), result[1]]
    if defect == "wrong_source":
        result[0].metadata["source_external_id"] = "source-other"
    if defect == "wrong_snapshot":
        result[0].metadata["snapshot_external_id"] = "snap-2"
    if defect == "wrong_revision":
        result[0].metadata["chunk_revision_external_id"] = "revision-fake"
    if defect == "missing_revision":
        del result[0].metadata["chunk_revision_external_id"]
    if defect == "not_list":
        return iter(result)
    return result


@pytest.mark.parametrize("kind", ["local", "remote"])
@pytest.mark.parametrize("defect", [
    "missing", "duplicate", "wrong_id", "wrong_content", "wrong_source",
    "wrong_snapshot", "wrong_revision", "missing_revision", "not_list",
])
def test_wrong_physical_content_or_provenance_never_published(monkeypatch, kind, defect):
    writer = Writer(transform=lambda result: _alter(result, defect))
    pub = LocalPublication(monkeypatch) if kind == "local" else RemotePublication()
    call = call_local if kind == "local" else call_remote
    with pytest.raises(LangChainPhysicalReadbackError):
        call(writer, pub)
    assert writer.writes == 1 and writer.reads == 1
    assert pub.staged == []


@pytest.mark.parametrize("kind", ["local", "remote"])
def test_writer_mutating_submitted_content_does_not_change_expected_bytes(monkeypatch, kind):
    def rewrite(documents):
        documents[0].page_content = "Refund within 365 days 📘"
    writer = Writer(mutation=rewrite)
    pub = LocalPublication(monkeypatch) if kind == "local" else RemotePublication()
    call = call_local if kind == "local" else call_remote
    with pytest.raises(LangChainPhysicalReadbackError):
        call(writer, pub)
    assert pub.staged == []


@pytest.mark.parametrize("kind", ["local", "remote"])
def test_readback_exception_fails_closed_without_publication(monkeypatch, kind):
    writer = Writer(error=OSError("sensitive connection details"))
    pub = LocalPublication(monkeypatch) if kind == "local" else RemotePublication()
    call = call_local if kind == "local" else call_remote
    with pytest.raises(LangChainPhysicalReadbackError, match="reconcile") as error:
        call(writer, pub)
    assert "sensitive" not in str(error.value)
    assert pub.staged == []


@pytest.mark.parametrize("kind", ["local", "remote"])
def test_nonboolean_strict_flag_rejected_before_write(monkeypatch, kind):
    writer = Writer()
    pub = LocalPublication(monkeypatch) if kind == "local" else RemotePublication()
    call = call_local if kind == "local" else call_remote
    with pytest.raises(TypeError):
        call(writer, pub, require_physical_readback="yes")
    assert writer.writes == 0 and pub.staged == []

@pytest.mark.parametrize("kind", ["local", "remote"])
def test_ack_only_legacy_can_publish_a_mutated_physical_row(monkeypatch, kind):
    """Reproduce the bug: exact ID ACK is not evidence of stored bytes."""
    def rewrite(documents):
        documents[0].page_content = "Refund within 365 days 📘"
    writer = Writer(mutation=rewrite)
    pub = LocalPublication(monkeypatch) if kind == "local" else RemotePublication()
    call = call_local if kind == "local" else call_remote
    result = call(writer, pub, require_physical_readback=False)
    assert result.point_in_time_readback_verified is False
    assert writer.documents[0].page_content == "Refund within 365 days 📘"
    assert pub.staged[0].revision_external_id != writer.documents[0].page_content
    assert len(pub.staged) == 3  # Shows legacy ACK-only publication succeeds.


@pytest.mark.parametrize("kind", ["local", "remote"])
def test_strict_readback_is_default_even_without_flag(monkeypatch, kind):
    writer = Writer()
    pub = LocalPublication(monkeypatch) if kind == "local" else RemotePublication()
    params = dict(
        vector_store=writer, source_external_id="source-a", documents=docs(),
        index_external_id="index-a", index_revision_external_id="index-r1", sequence=1,
    )
    if kind == "local":
        result = local.index_langchain_documents_with_lineage(
            authority_db=object(), organization_id=uuid.uuid4(), **params,
        )
    else:
        result = remote.index_langchain_documents_with_remote_lineage(
            lineage_client=pub, **params,
        )
    assert writer.reads == writer.writes == 1
    assert result.point_in_time_readback_verified is True


@pytest.mark.parametrize("kind", ["local", "remote"])
def test_default_no_readback_capability_fails_before_write(monkeypatch, kind):
    class LegacyWriter:
        writes = 0
        def add_documents(self, documents, *, ids):
            self.writes += 1
            return ids

    writer = LegacyWriter()
    pub = LocalPublication(monkeypatch) if kind == "local" else RemotePublication()
    params = dict(
        vector_store=writer, source_external_id="source-a", documents=docs(),
        index_external_id="index-a", index_revision_external_id="index-r1", sequence=1,
    )
    with pytest.raises(LangChainPhysicalReadbackError, match="get_by_ids"):
        if kind == "local":
            local.index_langchain_documents_with_lineage(
                authority_db=object(), organization_id=uuid.uuid4(), **params,
            )
        else:
            remote.index_langchain_documents_with_remote_lineage(
                lineage_client=pub, **params,
            )
    assert writer.writes == 0
    assert not pub.staged
