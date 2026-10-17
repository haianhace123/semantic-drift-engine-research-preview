from __future__ import annotations

import json
import uuid

import httpx
import pytest

from src.integrations.integrity_client import (
    SDEIntegrityClient,
    SDEIntegrityHTTPError,
    SDEIntegrityProtocolError,
    SDEIntegrityTransportError,
)


IDENTITY = {
    "source_external_id": "source-a",
    "index_external_id": "index-a",
    "binding_revision": 1,
    "connection_ref": "downstream-a",
    "collection_name": "collection-a",
}


def _echo(created: bool = True) -> dict[str, object]:
    return {
        "id": str(uuid.UUID(int=9)),
        "source_external_id": "source-a",
        "asset_external_id": "index-a",
        "binding_revision": 1,
        "provider": "pgvector",
        "target_kind": "langchain_pgvector_causal_chunk",
        "configuration": {
            "connection_ref": "downstream-a",
            "collection_name": "collection-a",
        },
        "created": created,
    }


def _client(handler):
    return SDEIntegrityClient(
        base_url="https://sde.example",
        api_key="not-for-logs",
        timeout_seconds=3,
        transport=httpx.MockTransport(handler),
    )


def test_http_binding_uses_api_key_and_exact_safe_selector() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=_echo())

    with _client(handler) as client:
        result = client.publish_causal_pgvector_binding(**IDENTITY)
    assert len(requests) == 1
    assert requests[0].url.path == "/api/lineage/execution-target-bindings"
    assert requests[0].headers["X-API-Key"] == "not-for-logs"
    assert json.loads(requests[0].content) == {
        "source_external_id": "source-a",
        "asset_external_id": "index-a",
        "binding_revision": 1,
        "provider": "pgvector",
        "target_kind": "langchain_pgvector_causal_chunk",
        "configuration": {
            "connection_ref": "downstream-a",
            "collection_name": "collection-a",
        },
    }
    assert result.id == uuid.UUID(int=9)
    assert result.index_external_id == "index-a"
    assert result.created is True


def test_http_binding_idempotent_replay() -> None:
    with _client(lambda _: httpx.Response(200, json=_echo(False))) as client:
        result = client.publish_causal_pgvector_binding(**IDENTITY)
    assert result.created is False


@pytest.mark.parametrize("change", [
    {"connection_ref": " "},
    {"source_external_id": ""},
    {"index_external_id": None},
    {"collection_name": "  "},
    {"binding_revision": 0},
    {"binding_revision": True},
    {"connection_ref": "postgresql://user:secret@db/test"},
    {"connection_ref": "api_key=secret"},
])
def test_http_binding_rejects_invalid_inputs_before_transport(change) -> None:
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, json=_echo())

    with _client(handler) as client:
        with pytest.raises((TypeError, ValueError)):
            client.publish_causal_pgvector_binding(**(IDENTITY | change))
    assert sent == []


@pytest.mark.parametrize("change", [
    {"id": "not-a-uuid"},
    {"created": 1},
    {"asset_external_id": "other-index"},
    {"binding_revision": 2},
    {"target_kind": "langchain_pgvector_document_revision"},
    {"provider": "unsupported"},
    {"configuration": {"connection_ref": "downstream-a", "collection_name": "other"}},
    {"configuration": {"connection_ref": "downstream-a", "collection_name": "collection-a", "document_id": "oops"}},
])
def test_http_binding_rejects_invalid_or_misbound_response(change) -> None:
    with _client(lambda _: httpx.Response(200, json=_echo() | change)) as client:
        with pytest.raises(SDEIntegrityProtocolError):
            client.publish_causal_pgvector_binding(**IDENTITY)


def test_http_binding_failures_do_not_log_secrets() -> None:
    with _client(lambda _: httpx.Response(409, text="secret DSN")) as client:
        with pytest.raises(SDEIntegrityHTTPError) as exc:
            client.publish_causal_pgvector_binding(**IDENTITY)
    assert exc.value.status_code == 409
    assert "secret" not in str(exc.value)
    assert "DSN" not in str(exc.value)


def test_http_binding_transport_failures_do_not_expose_url_or_secret() -> None:
    def handler(request):
        raise httpx.ConnectError("postgresql://secret@server", request=request)

    with _client(handler) as client:
        with pytest.raises(SDEIntegrityTransportError) as exc:
            client.publish_causal_pgvector_binding(**IDENTITY)
    assert "postgresql" not in str(exc.value)
