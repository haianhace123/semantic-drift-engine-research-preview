from __future__ import annotations

import os
import secrets
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from benchmarks.temporal_knowledge_integrity.langchain_lineage_adapter import (
    capture_langchain_consumer_binding,
    capture_langchain_index_materialization as capture_benchmark_langchain_index_materialization,
)
from src.core.database import SessionLocal
from src.core.security import (
    CSRF_COOKIE,
    hash_password,
    token_hash,
)
from src.integrations.integrity_client import SDEIntegrityClient
from src.integrations.langchain.lineage import (
    capture_langchain_index_materialization,
)
from src.main import app
from src.models import (
    ApiKey,
    Organization,
    User,
)
from src.domain.lineage_revision import (
    LineageRevisionIdentity,
)
from src.models.lineage_execution_target_binding import (
    LineageExecutionTargetBindingRecord,
)
from src.models.lineage_observation import (
    LineageObservationRecord,
)
from src.services.lineage_impact_service import (
    analyze_lineage_impact,
)


RUN_DB_INTEGRATION_TESTS = (
    os.getenv(
        "RUN_DB_INTEGRATION_TESTS",
        "0",
    )
    == "1"
)


pytestmark = pytest.mark.skipif(
    not RUN_DB_INTEGRATION_TESTS,
    reason=(
        "Set RUN_DB_INTEGRATION_TESTS=1 "
        "to run database integration tests."
    ),
)

TEST_PASSWORD = (
    "LineageIntegration!123"
)


class _FastAPITestTransport(httpx.BaseTransport):
    def __init__(
        self,
        client: TestClient,
    ) -> None:
        self._client = client

    def handle_request(
        self,
        request: httpx.Request,
    ) -> httpx.Response:
        response = self._client.request(
            request.method,
            request.url.path,
            headers=dict(request.headers),
            content=request.content,
        )

        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            content=response.content,
            request=request,
        )


def _create_organization(
    *,
    prefix: str,
) -> uuid.UUID:
    db = SessionLocal()

    try:
        suffix = uuid.uuid4().hex[:12]

        organization = Organization(
            name=(
                f"{prefix}-"
                f"{suffix}"
            ),
            slug=(
                f"{prefix}-"
                f"{suffix}"
            ),
            billing_email=(
                f"{prefix}-{suffix}@example.com"
            ),
        )
        db.add(organization)
        db.commit()
        db.refresh(organization)

        return organization.id

    finally:
        db.close()


