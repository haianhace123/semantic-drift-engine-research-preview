from __future__ import annotations

import json
import uuid

import httpx
import pytest

from src.domain.lineage_observation import (
    LineageObservation,
    LineageRevisionReference,
)
from src.integrations.integrity_client import (
    IntegrityRevision,
    SDEIntegrityClient,
    SDEIntegrityHTTPError,
    SDEIntegrityProtocolError,
    SDEIntegrityTransportError,
)


TARGET = IntegrityRevision(
    source_external_id="source-a",
    asset_external_id="chunk-a",
    revision_external_id="chunk-a-r3",
)


def _target_payload(*, status: str = "affected") -> dict[str, object]:
    return {
        "target": TARGET.as_payload(),
        "status": status,
        "evidence": [
            {
                "decision_id": "00000000-0000-0000-0000-000000000001",
                "root": {
                    "source_external_id": "source-a",
                    "asset_external_id": "page-a",
                    "revision_external_id": "page-a-r2",
                },
                "disposition": "invalidate",
                "reason_code": "causal_dependency_changed",
                "state": "unresolved",
            }
        ],
    }


def test_publish_lineage_observation_sends_exact_payload_and_parses_response() -> None:
    captured: dict[str, object] = {}
    publication_id = "00000000-0000-0000-0000-000000000099"
    observation = LineageObservation(
        observation_kind="consumption",
        source_external_id="source-a",
        snapshot_external_id="snapshot-a",
        asset_external_id="agent-a",
        asset_kind="agent",
        revision_external_id="agent-a-r7",
        sequence=7,
        dependencies=(
            LineageRevisionReference(
                asset_external_id="chunk-a",
                revision_external_id="chunk-a-r3",
                dependency_type="consumed",
            ),
            LineageRevisionReference(
                asset_external_id="chunk-b",
                revision_external_id="chunk-b-r4",
                dependency_type="consumed",
            ),
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(
            200,
            json={
                "id": publication_id,
                "created": True,
            },
        )

    with SDEIntegrityClient(
        base_url="https://sde.example/",
        api_key="secret-key",
        timeout_seconds=1.25,
        transport=httpx.MockTransport(handler),
    ) as client:
        publication = client.publish_lineage_observation(observation)

    request = captured["request"]
    assert isinstance(request, httpx.Request)
    assert request.url == httpx.URL(
        "https://sde.example/api/lineage/observations"
    )
    assert request.headers["X-API-Key"] == "secret-key"
    assert request.extensions["timeout"] == {
        "connect": 1.25,
        "read": 1.25,
        "write": 1.25,
        "pool": 1.25,
    }
    assert json.loads(request.content) == {
        "observation_kind": "consumption",
        "source_external_id": "source-a",
        "snapshot_external_id": "snapshot-a",
        "asset_external_id": "agent-a",
        "asset_kind": "agent",
        "revision_external_id": "agent-a-r7",
        "sequence": 7,
        "dependencies": [
            {
                "asset_external_id": "chunk-a",
                "revision_external_id": "chunk-a-r3",
                "dependency_type": "consumed",
            },
            {
                "asset_external_id": "chunk-b",
                "revision_external_id": "chunk-b-r4",
                "dependency_type": "consumed",
            },
        ],
    }
    assert str(publication.id) == publication_id
    assert publication.created is True


def test_publish_lineage_observation_preserves_idempotent_replay() -> None:
    observation = LineageObservation(
        observation_kind="materialization",
        source_external_id="source-a",
        snapshot_external_id="snapshot-a",
        asset_external_id="chunk-a",
        asset_kind="chunk",
        revision_external_id="chunk-a-r3",
        sequence=3,
    )

    with SDEIntegrityClient(
        base_url="https://sde.example",
        api_key="secret-key",
        timeout_seconds=1,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "id": "00000000-0000-0000-0000-000000000099",
                    "created": False,
                },
            )
        ),
    ) as client:
        publication = client.publish_lineage_observation(observation)

    assert publication.created is False


