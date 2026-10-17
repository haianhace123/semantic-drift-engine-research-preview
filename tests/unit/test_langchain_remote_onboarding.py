from __future__ import annotations

import json
import uuid
from dataclasses import dataclass

import httpx
import pytest

from src.integrations.integrity_client import (
    SDEIntegrityClient,
    SDEIntegrityHTTPError,
)
from src.integrations.langchain.onboarding import LangChainPhysicalWriteMismatchError
from src.integrations.langchain.remote_onboarding import (
    index_langchain_documents_with_remote_lineage,
)


@dataclass
class Document:
    id: str | None
    page_content: str
    metadata: dict


class Writer:
    def __init__(self, acknowledged=None, exception=None):
        self.acknowledged = acknowledged
        self.exception = exception
        self.called = 0
        self.ids = []

    def add_documents(self, documents, *, ids):
        self.called += 1
        self.ids = ids
        if self.exception:
            raise self.exception
        return ids if self.acknowledged is None else self.acknowledged


def docs():
    return [
        Document("chunk-a", "refunds", {"source_external_id": "source-a", "snapshot_external_id": "snap-1"}),
        Document("chunk-b", "billing", {"source_external_id": "source-a", "snapshot_external_id": "snap-1"}),
    ]


def args(writer, client, documents=None):
    return dict(
        vector_store=writer,
        lineage_client=client,
        source_external_id="source-a",
        documents=docs() if documents is None else documents,
        index_external_id="index-a",
        index_revision_external_id="index-a-r1",
        sequence=1,
        # These tests intentionally cover legacy ACK-only HTTP behavior.
        require_physical_readback=False,
    )


def _client(handler):
    return SDEIntegrityClient(
        base_url="https://sde.example",
        api_key="tenant-write-key",
        timeout_seconds=2,
        transport=httpx.MockTransport(handler),
    )


def test_remote_index_writes_before_publication_and_index_is_last() -> None:
    writer = Writer()
    sent = []

    def handler(request):
        assert writer.called == 1
        assert writer.ids == ["chunk-a", "chunk-b"]
        assert request.headers["X-API-Key"] == "tenant-write-key"
        assert request.url.path == "/api/lineage/observations"
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"id": str(uuid.uuid4()), "created": True})

    with _client(handler) as client:
        result = index_langchain_documents_with_remote_lineage(**args(writer, client))

    assert result.document_ids == ("chunk-a", "chunk-b")
    assert [obs["asset_kind"] for obs in sent] == ["chunk", "chunk", "index"]
    assert [obs["asset_external_id"] for obs in sent] == ["chunk-a", "chunk-b", "index-a"]
    assert sent[-1]["dependencies"] == [
        {"asset_external_id": "chunk-a", "revision_external_id": sent[0]["revision_external_id"], "dependency_type": "indexed_from"},
        {"asset_external_id": "chunk-b", "revision_external_id": sent[1]["revision_external_id"], "dependency_type": "indexed_from"},
    ]
    assert len(result.publications) == 3


@pytest.mark.parametrize("acknowledged", [[], ["chunk-a"], ["chunk-a", "wrong"], ["chunk-a", "chunk-a"], False])
def test_remote_index_incomplete_ack_never_publishes(acknowledged) -> None:
    writer = Writer(acknowledged=acknowledged)
    requested = []

    def handler(request):
        requested.append(request)
        return httpx.Response(200, json={"id": str(uuid.uuid4()), "created": True})

    with _client(handler) as client:
        with pytest.raises(LangChainPhysicalWriteMismatchError):
            index_langchain_documents_with_remote_lineage(**args(writer, client))
    assert requested == []


def test_remote_index_physical_failure_never_publishes() -> None:
    writer = Writer(exception=RuntimeError("write failed"))
    with _client(lambda _: pytest.fail("unexpected HTTP")) as client:
        with pytest.raises(RuntimeError, match="write failed"):
            index_langchain_documents_with_remote_lineage(**args(writer, client))


@pytest.mark.parametrize("invalid", [
    lambda ds: [Document("chunk-a", "x", {"source_external_id": "wrong", "snapshot_external_id": "snap-1"})],
    lambda ds: [Document("chunk-a", "x", {"source_external_id": "source-a", "snapshot_external_id": "snap-1"}), Document("chunk-a", "y", {"source_external_id": "source-a", "snapshot_external_id": "snap-1"})],
    lambda ds: [],
])
def test_remote_index_invalid_data_fails_before_physical_write(invalid) -> None:
    writer = Writer()
    with _client(lambda _: pytest.fail("unexpected HTTP")) as client:
        with pytest.raises(ValueError):
            index_langchain_documents_with_remote_lineage(**args(writer, client, invalid(docs())))
    assert writer.called == 0


def test_remote_index_partial_http_failure_does_not_publish_index() -> None:
    writer = Writer()
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        if len(sent) == 2:
            return httpx.Response(503, text="database password")
        return httpx.Response(200, json={"id": str(uuid.uuid4()), "created": True})

    with _client(handler) as client:
        with pytest.raises(SDEIntegrityHTTPError):
            index_langchain_documents_with_remote_lineage(**args(writer, client))
    assert len(sent) == 2
    assert all(item["asset_kind"] == "chunk" for item in sent)


def test_remote_index_idempotent_replay() -> None:
    writer = Writer()
    seen = {}

    def handler(request):
        key = (request.url.path, json.dumps(json.loads(request.content), sort_keys=True))
        was_seen = key in seen
        if not was_seen:
            seen[key] = str(uuid.uuid4())
        return httpx.Response(200, json={"id": seen[key], "created": not was_seen})

    with _client(handler) as client:
        first = index_langchain_documents_with_remote_lineage(**args(writer, client))
        second = index_langchain_documents_with_remote_lineage(**args(writer, client))
    assert all(item.created for item in first.publications)
    assert all(not item.created for item in second.publications)


def test_remote_index_never_registers_binding_or_authorizes_execution() -> None:
    writer = Writer()
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={"id": str(uuid.uuid4()), "created": True})

    with _client(handler) as client:
        index_langchain_documents_with_remote_lineage(**args(writer, client))
    assert paths == ["/api/lineage/observations"] * 3