def _create_user(
    *,
    organization_id: uuid.UUID,
    email: str,
) -> uuid.UUID:
    db = SessionLocal()

    try:
        user = User(
            organization_id=organization_id,
            email=email,
            password_hash=hash_password(
                TEST_PASSWORD
            ),
            full_name=(
                "Lineage Integration"
            ),
            role="user",
            is_active=True,
            email_verified=True,
            login_attempts=0,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        return user.id

    finally:
        db.close()


def _login(
    client: TestClient,
    *,
    email: str,
) -> None:
    csrf_response = client.get(
        "/api/auth/csrf"
    )

    assert (
        csrf_response.status_code
        == 200
    )

    csrf_token = client.cookies.get(
        CSRF_COOKIE
    )

    assert csrf_token

    response = client.post(
        "/api/auth/login",
        headers={
            "X-CSRF-Token":
                csrf_token,
        },
        json={
            "email":
                email,
            "password":
                TEST_PASSWORD,
        },
    )

    assert (
        response.status_code
        == 200
    ), response.text


def _create_api_key(
    *,
    organization_id: uuid.UUID,
    write: bool,
) -> tuple[uuid.UUID, str]:
    raw_key = (
        f"sk-{secrets.token_urlsafe(32)}"
    )

    db = SessionLocal()

    try:
        api_key = ApiKey(
            organization_id=organization_id,
            name="lineage-integration",
            key_hash=token_hash(
                raw_key
            ),
            permissions={
                "read": True,
                "write": write,
            },
            is_active=True,
        )
        db.add(api_key)
        db.commit()
        db.refresh(api_key)

        return api_key.id, raw_key

    finally:
        db.close()


def _cleanup_organization(
    organization_id: uuid.UUID,
) -> None:
    db = SessionLocal()

    try:
        organization = (
            db.query(Organization)
            .filter(
                Organization.id
                == organization_id
            )
            .one_or_none()
        )

        if organization is not None:
            (
                db.query(ApiKey)
                .filter(
                    ApiKey.organization_id
                    == organization_id
                )
                .delete(
                    synchronize_session=False
                )
            )

            (
                db.query(User)
                .filter(
                    User.organization_id
                    == organization_id
                )
                .delete(
                    synchronize_session=False
                )
            )

            db.delete(organization)
            db.commit()

    finally:
        db.close()


def _observation_payload(
    *,
    source_external_id: str,
    asset_external_id: str,
    sequence: int = 7,
) -> dict[str, object]:
    return {
        "observation_kind":
            "materialization",
        "source_external_id":
            source_external_id,
        "snapshot_external_id":
            "snapshot-v1",
        "asset_external_id":
            asset_external_id,
        "asset_kind":
            "index",
        "revision_external_id":
            "index-revision-v1",
        "sequence":
            sequence,
        "dependencies": [
            {
                "asset_external_id":
                    "chunk-1",
                "revision_external_id":
                    "chunk-revision-v1",
                "dependency_type":
                    "indexed_from",
            }
        ],
    }


def _load_observation(
    *,
    organization_id: uuid.UUID,
    source_external_id: str,
    asset_external_id: str,
    sequence: int,
) -> LineageObservationRecord | None:
    db = SessionLocal()

    try:
        return (
            db.query(
                LineageObservationRecord
            )
            .filter(
                LineageObservationRecord.organization_id
                == organization_id,
                LineageObservationRecord.source_external_id
                == source_external_id,
                LineageObservationRecord.asset_external_id
                == asset_external_id,
                LineageObservationRecord.observation_kind
                == "materialization",
                LineageObservationRecord.sequence
                == sequence,
            )
            .one_or_none()
        )

    finally:
        db.close()


def test_lineage_ingestion_requires_api_key():
    client = TestClient(
        app
    )

    payload = _observation_payload(
        source_external_id=(
            f"source-{uuid.uuid4().hex}"
        ),
        asset_external_id=(
            f"index-{uuid.uuid4().hex}"
        ),
    )

    try:
        response = client.post(
            "/api/lineage/observations",
            json=payload,
        )

        assert response.status_code == 401
        assert (
            response.json()["detail"]["error"]["code"]
            == "API_KEY_002"
        )

    finally:
        client.close()


def test_lineage_ingestion_does_not_accept_browser_session():
    organization_id = (
        _create_organization(
            prefix="lineage-browser-auth"
        )
    )

    suffix = uuid.uuid4().hex[:12]
    email = (
        f"lineage-browser-{suffix}"
        "@example.test"
    )

    _create_user(
        organization_id=organization_id,
        email=email,
    )

    client = TestClient(
        app
    )

    try:
        _login(
            client,
            email=email,
        )

        response = client.post(
            "/api/lineage/observations",
            json=_observation_payload(
                source_external_id=(
                    f"source-{uuid.uuid4().hex}"
                ),
                asset_external_id=(
                    f"index-{uuid.uuid4().hex}"
                ),
            ),
        )

        assert response.status_code == 401
        assert (
            response.json()["detail"]["error"]["code"]
            == "API_KEY_002"
        )

    finally:
        client.close()
        _cleanup_organization(
            organization_id
        )


def test_lineage_ingestion_does_not_accept_bearer_token():
    client = TestClient(
        app
    )

    payload = _observation_payload(
        source_external_id=(
            f"source-{uuid.uuid4().hex}"
        ),
        asset_external_id=(
            f"index-{uuid.uuid4().hex}"
        ),
    )

    try:
        response = client.post(
            "/api/lineage/observations",
            headers={
                "Authorization":
                    "Bearer invalid-for-lineage",
            },
            json=payload,
        )

        assert response.status_code == 401
        assert (
            response.json()["detail"]["error"]["code"]
            == "API_KEY_002"
        )

    finally:
        client.close()


def test_lineage_ingestion_rejects_invalid_api_key():
    client = TestClient(
        app
    )

    payload = _observation_payload(
        source_external_id=(
            f"source-{uuid.uuid4().hex}"
        ),
        asset_external_id=(
            f"index-{uuid.uuid4().hex}"
        ),
    )

    try:
        response = client.post(
            "/api/lineage/observations",
            headers={
                "X-API-Key":
                    "sk-invalid",
            },
            json=payload,
        )

        assert response.status_code == 401
        assert (
            response.json()["detail"]["error"]["code"]
            == "API_KEY_003"
        )

    finally:
        client.close()


def test_lineage_ingestion_requires_write_permission():
    organization_id = (
        _create_organization(
            prefix="lineage-read-only"
        )
    )
    _key_id, raw_key = _create_api_key(
        organization_id=organization_id,
        write=False,
    )

    client = TestClient(
        app
    )

    try:
        response = client.post(
            "/api/lineage/observations",
            headers={
                "X-API-Key":
                    raw_key,
            },
            json=_observation_payload(
                source_external_id=(
                    f"source-{uuid.uuid4().hex}"
                ),
                asset_external_id=(
                    f"index-{uuid.uuid4().hex}"
                ),
            ),
        )

        assert response.status_code == 403
        assert (
            response.json()["detail"]["error"]["code"]
            == "API_KEY_004"
        )

    finally:
        client.close()
        _cleanup_organization(
            organization_id
        )


def test_lineage_ingestion_persists_under_api_key_tenant():
    organization_id = (
        _create_organization(
            prefix="lineage-write"
        )
    )
    _key_id, raw_key = _create_api_key(
        organization_id=organization_id,
        write=True,
    )

    source_external_id = (
        f"source-{uuid.uuid4().hex}"
    )
    asset_external_id = (
        f"index-{uuid.uuid4().hex}"
    )
    sequence = 11

    payload = _observation_payload(
        source_external_id=(
            source_external_id
        ),
        asset_external_id=(
            asset_external_id
        ),
        sequence=sequence,
    )

    client = TestClient(
        app
    )

    try:
        response = client.post(
            "/api/lineage/observations",
            headers={
                "X-API-Key":
                    raw_key,
            },
            json=payload,
        )

        assert response.status_code == 200

        body = response.json()

        assert body["created"] is True
        uuid.UUID(
            body["id"]
        )

        persisted = _load_observation(
            organization_id=(
                organization_id
            ),
            source_external_id=(
                source_external_id
            ),
            asset_external_id=(
                asset_external_id
            ),
            sequence=sequence,
        )

        assert persisted is not None
        assert (
            persisted.organization_id
            == organization_id
        )
        assert (
            persisted.snapshot_external_id
            == "snapshot-v1"
        )
        assert (
            persisted.revision_external_id
            == "index-revision-v1"
        )

    finally:
        client.close()
        _cleanup_organization(
            organization_id
        )


def test_runtime_lineage_capture_publishes_through_client_and_persists():
    organization_id = _create_organization(
        prefix="lineage-runtime-acquisition"
    )
    _key_id, raw_key = _create_api_key(
        organization_id=organization_id,
        write=True,
    )

    source_external_id = (
        f"source-{uuid.uuid4().hex}"
    )
    snapshot_external_id = (
        f"snapshot-{uuid.uuid4().hex}"
    )
    chunk_external_id = "chunk-runtime"
    chunk_revision_external_id = "chunk-runtime@v1"
    index_external_id = "index-runtime"
    index_revision_external_id = "index-runtime@v1"

    class DocumentLike:
        def __init__(self) -> None:
            self.id = chunk_external_id
            self.page_content = (
                "Refunds require approval."
            )
            self.metadata = {
                "source_external_id":
                    source_external_id,
                "snapshot_external_id":
                    snapshot_external_id,
                "chunk_revision_external_id":
                    chunk_revision_external_id,
            }

    captured = capture_langchain_index_materialization(
        documents=[DocumentLike()],
        index_external_id=index_external_id,
        index_revision_external_id=(
            index_revision_external_id
        ),
        sequence=1,
    )

    assert len(captured.observations) == 2

    app_client = TestClient(app)

    try:
        with SDEIntegrityClient(
            base_url="http://testserver",
            api_key=raw_key,
            timeout_seconds=5.0,
            transport=_FastAPITestTransport(
                app_client
            ),
        ) as lineage_client:
            publications = [
                lineage_client.publish_lineage_observation(
                    observation
                )
                for observation in captured.observations
            ]

        assert len(publications) == 2
        assert all(
            publication.created
            for publication in publications
        )

        chunk_persisted = _load_observation(
            organization_id=organization_id,
            source_external_id=source_external_id,
            asset_external_id=chunk_external_id,
            sequence=1,
        )
        index_persisted = _load_observation(
            organization_id=organization_id,
            source_external_id=source_external_id,
            asset_external_id=index_external_id,
            sequence=1,
        )

        assert chunk_persisted is not None
        assert index_persisted is not None

        assert (
            chunk_persisted.organization_id
            == organization_id
        )
        assert (
            chunk_persisted.revision_external_id
            == chunk_revision_external_id
        )

        assert (
            index_persisted.organization_id
            == organization_id
        )
        assert (
            index_persisted.snapshot_external_id
            == snapshot_external_id
        )
        assert (
            index_persisted.revision_external_id
            == index_revision_external_id
        )

        with SDEIntegrityClient(
            base_url="http://testserver",
            api_key=raw_key,
            timeout_seconds=5.0,
            transport=_FastAPITestTransport(
                app_client
            ),
        ) as lineage_client:
            replay = (
                lineage_client.publish_lineage_observation(
                    captured.observations[-1]
                )
            )

        assert replay.created is False

    finally:
        app_client.close()
        _cleanup_organization(
            organization_id
        )


def test_lineage_ingestion_is_idempotent_and_conflicts_fail_closed():
    organization_id = (
        _create_organization(
            prefix="lineage-replay"
        )
    )
    _key_id, raw_key = _create_api_key(
        organization_id=organization_id,
        write=True,
    )

    source_external_id = (
        f"source-{uuid.uuid4().hex}"
    )
    asset_external_id = (
        f"index-{uuid.uuid4().hex}"
    )

    payload = _observation_payload(
        source_external_id=(
            source_external_id
        ),
        asset_external_id=(
            asset_external_id
        ),
    )

    client = TestClient(
        app
    )

    try:
        first = client.post(
            "/api/lineage/observations",
            headers={
                "X-API-Key":
                    raw_key,
            },
            json=payload,
        )

        assert first.status_code == 200
        assert (
            first.json()["created"]
            is True
        )

        replay = client.post(
            "/api/lineage/observations",
            headers={
                "X-API-Key":
                    raw_key,
            },
            json=payload,
        )

        assert replay.status_code == 200
        assert (
            replay.json()["created"]
            is False
        )
        assert (
            replay.json()["id"]
            == first.json()["id"]
        )

        conflicting_payload = dict(
            payload
        )
        conflicting_payload[
            "snapshot_external_id"
        ] = "snapshot-conflict"

        conflict = client.post(
            "/api/lineage/observations",
            headers={
                "X-API-Key":
                    raw_key,
            },
            json=conflicting_payload,
        )

        assert conflict.status_code == 409
        assert (
            conflict.json()["detail"]["error"]["code"]
            == "LINEAGE_002"
        )

        persisted = _load_observation(
            organization_id=(
                organization_id
            ),
            source_external_id=(
                source_external_id
            ),
            asset_external_id=(
                asset_external_id
            ),
            sequence=7,
        )

        assert persisted is not None
        assert (
            persisted.snapshot_external_id
            == "snapshot-v1"
        )

    finally:
        client.close()
        _cleanup_organization(
            organization_id
        )


def test_lineage_ingestion_rejects_tenant_and_unknown_fields():
    organization_id = (
        _create_organization(
            prefix="lineage-extra-field"
        )
    )
    _key_id, raw_key = _create_api_key(
        organization_id=organization_id,
        write=True,
    )

    payload = _observation_payload(
        source_external_id=(
            f"source-{uuid.uuid4().hex}"
        ),
        asset_external_id=(
            f"index-{uuid.uuid4().hex}"
        ),
    )
    payload["organization_id"] = str(
        uuid.uuid4()
    )

    client = TestClient(
        app
    )

    try:
        response = client.post(
            "/api/lineage/observations",
            headers={
                "X-API-Key":
                    raw_key,
            },
            json=payload,
        )

        assert response.status_code == 422

    finally:
        client.close()
        _cleanup_organization(
            organization_id
        )


def test_lineage_ingestion_rejects_invalid_domain_observation():
    organization_id = (
        _create_organization(
            prefix="lineage-domain"
        )
    )
    _key_id, raw_key = _create_api_key(
        organization_id=organization_id,
        write=True,
    )

    payload = {
        "observation_kind":
            "consumption",
        "source_external_id":
            "source-consumption",
        "snapshot_external_id":
            "snapshot-consumption",
        "asset_external_id":
            "agent-consumption",
        "asset_kind":
            "agent",
        "revision_external_id":
            "agent-revision-v1",
        "sequence":
            1,
        "dependencies": [],
    }

    client = TestClient(
        app
    )

    try:
        response = client.post(
            "/api/lineage/observations",
            headers={
                "X-API-Key":
                    raw_key,
            },
            json=payload,
        )

        assert response.status_code == 422
        assert (
            response.json()["detail"]["error"]["code"]
            == "LINEAGE_001"
        )

    finally:
        client.close()
        _cleanup_organization(
            organization_id
        )


def test_langchain_materialization_capture_maps_through_public_lineage_api():
    langchain_documents = pytest.importorskip(
        "langchain_core.documents"
    )
    Document = langchain_documents.Document

    organization_id = _create_organization(
        prefix="lineage-langchain-adoption"
    )
    _key_id, raw_key = _create_api_key(
        organization_id=organization_id,
        write=True,
    )

    source_external_id = (
        f"source-{uuid.uuid4().hex}"
    )
    snapshot_external_id = (
        f"snapshot-{uuid.uuid4().hex}"
    )

    refund_chunk_asset = "chunk-refund"
    refund_chunk_revision = "chunk-refund@v1"

    shipping_chunk_asset = "chunk-shipping"
    shipping_chunk_revision = "chunk-shipping@v1"

    index_asset = "index-support"
    index_revision = "index-support@v1"

    documents = [
        Document(
            id=refund_chunk_asset,
            page_content=(
                "Refunds require approval."
            ),
            metadata={
                "source_external_id":
                    source_external_id,
                "snapshot_external_id":
                    snapshot_external_id,
                "chunk_revision_external_id":
                    refund_chunk_revision,
            },
        ),
        Document(
            id=shipping_chunk_asset,
            page_content=(
                "Shipping requires tracking."
            ),
            metadata={
                "source_external_id":
                    source_external_id,
                "snapshot_external_id":
                    snapshot_external_id,
                "chunk_revision_external_id":
                    shipping_chunk_revision,
            },
        ),
    ]

    captured = (
        capture_benchmark_langchain_index_materialization(
            documents=documents,
            index_external_id=index_asset,
            index_revision_external_id=index_revision,
            sequence=1,
        )
    )

    assert len(captured.events) == 3

    client = TestClient(
        app
    )

    headers = {
        "X-API-Key": raw_key,
    }

    try:
        for event in captured.events:
            payload = {
                "observation_kind":
                    "materialization",
                "source_external_id":
                    event.source_external_id,
                "snapshot_external_id":
                    event.snapshot_external_id,
                "asset_external_id":
                    event.asset_external_id,
                "asset_kind":
                    event.asset_kind,
                "revision_external_id":
                    event.revision_external_id,
                "sequence":
                    event.sequence,
                "dependencies": [
                    {
                        "asset_external_id":
                            dependency.asset_external_id,
                        "revision_external_id":
                            dependency.revision_external_id,
                        "dependency_type":
                            dependency.dependency_type,
                    }
                    for dependency
                    in event.dependencies
                ],
            }

            response = client.post(
                "/api/lineage/observations",
                headers=headers,
                json=payload,
            )

            assert (
                response.status_code
                == 200
            ), response.text
            assert (
                response.json()["created"]
                is True
            )

        db = SessionLocal()

        try:
            impact = analyze_lineage_impact(
                db,
                organization_id=organization_id,
                root=LineageRevisionIdentity(
                    source_external_id=(
                        source_external_id
                    ),
                    asset_external_id=(
                        refund_chunk_asset
                    ),
                    revision_external_id=(
                        refund_chunk_revision
                    ),
                ),
            )
        finally:
            db.close()

        expected_index = (
            LineageRevisionIdentity(
                source_external_id=(
                    source_external_id
                ),
                asset_external_id=(
                    index_asset
                ),
                revision_external_id=(
                    index_revision
                ),
            )
        )

        unrelated_chunk = (
            LineageRevisionIdentity(
                source_external_id=(
                    source_external_id
                ),
                asset_external_id=(
                    shipping_chunk_asset
                ),
                revision_external_id=(
                    shipping_chunk_revision
                ),
            )
        )

        assert impact.impacted_revisions == (
            expected_index,
        )

        assert len(impact.paths) == 1

        assert impact.paths[0].target == (
            expected_index
        )

        assert impact.paths[0].revisions == (
            LineageRevisionIdentity(
                source_external_id=(
                    source_external_id
                ),
                asset_external_id=(
                    refund_chunk_asset
                ),
                revision_external_id=(
                    refund_chunk_revision
                ),
            ),
            expected_index,
        )

        assert unrelated_chunk not in (
            impact.impacted_revisions
        )

    finally:
        client.close()
        _cleanup_organization(
            organization_id
        )


def test_langchain_consumer_capture_maps_through_public_lineage_api():
    langchain_documents = pytest.importorskip(
        "langchain_core.documents"
    )
    Document = langchain_documents.Document

    organization_id = _create_organization(
        prefix="lineage-langchain-consumer"
    )
    _key_id, raw_key = _create_api_key(
        organization_id=organization_id,
        write=True,
    )

    source_external_id = (
        f"source-{uuid.uuid4().hex}"
    )
    snapshot_external_id = (
        f"snapshot-{uuid.uuid4().hex}"
    )

    chunk_asset = "chunk-refund"
    chunk_revision = "chunk-refund@v1"
    agent_asset = "agent-support"
    agent_revision = "agent-support@run1"

    retrieved_document = Document(
        id=chunk_asset,
        page_content="Refunds require approval.",
        metadata={
            "source_external_id": source_external_id,
            "snapshot_external_id": snapshot_external_id,
            "chunk_revision_external_id": chunk_revision,
        },
    )

    binding = capture_langchain_consumer_binding(
        retrieved_documents=[
            retrieved_document,
        ],
        consumer_external_id=agent_asset,
        consumer_revision_external_id=agent_revision,
        consumer_asset_kind="agent",
        binding_snapshot_external_id=snapshot_external_id,
        sequence=1,
    )

    assert binding.event.asset_kind == "agent"
    assert binding.event.asset_external_id == agent_asset
    assert (
        binding.event.revision_external_id
        == agent_revision
    )
    assert len(binding.event.dependencies) == 1
    assert (
        binding.event.dependencies[0].asset_external_id
        == chunk_asset
    )
    assert (
        binding.event.dependencies[0].revision_external_id
        == chunk_revision
    )
    assert (
        binding.event.dependencies[0].dependency_type
        == "consumed"
    )

    client = TestClient(
        app
    )

    headers = {
        "X-API-Key": raw_key,
    }

    observations = (
        {
            "observation_kind": "materialization",
            "source_external_id": source_external_id,
            "snapshot_external_id": snapshot_external_id,
            "asset_external_id": chunk_asset,
            "asset_kind": "chunk",
            "revision_external_id": chunk_revision,
            "sequence": 1,
            "dependencies": [],
        },
        {
            "observation_kind": "consumption",
            "source_external_id": (
                binding.event.source_external_id
            ),
            "snapshot_external_id": (
                binding.event.snapshot_external_id
            ),
            "asset_external_id": (
                binding.event.asset_external_id
            ),
            "asset_kind": binding.event.asset_kind,
            "revision_external_id": (
                binding.event.revision_external_id
            ),
            "sequence": binding.event.sequence,
            "dependencies": [
                {
                    "asset_external_id": (
                        dependency.asset_external_id
                    ),
                    "revision_external_id": (
                        dependency.revision_external_id
                    ),
                    "dependency_type": (
                        dependency.dependency_type
                    ),
                }
                for dependency in binding.event.dependencies
            ],
        },
    )

    try:
        for observation in observations:
            response = client.post(
                "/api/lineage/observations",
                headers=headers,
                json=observation,
            )

            assert response.status_code == 200, response.text
            assert response.json()["created"] is True

        db = SessionLocal()

        try:
            impact = analyze_lineage_impact(
                db,
                organization_id=organization_id,
                root=LineageRevisionIdentity(
                    source_external_id=source_external_id,
                    asset_external_id=chunk_asset,
                    revision_external_id=chunk_revision,
                ),
            )
        finally:
            db.close()

        expected_agent = LineageRevisionIdentity(
            source_external_id=source_external_id,
            asset_external_id=agent_asset,
            revision_external_id=agent_revision,
        )

        assert impact.impacted_revisions == (
            expected_agent,
        )

        assert len(impact.paths) == 1

        assert impact.paths[0].target == (
            expected_agent
        )

        assert impact.paths[0].revisions == (
            LineageRevisionIdentity(
                source_external_id=source_external_id,
                asset_external_id=chunk_asset,
                revision_external_id=chunk_revision,
            ),
            expected_agent,
        )
    finally:
        client.close()
        _cleanup_organization(
            organization_id
        )


def test_lineage_ingestion_preserves_exact_causal_chain_for_impact_analysis():
    organization_id = _create_organization(
        prefix="lineage-adoption"
    )
    _key_id, raw_key = _create_api_key(
        organization_id=organization_id,
        write=True,
    )

    source_external_id = (
        f"source-{uuid.uuid4().hex}"
    )
    snapshot_external_id = (
        f"snapshot-{uuid.uuid4().hex}"
    )

    refund_chunk_asset = "chunk-refund"
    refund_chunk_revision = "chunk-refund@v1"

    shipping_chunk_asset = "chunk-shipping"
    shipping_chunk_revision = "chunk-shipping@v1"

    support_index_asset = "index-support"
    support_index_revision = "index-support@v1"

    support_agent_asset = "agent-support"
    support_agent_revision = "agent-support@run1"

    unrelated_chunk_asset = "chunk-escalation"
    unrelated_chunk_revision = "chunk-escalation@v1"

    unrelated_index_asset = "index-escalation"
    unrelated_index_revision = "index-escalation@v1"

    client = TestClient(
        app
    )

    headers = {
        "X-API-Key": raw_key,
    }

    observations = (
        {
            "observation_kind": "materialization",
            "source_external_id": source_external_id,
            "snapshot_external_id": snapshot_external_id,
            "asset_external_id": refund_chunk_asset,
            "asset_kind": "chunk",
            "revision_external_id": refund_chunk_revision,
            "sequence": 1,
            "dependencies": [],
        },
        {
            "observation_kind": "materialization",
            "source_external_id": source_external_id,
            "snapshot_external_id": snapshot_external_id,
            "asset_external_id": shipping_chunk_asset,
            "asset_kind": "chunk",
            "revision_external_id": shipping_chunk_revision,
            "sequence": 1,
            "dependencies": [],
        },
        {
            "observation_kind": "materialization",
            "source_external_id": source_external_id,
            "snapshot_external_id": snapshot_external_id,
            "asset_external_id": support_index_asset,
            "asset_kind": "index",
            "revision_external_id": support_index_revision,
            "sequence": 1,
            "dependencies": [
                {
                    "asset_external_id": refund_chunk_asset,
                    "revision_external_id": refund_chunk_revision,
                    "dependency_type": "indexed_from",
                },
                {
                    "asset_external_id": shipping_chunk_asset,
                    "revision_external_id": shipping_chunk_revision,
                    "dependency_type": "indexed_from",
                },
            ],
        },
        {
            "observation_kind": "consumption",
            "source_external_id": source_external_id,
            "snapshot_external_id": snapshot_external_id,
            "asset_external_id": support_agent_asset,
            "asset_kind": "agent",
            "revision_external_id": support_agent_revision,
            "sequence": 1,
            "dependencies": [
                {
                    "asset_external_id": support_index_asset,
                    "revision_external_id": support_index_revision,
                    "dependency_type": "retrieved_from",
                },
            ],
        },
        {
            "observation_kind": "materialization",
            "source_external_id": source_external_id,
            "snapshot_external_id": snapshot_external_id,
            "asset_external_id": unrelated_chunk_asset,
            "asset_kind": "chunk",
            "revision_external_id": unrelated_chunk_revision,
            "sequence": 1,
            "dependencies": [],
        },
        {
            "observation_kind": "materialization",
            "source_external_id": source_external_id,
            "snapshot_external_id": snapshot_external_id,
            "asset_external_id": unrelated_index_asset,
            "asset_kind": "index",
            "revision_external_id": unrelated_index_revision,
            "sequence": 1,
            "dependencies": [
                {
                    "asset_external_id": unrelated_chunk_asset,
                    "revision_external_id": unrelated_chunk_revision,
                    "dependency_type": "indexed_from",
                },
            ],
        },
    )

    try:
        for observation in observations:
            response = client.post(
                "/api/lineage/observations",
                headers=headers,
                json=observation,
            )

            assert response.status_code == 200, response.text
            assert response.json()["created"] is True

        db = SessionLocal()

        try:
            impact = analyze_lineage_impact(
                db,
                organization_id=organization_id,
                root=LineageRevisionIdentity(
                    source_external_id=source_external_id,
                    asset_external_id=refund_chunk_asset,
                    revision_external_id=refund_chunk_revision,
                ),
            )
        finally:
            db.close()

        support_index = LineageRevisionIdentity(
            source_external_id=source_external_id,
            asset_external_id=support_index_asset,
            revision_external_id=support_index_revision,
        )
        support_agent = LineageRevisionIdentity(
            source_external_id=source_external_id,
            asset_external_id=support_agent_asset,
            revision_external_id=support_agent_revision,
        )
        unrelated_index = LineageRevisionIdentity(
            source_external_id=source_external_id,
            asset_external_id=unrelated_index_asset,
            revision_external_id=unrelated_index_revision,
        )

        assert impact.impacted_revisions == (
            support_agent,
            support_index,
        )

        assert len(impact.paths) == 2

        path_by_target = {
            path.target: path.revisions
            for path in impact.paths
        }

        root = LineageRevisionIdentity(
            source_external_id=source_external_id,
            asset_external_id=refund_chunk_asset,
            revision_external_id=refund_chunk_revision,
        )

        assert path_by_target[support_index] == (
            root,
            support_index,
        )
        assert path_by_target[support_agent] == (
            root,
            support_index,
            support_agent,
        )

        assert unrelated_index not in (
            impact.impacted_revisions
        )
    finally:
        client.close()
        _cleanup_organization(
            organization_id
        )


def test_lineage_impact_api_returns_exact_causal_impact() -> None:
    organization_id = _create_organization(
        prefix="lineage-impact-api"
    )
    _, raw_key = _create_api_key(
        organization_id=organization_id,
        write=True,
    )

    client = TestClient(app)

    observations = [
        {
            "observation_kind": "materialization",
            "source_external_id": "policy-source",
            "snapshot_external_id": "snapshot-v1",
            "asset_external_id": "chunk-refund",
            "asset_kind": "chunk",
            "revision_external_id": "chunk-refund@v1",
            "sequence": 1,
            "dependencies": [],
        },
        {
            "observation_kind": "materialization",
            "source_external_id": "policy-source",
            "snapshot_external_id": "snapshot-v1",
            "asset_external_id": "index-support",
            "asset_kind": "index",
            "revision_external_id": "index-support@v1",
            "sequence": 1,
            "dependencies": [
                {
                    "asset_external_id": "chunk-refund",
                    "revision_external_id": "chunk-refund@v1",
                    "dependency_type": "indexed_from",
                }
            ],
        },
        {
            "observation_kind": "consumption",
            "source_external_id": "policy-source",
            "snapshot_external_id": "snapshot-v1",
            "asset_external_id": "agent-support",
            "asset_kind": "agent",
            "revision_external_id": "agent-support@run-1",
            "sequence": 1,
            "dependencies": [
                {
                    "asset_external_id": "index-support",
                    "revision_external_id": "index-support@v1",
                    "dependency_type": "consumed",
                }
            ],
        },
        {
            "observation_kind": "materialization",
            "source_external_id": "policy-source",
            "snapshot_external_id": "snapshot-v1",
            "asset_external_id": "chunk-unrelated",
            "asset_kind": "chunk",
            "revision_external_id": "chunk-unrelated@v1",
            "sequence": 1,
            "dependencies": [],
        },
    ]

    try:
        for observation in observations:
            response = client.post(
                "/api/lineage/observations",
                headers={
                    "X-API-Key": raw_key,
                },
                json=observation,
            )

            assert response.status_code == 200, response.text

        response = client.post(
            "/api/lineage/impact",
            headers={
                "X-API-Key": raw_key,
            },
            json={
                "source_external_id": "policy-source",
                "asset_external_id": "chunk-refund",
                "revision_external_id": "chunk-refund@v1",
            },
        )

        assert response.status_code == 200, response.text

        payload = response.json()

        assert payload["root"] == {
            "source_external_id": "policy-source",
            "asset_external_id": "chunk-refund",
            "revision_external_id": "chunk-refund@v1",
        }

        assert payload["impacted_revisions"] == [
            {
                "source_external_id": "policy-source",
                "asset_external_id": "agent-support",
                "revision_external_id": "agent-support@run-1",
            },
            {
                "source_external_id": "policy-source",
                "asset_external_id": "index-support",
                "revision_external_id": "index-support@v1",
            },
        ]

        paths_by_target = {
            path["target"]["asset_external_id"]: path
            for path in payload["paths"]
        }

        assert set(paths_by_target) == {
            "agent-support",
            "index-support",
        }

        assert [
            revision["asset_external_id"]
            for revision
            in paths_by_target["index-support"]["revisions"]
        ] == [
            "chunk-refund",
            "index-support",
        ]

        assert [
            revision["asset_external_id"]
            for revision
            in paths_by_target["agent-support"]["revisions"]
        ] == [
            "chunk-refund",
            "index-support",
            "agent-support",
        ]

        assert all(
            revision["asset_external_id"]
            != "chunk-unrelated"
            for revision
            in payload["impacted_revisions"]
        )
    finally:
        _cleanup_organization(
            organization_id
        )


def test_lineage_impact_api_requires_read_permission() -> None:
    organization_id = _create_organization(
        prefix="lineage-impact-read"
    )
    _, raw_key = _create_api_key(
        organization_id=organization_id,
        write=True,
    )

    db = SessionLocal()

    try:
        api_key = (
            db.query(ApiKey)
            .filter(
                ApiKey.organization_id
                == organization_id
            )
            .one()
        )
        api_key.permissions = {
            "read": False,
            "write": True,
        }
        db.commit()
    finally:
        db.close()

    client = TestClient(app)

    try:
        response = client.post(
            "/api/lineage/impact",
            headers={
                "X-API-Key": raw_key,
            },
            json={
                "source_external_id": "policy-source",
                "asset_external_id": "chunk-refund",
                "revision_external_id": "chunk-refund@v1",
            },
        )

        assert response.status_code == 403, response.text
    finally:
        _cleanup_organization(
            organization_id
        )


def _execution_target_binding_payload(
    *,
    source_external_id: str,
    asset_external_id: str,
    binding_revision: int = 1,
    configuration: dict[str, str] | None = None,
) -> dict:
    return {
        "source_external_id": source_external_id,
        "asset_external_id": asset_external_id,
        "binding_revision": binding_revision,
        "provider": "pgvector",
        "target_kind": "collection",
        "configuration": (
            configuration
            if configuration is not None
            else {
                "database": "knowledge",
                "collection": "support",
            }
        ),
    }


def _load_execution_target_binding(
    *,
    organization_id: uuid.UUID,
    source_external_id: str,
    asset_external_id: str,
    binding_revision: int,
) -> LineageExecutionTargetBindingRecord | None:
    db = SessionLocal()

    try:
        return (
            db.query(LineageExecutionTargetBindingRecord)
            .filter(
                LineageExecutionTargetBindingRecord.organization_id
                == organization_id,
                LineageExecutionTargetBindingRecord.source_external_id
                == source_external_id,
                LineageExecutionTargetBindingRecord.asset_external_id
                == asset_external_id,
                LineageExecutionTargetBindingRecord.binding_revision
                == binding_revision,
            )
            .one_or_none()
        )
    finally:
        db.close()


def test_execution_target_binding_publication_requires_api_key():
    client = TestClient(
        app
    )

    payload = _execution_target_binding_payload(
        source_external_id=f"source-{uuid.uuid4().hex}",
        asset_external_id=f"index-{uuid.uuid4().hex}",
    )

    try:
        response = client.post(
            "/api/lineage/execution-target-bindings",
            json=payload,
        )

        assert response.status_code == 401
        assert (
            response.json()["detail"]["error"]["code"]
            == "API_KEY_002"
        )

    finally:
        client.close()


def test_execution_target_binding_publication_requires_write_permission():
    organization_id = _create_organization(
        prefix="lineage-binding-read-only"
    )
    _key_id, raw_key = _create_api_key(
        organization_id=organization_id,
        write=False,
    )

    client = TestClient(
        app
    )

    try:
        response = client.post(
            "/api/lineage/execution-target-bindings",
            headers={
                "X-API-Key": raw_key,
            },
            json=_execution_target_binding_payload(
                source_external_id=f"source-{uuid.uuid4().hex}",
                asset_external_id=f"index-{uuid.uuid4().hex}",
            ),
        )

        assert response.status_code == 403
        assert (
            response.json()["detail"]["error"]["code"]
            == "API_KEY_004"
        )

    finally:
        client.close()
        _cleanup_organization(
            organization_id
        )


def test_execution_target_binding_publication_persists_under_api_key_tenant():
    organization_id = _create_organization(
        prefix="lineage-binding-write"
    )
    _key_id, raw_key = _create_api_key(
        organization_id=organization_id,
        write=True,
    )

    source_external_id = f"source-{uuid.uuid4().hex}"
    asset_external_id = f"index-{uuid.uuid4().hex}"

    client = TestClient(
        app
    )

    try:
        response = client.post(
            "/api/lineage/execution-target-bindings",
            headers={
                "X-API-Key": raw_key,
            },
            json=_execution_target_binding_payload(
                source_external_id=f"  {source_external_id}  ",
                asset_external_id=f"  {asset_external_id}  ",
                configuration={
                    "database": "knowledge",
                    "collection": "support",
                },
            ),
        )

        assert response.status_code == 200

        body = response.json()

        assert body["created"] is True
        uuid.UUID(body["id"])
        assert body["source_external_id"] == source_external_id
        assert body["asset_external_id"] == asset_external_id
        assert body["binding_revision"] == 1
        assert body["provider"] == "pgvector"
        assert body["target_kind"] == "collection"
        assert body["configuration"] == {
            "database": "knowledge",
            "collection": "support",
        }

        persisted = _load_execution_target_binding(
            organization_id=organization_id,
            source_external_id=source_external_id,
            asset_external_id=asset_external_id,
            binding_revision=1,
        )

        assert persisted is not None
        assert persisted.organization_id == organization_id
        assert str(persisted.id) == body["id"]

    finally:
        client.close()
        _cleanup_organization(
            organization_id
        )


def test_execution_target_binding_publication_is_idempotent_and_conflicts_fail_closed():
    organization_id = _create_organization(
        prefix="lineage-binding-replay"
    )
    _key_id, raw_key = _create_api_key(
        organization_id=organization_id,
        write=True,
    )

    source_external_id = f"source-{uuid.uuid4().hex}"
    asset_external_id = f"index-{uuid.uuid4().hex}"

    payload = _execution_target_binding_payload(
        source_external_id=source_external_id,
        asset_external_id=asset_external_id,
    )

    client = TestClient(
        app
    )

    try:
        first = client.post(
            "/api/lineage/execution-target-bindings",
            headers={
                "X-API-Key": raw_key,
            },
            json=payload,
        )

        assert first.status_code == 200
        assert first.json()["created"] is True

        replay = client.post(
            "/api/lineage/execution-target-bindings",
            headers={
                "X-API-Key": raw_key,
            },
            json=payload,
        )

        assert replay.status_code == 200
        assert replay.json()["created"] is False
        assert replay.json()["id"] == first.json()["id"]

        conflicting_payload = {
            **payload,
            "configuration": {
                "database": "knowledge",
                "collection": "different",
            },
        }

        conflict = client.post(
            "/api/lineage/execution-target-bindings",
            headers={
                "X-API-Key": raw_key,
            },
            json=conflicting_payload,
        )

        assert conflict.status_code == 409
        assert (
            conflict.json()["detail"]["error"]["code"]
            == "LINEAGE_006"
        )

        persisted = _load_execution_target_binding(
            organization_id=organization_id,
            source_external_id=source_external_id,
            asset_external_id=asset_external_id,
            binding_revision=1,
        )

        assert persisted is not None
        assert persisted.configuration == {
            "database": "knowledge",
            "collection": "support",
        }

    finally:
        client.close()
        _cleanup_organization(
            organization_id
        )


def test_execution_target_binding_publication_requires_contiguous_revisions():
    organization_id = _create_organization(
        prefix="lineage-binding-gap"
    )
    _key_id, raw_key = _create_api_key(
        organization_id=organization_id,
        write=True,
    )

    source_external_id = f"source-{uuid.uuid4().hex}"
    asset_external_id = f"index-{uuid.uuid4().hex}"

    client = TestClient(
        app
    )

    try:
        first = client.post(
            "/api/lineage/execution-target-bindings",
            headers={
                "X-API-Key": raw_key,
            },
            json=_execution_target_binding_payload(
                source_external_id=source_external_id,
                asset_external_id=asset_external_id,
                binding_revision=1,
            ),
        )

        assert first.status_code == 200

        gap = client.post(
            "/api/lineage/execution-target-bindings",
            headers={
                "X-API-Key": raw_key,
            },
            json=_execution_target_binding_payload(
                source_external_id=source_external_id,
                asset_external_id=asset_external_id,
                binding_revision=3,
            ),
        )

        assert gap.status_code == 409
        assert (
            gap.json()["detail"]["error"]["code"]
            == "LINEAGE_007"
        )

        second = client.post(
            "/api/lineage/execution-target-bindings",
            headers={
                "X-API-Key": raw_key,
            },
            json=_execution_target_binding_payload(
                source_external_id=source_external_id,
                asset_external_id=asset_external_id,
                binding_revision=2,
                configuration={
                    "database": "knowledge",
                    "collection": "support-v2",
                },
            ),
        )

        assert second.status_code == 200
        assert second.json()["created"] is True
        assert second.json()["binding_revision"] == 2

    finally:
        client.close()
        _cleanup_organization(
            organization_id
        )


def test_execution_target_binding_publication_rejects_secret_configuration():
    organization_id = _create_organization(
        prefix="lineage-binding-secret"
    )
    _key_id, raw_key = _create_api_key(
        organization_id=organization_id,
        write=True,
    )

    client = TestClient(
        app
    )

    try:
        response = client.post(
            "/api/lineage/execution-target-bindings",
            headers={
                "X-API-Key": raw_key,
            },
            json=_execution_target_binding_payload(
                source_external_id=f"source-{uuid.uuid4().hex}",
                asset_external_id=f"index-{uuid.uuid4().hex}",
                configuration={
                    "collection": "support",
                    "password": "must-not-cross-control-plane",
                },
            ),
        )

        assert response.status_code == 422
        assert (
            response.json()["detail"]["error"]["code"]
            == "LINEAGE_005"
        )

    finally:
        client.close()
        _cleanup_organization(
            organization_id
        )


def test_execution_target_binding_publication_rejects_tenant_and_unknown_fields():
    organization_id = _create_organization(
        prefix="lineage-binding-extra-field"
    )
    _key_id, raw_key = _create_api_key(
        organization_id=organization_id,
        write=True,
    )

    base_payload = _execution_target_binding_payload(
        source_external_id=f"source-{uuid.uuid4().hex}",
        asset_external_id=f"index-{uuid.uuid4().hex}",
    )

    client = TestClient(
        app
    )

    try:
        tenant_payload = {
            **base_payload,
            "organization_id": str(uuid.uuid4()),
        }

        tenant_response = client.post(
            "/api/lineage/execution-target-bindings",
            headers={
                "X-API-Key": raw_key,
            },
            json=tenant_payload,
        )

        assert tenant_response.status_code == 422

        unknown_payload = {
            **base_payload,
            "connection_url": "postgresql://must-not-be-accepted",
        }

        unknown_response = client.post(
            "/api/lineage/execution-target-bindings",
            headers={
                "X-API-Key": raw_key,
            },
            json=unknown_payload,
        )

        assert unknown_response.status_code == 422

    finally:
        client.close()
        _cleanup_organization(
            organization_id
        )


def test_execution_target_binding_publication_is_tenant_isolated():
    first_organization_id = _create_organization(
        prefix="lineage-binding-tenant-a"
    )
    second_organization_id = _create_organization(
        prefix="lineage-binding-tenant-b"
    )

    _first_key_id, first_raw_key = _create_api_key(
        organization_id=first_organization_id,
        write=True,
    )
    _second_key_id, second_raw_key = _create_api_key(
        organization_id=second_organization_id,
        write=True,
    )

    source_external_id = f"source-{uuid.uuid4().hex}"
    asset_external_id = f"index-{uuid.uuid4().hex}"

    payload = _execution_target_binding_payload(
        source_external_id=source_external_id,
        asset_external_id=asset_external_id,
    )

    client = TestClient(
        app
    )

    try:
        first = client.post(
            "/api/lineage/execution-target-bindings",
            headers={
                "X-API-Key": first_raw_key,
            },
            json=payload,
        )
        second = client.post(
            "/api/lineage/execution-target-bindings",
            headers={
                "X-API-Key": second_raw_key,
            },
            json=payload,
        )

        assert first.status_code == 200
        assert second.status_code == 200
        assert first.json()["created"] is True
        assert second.json()["created"] is True
        assert first.json()["id"] != second.json()["id"]

        first_persisted = _load_execution_target_binding(
            organization_id=first_organization_id,
            source_external_id=source_external_id,
            asset_external_id=asset_external_id,
            binding_revision=1,
        )
        second_persisted = _load_execution_target_binding(
            organization_id=second_organization_id,
            source_external_id=source_external_id,
            asset_external_id=asset_external_id,
            binding_revision=1,
        )

        assert first_persisted is not None
        assert second_persisted is not None
        assert first_persisted.organization_id == first_organization_id
        assert second_persisted.organization_id == second_organization_id

    finally:
        client.close()
        _cleanup_organization(
            first_organization_id
        )
        _cleanup_organization(
            second_organization_id
        )