@pytest.mark.parametrize(
    "response_body",
    [
        {
            "id": "not-a-uuid",
            "created": True,
        },
        {
            "id": "00000000-0000-0000-0000-000000000099",
        },
        {
            "id": "00000000-0000-0000-0000-000000000099",
            "created": 1,
        },
    ],
)
def test_publish_lineage_observation_rejects_malformed_response(
    response_body: dict[str, object],
) -> None:
    observation = LineageObservation(
        observation_kind="materialization",
        source_external_id="source-a",
        snapshot_external_id="snapshot-a",
        asset_external_id="chunk-a",
        asset_kind="chunk",
        revision_external_id="chunk-a-r3",
        sequence=3,
    )

    with SDEIntegrityClient(
        base_url="https://sde.example",
        api_key="secret-key",
        timeout_seconds=1,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=response_body)
        ),
    ) as client:
        with pytest.raises(SDEIntegrityProtocolError):
            client.publish_lineage_observation(observation)


def test_publish_lineage_observation_preserves_http_conflict() -> None:
    observation = LineageObservation(
        observation_kind="materialization",
        source_external_id="source-a",
        snapshot_external_id="snapshot-a",
        asset_external_id="chunk-a",
        asset_kind="chunk",
        revision_external_id="chunk-a-r3",
        sequence=3,
    )

    with SDEIntegrityClient(
        base_url="https://sde.example",
        api_key="secret-key",
        timeout_seconds=1,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(409)
        ),
    ) as client:
        with pytest.raises(SDEIntegrityHTTPError) as exc_info:
            client.publish_lineage_observation(observation)

    assert exc_info.value.status_code == 409

def test_publish_lineage_execution_verification_sends_exact_binding() -> None:
    captured: dict[str, object] = {}
    execution_result_id = uuid.UUID("00000000-0000-0000-0000-000000000101")
    observation_id = uuid.UUID("00000000-0000-0000-0000-000000000102")
    verification_id = uuid.UUID("00000000-0000-0000-0000-000000000103")

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(
            200,
            json={
                "id": str(verification_id),
                "execution_result_id": str(execution_result_id),
                "observation_id": str(observation_id),
                "created": True,
            },
        )

    with SDEIntegrityClient(
        base_url="https://sde.example",
        api_key="secret-key",
        timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    ) as client:
        publication = client.publish_lineage_execution_verification(
            execution_result_id=execution_result_id,
            observation_id=observation_id,
        )

    request = captured["request"]
    assert isinstance(request, httpx.Request)
    assert request.url == httpx.URL(
        "https://sde.example/api/lineage/invalidation-execution-verifications"
    )
    assert json.loads(request.content) == {
        "execution_result_id": str(execution_result_id),
        "observation_id": str(observation_id),
    }
    assert publication.id == verification_id
    assert publication.execution_result_id == execution_result_id
    assert publication.observation_id == observation_id
    assert publication.created is True


def test_publish_lineage_recovery_publishes_evidence_then_binds_it() -> None:
    execution_result_id = uuid.UUID("00000000-0000-0000-0000-000000000101")
    observation_id = uuid.UUID("00000000-0000-0000-0000-000000000102")
    verification_id = uuid.UUID("00000000-0000-0000-0000-000000000103")
    requests: list[httpx.Request] = []

    observation = LineageObservation(
        observation_kind="materialization",
        source_external_id="source-a",
        snapshot_external_id="snapshot-8",
        asset_external_id="index-a",
        asset_kind="index",
        revision_external_id="index-a-r8",
        sequence=8,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)

        if request.url.path == "/api/lineage/observations":
            return httpx.Response(
                200,
                json={
                    "id": str(observation_id),
                    "created": True,
                },
            )

        assert request.url.path == (
            "/api/lineage/invalidation-execution-verifications"
        )
        return httpx.Response(
            200,
            json={
                "id": str(verification_id),
                "execution_result_id": str(execution_result_id),
                "observation_id": str(observation_id),
                "created": True,
            },
        )

    with SDEIntegrityClient(
        base_url="https://sde.example",
        api_key="secret-key",
        timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    ) as client:
        recovery = client.publish_lineage_recovery(
            execution_result_id=execution_result_id,
            observation=observation,
        )

    assert [request.url.path for request in requests] == [
        "/api/lineage/observations",
        "/api/lineage/invalidation-execution-verifications",
    ]
    assert recovery.observation.id == observation_id
    assert recovery.observation.created is True
    assert recovery.verification.id == verification_id
    assert recovery.verification.execution_result_id == execution_result_id
    assert recovery.verification.observation_id == observation_id
    assert recovery.verification.created is True


def test_publish_lineage_recovery_rejects_non_materialization_before_http() -> None:
    requests: list[httpx.Request] = []
    observation = LineageObservation(
        observation_kind="consumption",
        source_external_id="source-a",
        snapshot_external_id="snapshot-a",
        asset_external_id="agent-a",
        asset_kind="agent",
        revision_external_id="agent-a-r7",
        sequence=7,
        dependencies=(
            LineageRevisionReference(
                asset_external_id="chunk-a",
                revision_external_id="chunk-a-r3",
                dependency_type="consumed",
            ),
        ),
    )

    with SDEIntegrityClient(
        base_url="https://sde.example",
        api_key="secret-key",
        timeout_seconds=1,
        transport=httpx.MockTransport(
            lambda request: requests.append(request) or httpx.Response(500)
        ),
    ) as client:
        with pytest.raises(ValueError, match="materialization"):
            client.publish_lineage_recovery(
                execution_result_id=uuid.uuid4(),
                observation=observation,
            )

    assert requests == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "not-a-uuid"),
        ("execution_result_id", "not-a-uuid"),
        ("observation_id", "not-a-uuid"),
        ("created", 1),
    ],
)


def test_publish_lineage_execution_verification_rejects_malformed_response(
    field: str,
    value: object,
) -> None:
    execution_result_id = uuid.UUID("00000000-0000-0000-0000-000000000101")
    observation_id = uuid.UUID("00000000-0000-0000-0000-000000000102")
    response = {
        "id": "00000000-0000-0000-0000-000000000103",
        "execution_result_id": str(execution_result_id),
        "observation_id": str(observation_id),
        "created": True,
    }
    response[field] = value

    with SDEIntegrityClient(
        base_url="https://sde.example",
        api_key="secret-key",
        timeout_seconds=1,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=response)
        ),
    ) as client:
        with pytest.raises(SDEIntegrityProtocolError):
            client.publish_lineage_execution_verification(
                execution_result_id=execution_result_id,
                observation_id=observation_id,
            )


@pytest.mark.parametrize(
    "field",
    [
        "execution_result_id",
        "observation_id",
    ],
)
def test_publish_lineage_execution_verification_rejects_mismatched_binding(
    field: str,
) -> None:
    execution_result_id = uuid.UUID("00000000-0000-0000-0000-000000000101")
    observation_id = uuid.UUID("00000000-0000-0000-0000-000000000102")
    response = {
        "id": "00000000-0000-0000-0000-000000000103",
        "execution_result_id": str(execution_result_id),
        "observation_id": str(observation_id),
        "created": True,
    }
    response[field] = "00000000-0000-0000-0000-000000000999"

    with SDEIntegrityClient(
        base_url="https://sde.example",
        api_key="secret-key",
        timeout_seconds=1,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=response)
        ),
    ) as client:
        with pytest.raises(SDEIntegrityProtocolError, match="requested"):
            client.publish_lineage_execution_verification(
                execution_result_id=execution_result_id,
                observation_id=observation_id,
            )


def test_target_query_sends_api_key_timeout_and_exact_revision() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(200, json=_target_payload())

    with SDEIntegrityClient(
        base_url="https://sde.example/",
        api_key="secret-key",
        timeout_seconds=1.25,
        transport=httpx.MockTransport(handler),
    ) as client:
        assessment = client.query_target_integrity(TARGET)

    request = captured["request"]
    assert isinstance(request, httpx.Request)
    assert request.url == httpx.URL(
        "https://sde.example/api/lineage/target-integrity/query"
    )
    assert request.headers["X-API-Key"] == "secret-key"
    assert json.loads(request.content) == TARGET.as_payload()
    assert request.extensions["timeout"] == {
        "connect": 1.25,
        "read": 1.25,
        "write": 1.25,
        "pool": 1.25,
    }
    assert assessment.status == "affected"
    assert assessment.evidence[0].state == "unresolved"


def test_context_query_preserves_target_order_and_parses_summary() -> None:
    second = IntegrityRevision("source-b", "chunk-b", "chunk-b-r4")

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {
            "targets": [TARGET.as_payload(), second.as_payload()]
        }
        return httpx.Response(
            200,
            json={
                "assessments": [
                    _target_payload(status="affected"),
                    {
                        "target": second.as_payload(),
                        "status": "unknown",
                        "evidence": [],
                    },
                ],
                "summary": {
                    "target_count": 2,
                    "unknown_count": 1,
                    "not_affected_count": 0,
                    "affected_count": 1,
                    "verified_count": 0,
                    "has_unknown": True,
                    "has_unresolved_impact": True,
                },
            },
        )

    with SDEIntegrityClient(
        base_url="https://sde.example",
        api_key="secret-key",
        timeout_seconds=2,
        transport=httpx.MockTransport(handler),
    ) as client:
        result = client.query_context_integrity((TARGET, second))

    assert tuple(item.target for item in result.assessments) == (TARGET, second)
    assert result.summary.target_count == 2
    assert result.summary.has_unknown is True
    assert result.summary.has_unresolved_impact is True


def test_context_query_rejects_empty_or_oversized_context_locally() -> None:
    with SDEIntegrityClient(
        base_url="https://sde.example",
        api_key="secret-key",
        timeout_seconds=2,
        transport=httpx.MockTransport(lambda _: httpx.Response(500)),
    ) as client:
        with pytest.raises(ValueError, match="at least one"):
            client.query_context_integrity(())
        with pytest.raises(ValueError, match="at most 64"):
            client.query_context_integrity((TARGET,) * 65)


def test_client_requires_explicit_valid_connection_settings() -> None:
    with pytest.raises(ValueError, match="base URL"):
        SDEIntegrityClient(base_url=" ", api_key="key", timeout_seconds=1)
    with pytest.raises(ValueError, match="API key"):
        SDEIntegrityClient(base_url="https://sde.example", api_key=" ", timeout_seconds=1)
    with pytest.raises(ValueError, match="timeout"):
        SDEIntegrityClient(base_url="https://sde.example", api_key="key", timeout_seconds=0)


def test_transport_failure_is_distinct_and_does_not_expose_api_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with SDEIntegrityClient(
        base_url="https://sde.example",
        api_key="top-secret-key",
        timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(SDEIntegrityTransportError) as exc_info:
            client.query_target_integrity(TARGET)

    assert "top-secret-key" not in str(exc_info.value)


def test_http_failure_exposes_status_without_response_body_or_secret() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={"detail": "top-secret-key should never be reflected"},
        )

    with SDEIntegrityClient(
        base_url="https://sde.example",
        api_key="top-secret-key",
        timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(SDEIntegrityHTTPError) as exc_info:
            client.query_target_integrity(TARGET)

    assert exc_info.value.status_code == 503
    assert "HTTP 503" in str(exc_info.value)
    assert "top-secret-key" not in str(exc_info.value)



def test_malformed_or_unknown_response_fails_closed() -> None:
    responses = iter(
        [
            httpx.Response(200, content=b"not-json"),
            httpx.Response(200, json=_target_payload(status="trusted")),
        ]
    )

    with SDEIntegrityClient(
        base_url="https://sde.example",
        api_key="secret-key",
        timeout_seconds=1,
        transport=httpx.MockTransport(lambda _: next(responses)),
    ) as client:
        with pytest.raises(SDEIntegrityProtocolError, match="valid JSON"):
            client.query_target_integrity(TARGET)
        with pytest.raises(SDEIntegrityProtocolError, match="unknown integrity status"):
            client.query_target_integrity(TARGET)


def test_target_response_must_bind_to_requested_revision() -> None:
    wrong_target = IntegrityRevision("source-a", "chunk-other", "chunk-other-r1")

    with SDEIntegrityClient(
        base_url="https://sde.example",
        api_key="secret-key",
        timeout_seconds=1,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "target": wrong_target.as_payload(),
                    "status": "unknown",
                    "evidence": [],
                },
            )
        ),
    ) as client:
        with pytest.raises(SDEIntegrityProtocolError, match="requested revision"):
            client.query_target_integrity(TARGET)


def test_context_response_must_preserve_requested_revision_binding() -> None:
    second = IntegrityRevision("source-b", "chunk-b", "chunk-b-r4")

    with SDEIntegrityClient(
        base_url="https://sde.example",
        api_key="secret-key",
        timeout_seconds=1,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "assessments": [
                        {
                            "target": second.as_payload(),
                            "status": "unknown",
                            "evidence": [],
                        },
                        {
                            "target": TARGET.as_payload(),
                            "status": "unknown",
                            "evidence": [],
                        },
                    ],
                    "summary": {
                        "target_count": 2,
                        "unknown_count": 2,
                        "not_affected_count": 0,
                        "affected_count": 0,
                        "verified_count": 0,
                        "has_unknown": True,
                        "has_unresolved_impact": False,
                    },
                },
            )
        ),
    ) as client:
        with pytest.raises(SDEIntegrityProtocolError, match="requested revisions"):
            client.query_context_integrity((TARGET, second))


def test_context_response_rejects_summary_status_count_mismatch() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "assessments": [_target_payload(status="affected")],
                "summary": {
                    "target_count": 1,
                    "unknown_count": 1,
                    "not_affected_count": 0,
                    "affected_count": 0,
                    "verified_count": 0,
                    "has_unknown": True,
                    "has_unresolved_impact": False,
                },
            },
        )

    with SDEIntegrityClient(
        base_url="https://sde.example",
        api_key="secret-key",
        timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(SDEIntegrityProtocolError, match="status counts"):
            client.query_context_integrity((TARGET,))


def test_context_response_rejects_inconsistent_summary_flags() -> None:
    summaries = iter(
        [
            {
                "target_count": 1,
                "unknown_count": 1,
                "not_affected_count": 0,
                "affected_count": 0,
                "verified_count": 0,
                "has_unknown": False,
                "has_unresolved_impact": False,
            },
            {
                "target_count": 1,
                "unknown_count": 0,
                "not_affected_count": 0,
                "affected_count": 1,
                "verified_count": 0,
                "has_unknown": False,
                "has_unresolved_impact": False,
            },
        ]
    )
    statuses = iter(["unknown", "affected"])

    def handler(_: httpx.Request) -> httpx.Response:
        status = next(statuses)
        payload = (
            {
                "target": TARGET.as_payload(),
                "status": "unknown",
                "evidence": [],
            }
            if status == "unknown"
            else _target_payload(status="affected")
        )
        return httpx.Response(
            200,
            json={
                "assessments": [payload],
                "summary": next(summaries),
            },
        )

    with SDEIntegrityClient(
        base_url="https://sde.example",
        api_key="secret-key",
        timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(SDEIntegrityProtocolError, match="unknown flag"):
            client.query_context_integrity((TARGET,))
        with pytest.raises(SDEIntegrityProtocolError, match="unresolved-impact flag"):
            client.query_context_integrity((TARGET,))
