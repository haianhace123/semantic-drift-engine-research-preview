from __future__ import annotations

from datetime import datetime, timedelta
import logging
import os
import secrets
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, event

from src.core.database import SessionLocal, engine
from src.core.security import token_hash
from src.domain.lineage_execution_target import (
    LineageExecutionTargetBinding,
    LineageExecutionTargetIdentity,
)
from src.domain.lineage_invalidation import (
    LineageInvalidationDecision,
    LineageInvalidationPolicyIdentity,
)
from src.domain.lineage_observation import (
    LineageObservation,
    LineageRevisionReference,
)
from src.domain.lineage_revision import LineageRevisionIdentity
from src.integrations.integrity_client import IntegrityRevision, SDEIntegrityClient
from src.integrations.langchain.context_integrity import (
    query_langchain_context_integrity,
)
from src.integrations.langchain.lineage import (
    CHUNK_REVISION_EXTERNAL_ID_KEY,
    SOURCE_EXTERNAL_ID_KEY,
)
from src.main import app
from src.models import ApiKey
from src.models.lineage_execution_target_binding import (
    LineageExecutionTargetBindingRecord,
)
from src.models.lineage_invalidation_decision import (
    LineageInvalidationDecisionRecord,
)
from src.models.lineage_invalidation_execution_attempt import (
    LineageInvalidationExecutionAttemptRecord,
)
from src.models.lineage_invalidation_execution_intent import (
    LineageInvalidationExecutionIntentRecord,
)
from src.models.lineage_invalidation_execution_result import (
    LineageInvalidationExecutionResultRecord,
)
from src.models.lineage_invalidation_execution_verification import (
    LineageInvalidationExecutionVerificationRecord,
)
from src.models.lineage_invalidation_policy_activation import (
    LineageInvalidationPolicyActivationRecord,
)
from src.models.lineage_observation import LineageObservationRecord
from src.models.organization import Organization
from src.services.lineage_execution_target_binding_service import (
    persist_lineage_execution_target_binding,
)
from src.services.lineage_invalidation_execution_claim_service import (
    claim_lineage_invalidation_execution,
)
from src.services.lineage_invalidation_execution_intent_service import (
    authorize_lineage_invalidation_execution,
)
from src.services.lineage_invalidation_execution_result_service import (
    persist_lineage_invalidation_execution_result,
)
from src.services.lineage_invalidation_execution_verification_service import (
    persist_lineage_invalidation_execution_verification,
)
from src.services.lineage_invalidation_persistence_service import (
    persist_lineage_invalidation_decision,
)
from src.services.lineage_invalidation_policy_activation_service import (
    activate_lineage_invalidation_policy,
)
from src.services.lineage_observation_persistence_service import (
    persist_lineage_observation,
)
from src.services.lineage_target_integrity_service import (
    assess_lineage_context_integrity,
    assess_lineage_target_integrity,
)


SENSITIVE_TELEMETRY_FIELDS = {
    "organization_id",
    "source_external_id",
    "asset_external_id",
    "revision_external_id",
    "decision_id",
    "api_key",
}


def _integrity_telemetry_records(caplog):
    return [
        record
        for record in caplog.records
        if record.name == "src.api.routes.lineage"
        and record.getMessage() == "integrity_assessment_completed"
    ]


def _assert_integrity_telemetry_is_identifier_free(record) -> None:
    assert SENSITIVE_TELEMETRY_FIELDS.isdisjoint(record.__dict__)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


def _make_organization(db) -> Organization:
    suffix = uuid.uuid4().hex[:12]
    organization = Organization(
        id=uuid.uuid4(),
        name=f"lineage-control-plane-api-{suffix}",
        slug=f"lineage-control-plane-api-{suffix}",
        billing_email=f"{suffix}@example.com",
    )
    db.add(organization)
    db.flush()
    return organization


def _create_api_key(
    db,
    *,
    organization_id: uuid.UUID,
    read: bool = True,
    write: bool = False,
) -> str:
    raw_key = f"sk-{secrets.token_urlsafe(32)}"
    api_key = ApiKey(
        organization_id=organization_id,
        name="lineage-control-plane-api",
        key_hash=token_hash(raw_key),
        permissions={
            "read": read,
            "write": write,
        },
        is_active=True,
    )
    db.add(api_key)
    db.flush()
    return raw_key


def _revision(
    asset_external_id: str,
    revision_external_id: str,
    *,
    source_external_id: str = "source-a",
) -> LineageRevisionIdentity:
    return LineageRevisionIdentity(
        source_external_id=source_external_id,
        asset_external_id=asset_external_id,
        revision_external_id=revision_external_id,
    )


def _policy() -> LineageInvalidationPolicyIdentity:
    return LineageInvalidationPolicyIdentity(
        policy_external_id="rag-invalidation",
        revision=7,
    )


def _decision(
    *,
    root: LineageRevisionIdentity | None = None,
    target: LineageRevisionIdentity | None = None,
) -> LineageInvalidationDecision:
    root = root or _revision(
        "chunk-a",
        "chunk-a-r3",
    )
    target = target or _revision(
        "index-a",
        "index-a-r7",
    )

    return LineageInvalidationDecision(
        root=root,
        target=target,
        policy=_policy(),
        disposition="invalidate",
        reason_code="causal_dependency_changed",
        causal_path=(
            root,
            target,
        ),
    )


def _materialization(
    *,
    revision_external_id: str = "index-a-r7",
    sequence: int = 7,
    dependencies: tuple[LineageRevisionReference, ...] | None = None,
) -> LineageObservation:
    return LineageObservation(
        observation_kind="materialization",
        source_external_id="source-a",
        snapshot_external_id=f"snapshot-{sequence}",
        asset_external_id="index-a",
        asset_kind="index",
        revision_external_id=revision_external_id,
        sequence=sequence,
        dependencies=(
            dependencies
            if dependencies is not None
            else (
                LineageRevisionReference(
                    asset_external_id="chunk-a",
                    revision_external_id="chunk-a-r3",
                    dependency_type="indexed_from",
                ),
            )
        ),
    )


def _chunk_materialization() -> LineageObservation:
    return LineageObservation(
        observation_kind="materialization",
        source_external_id="source-a",
        snapshot_external_id="chunk-snapshot-3",
        asset_external_id="chunk-a",
        asset_kind="chunk",
        revision_external_id="chunk-a-r3",
        sequence=3,
        dependencies=(),
    )


def _binding() -> LineageExecutionTargetBinding:
    return LineageExecutionTargetBinding(
        identity=LineageExecutionTargetIdentity(
            source_external_id="source-a",
            asset_external_id="index-a",
        ),
        binding_revision=1,
        provider="pgvector",
        target_kind="langchain_pgvector_causal_chunk",
        configuration={
            "connection_ref": "test-downstream",
            "collection_name": "rag-index",
        },
    )


def _persist_decision(
    db,
    *,
    organization_id: uuid.UUID,
    decision: LineageInvalidationDecision | None = None,
):
    return persist_lineage_invalidation_decision(
        db,
        organization_id=organization_id,
        decision=decision or _decision(),
    )


def _establish_execution_authority(
    db,
    *,
    organization_id: uuid.UUID,
) -> None:
    activate_lineage_invalidation_policy(
        db,
        organization_id=organization_id,
        policy=_policy(),
    )
    persist_lineage_observation(
        db,
        organization_id=organization_id,
        observation=_materialization(),
    )
    persist_lineage_observation(
        db,
        organization_id=organization_id,
        observation=_chunk_materialization(),
    )
    persist_lineage_execution_target_binding(
        db,
        organization_id=organization_id,
        binding=_binding(),
    )


def _establish_execution_result(
    db,
    *,
    organization_id: uuid.UUID,
    outcome: str = "succeeded",
):
    persisted = _persist_decision(
        db,
        organization_id=organization_id,
    )
    _establish_execution_authority(
        db,
        organization_id=organization_id,
    )
    db.commit()

    intent = authorize_lineage_invalidation_execution(
        db,
        organization_id=organization_id,
        decision_record_id=persisted.record.id,
    ).record
    db.commit()

    attempt = claim_lineage_invalidation_execution(
        db,
        organization_id=organization_id,
        intent_id=intent.id,
        lease_seconds=300,
    ).record
    db.commit()

    result = persist_lineage_invalidation_execution_result(
        db,
        organization_id=organization_id,
        attempt_id=attempt.id,
        outcome=outcome,
    ).record
    db.commit()

    return persisted.record, result


def _cleanup(organization_ids: list[uuid.UUID]) -> None:
    if not organization_ids:
        return

    db = SessionLocal()

    try:
        db.rollback()
        db.execute(
            delete(LineageInvalidationExecutionVerificationRecord).where(
                LineageInvalidationExecutionVerificationRecord.organization_id.in_(
                    organization_ids
                )
            )
        )
        db.execute(
            delete(LineageInvalidationExecutionResultRecord).where(
                LineageInvalidationExecutionResultRecord.organization_id.in_(
                    organization_ids
                )
            )
        )
        db.execute(
            delete(LineageInvalidationExecutionAttemptRecord).where(
                LineageInvalidationExecutionAttemptRecord.organization_id.in_(
                    organization_ids
                )
            )
        )
        db.execute(
            delete(LineageInvalidationExecutionIntentRecord).where(
                LineageInvalidationExecutionIntentRecord.organization_id.in_(
                    organization_ids
                )
            )
        )
        db.execute(
            delete(LineageInvalidationDecisionRecord).where(
                LineageInvalidationDecisionRecord.organization_id.in_(
                    organization_ids
                )
            )
        )
        db.execute(
            delete(LineageInvalidationPolicyActivationRecord).where(
                LineageInvalidationPolicyActivationRecord.organization_id.in_(
                    organization_ids
                )
            )
        )
        db.execute(
            delete(LineageObservationRecord).where(
                LineageObservationRecord.organization_id.in_(
                    organization_ids
                )
            )
        )
        db.execute(
            delete(LineageExecutionTargetBindingRecord).where(
                LineageExecutionTargetBindingRecord.organization_id.in_(
                    organization_ids
                )
            )
        )
        db.execute(
            delete(ApiKey).where(
                ApiKey.organization_id.in_(organization_ids)
            )
        )
        db.execute(
            delete(Organization).where(
                Organization.id.in_(organization_ids)
            )
        )
        db.commit()
    finally:
        db.close()


def test_invalidation_decision_query_requires_api_key() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/lineage/invalidation-decisions/query",
        json={
            "source_external_id": "source-a",
            "asset_external_id": "chunk-a",
            "revision_external_id": "chunk-a-r3",
        },
    )

    assert response.status_code == 401


def test_invalidation_decision_query_requires_read_permission() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        raw_key = _create_api_key(
            db,
            organization_id=organization.id,
            read=False,
            write=True,
        )
        _persist_decision(
            db,
            organization_id=organization.id,
        )
        db.commit()

        client = TestClient(app)
        response = client.post(
            "/api/lineage/invalidation-decisions/query",
            json={
                "source_external_id": "source-a",
                "asset_external_id": "chunk-a",
                "revision_external_id": "chunk-a-r3",
            },
            headers={
                "X-API-Key": raw_key,
            },
        )

        assert response.status_code == 403
    finally:
        db.close()
        _cleanup(organization_ids)


def test_invalidation_decision_query_returns_empty_collection_for_unknown_root() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        raw_key = _create_api_key(
            db,
            organization_id=organization.id,
        )
        _persist_decision(
            db,
            organization_id=organization.id,
        )
        db.commit()

        client = TestClient(app)
        response = client.post(
            "/api/lineage/invalidation-decisions/query",
            json={
                "source_external_id": "source-a",
                "asset_external_id": "unknown",
                "revision_external_id": "unknown-r1",
            },
            headers={
                "X-API-Key": raw_key,
            },
        )

        assert response.status_code == 200
        assert response.json() == {
            "root": {
                "source_external_id": "source-a",
                "asset_external_id": "unknown",
                "revision_external_id": "unknown-r1",
            },
            "decisions": [],
        }
    finally:
        db.close()
        _cleanup(organization_ids)


def test_invalidation_decision_query_returns_exact_root_decisions_in_order() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        raw_key = _create_api_key(
            db,
            organization_id=organization.id,
        )
        root = _revision(
            "chunk-a",
            "chunk-a-r3",
        )
        first = _persist_decision(
            db,
            organization_id=organization.id,
            decision=_decision(
                root=root,
                target=_revision(
                    "index-a",
                    "index-a-r7",
                ),
            ),
        )
        second = _persist_decision(
            db,
            organization_id=organization.id,
            decision=_decision(
                root=root,
                target=_revision(
                    "index-b",
                    "index-b-r4",
                ),
            ),
        )
        _persist_decision(
            db,
            organization_id=organization.id,
            decision=_decision(
                root=_revision(
                    "chunk-b",
                    "chunk-b-r1",
                ),
                target=_revision(
                    "index-c",
                    "index-c-r2",
                ),
            ),
        )
        db.commit()

        client = TestClient(app)
        response = client.post(
            "/api/lineage/invalidation-decisions/query",
            json={
                "source_external_id": "source-a",
                "asset_external_id": "chunk-a",
                "revision_external_id": "chunk-a-r3",
            },
            headers={
                "X-API-Key": raw_key,
            },
        )

        assert response.status_code == 200
        payload = response.json()

        assert payload["root"] == {
            "source_external_id": "source-a",
            "asset_external_id": "chunk-a",
            "revision_external_id": "chunk-a-r3",
        }
        expected = sorted(
            (
                first.record,
                second.record,
            ),
            key=lambda record: (
                record.created_at,
                record.id,
            ),
        )

        assert [
            decision["id"]
            for decision in payload["decisions"]
        ] == [
            str(record.id)
            for record in expected
        ]
        assert [
            decision["target"]
            for decision in payload["decisions"]
        ] == [
            {
                "source_external_id": record.target_source_external_id,
                "asset_external_id": record.target_asset_external_id,
                "revision_external_id": record.target_revision_external_id,
            }
            for record in expected
        ]
    finally:
        db.close()
        _cleanup(organization_ids)


def test_invalidation_decision_query_is_tenant_scoped() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization_a = _make_organization(db)
        organization_b = _make_organization(db)
        organization_ids.extend(
            [
                organization_a.id,
                organization_b.id,
            ]
        )

        raw_key_a = _create_api_key(
            db,
            organization_id=organization_a.id,
        )
        decision_a = _persist_decision(
            db,
            organization_id=organization_a.id,
        )
        _persist_decision(
            db,
            organization_id=organization_b.id,
        )
        db.commit()

        client = TestClient(app)
        response = client.post(
            "/api/lineage/invalidation-decisions/query",
            json={
                "source_external_id": "source-a",
                "asset_external_id": "chunk-a",
                "revision_external_id": "chunk-a-r3",
            },
            headers={
                "X-API-Key": raw_key_a,
            },
        )

        assert response.status_code == 200
        payload = response.json()

        assert [
            decision["id"]
            for decision in payload["decisions"]
        ] == [
            str(decision_a.record.id),
        ]
    finally:
        db.close()
        _cleanup(organization_ids)


def test_invalidation_decision_read_requires_api_key() -> None:
    client = TestClient(app)

    response = client.get(
        f"/api/lineage/invalidation-decisions/{uuid.uuid4()}"
    )

    assert response.status_code == 401


def test_invalidation_decision_read_requires_read_permission() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        raw_key = _create_api_key(
            db,
            organization_id=organization.id,
            read=False,
            write=True,
        )
        persisted = _persist_decision(
            db,
            organization_id=organization.id,
        )
        db.commit()

        client = TestClient(app)
        response = client.get(
            (
                "/api/lineage/invalidation-decisions/"
                f"{persisted.record.id}"
            ),
            headers={
                "X-API-Key": raw_key,
            },
        )

        assert response.status_code == 403
    finally:
        db.close()
        _cleanup(organization_ids)


def test_invalidation_decision_read_projects_decision_without_execution() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        raw_key = _create_api_key(
            db,
            organization_id=organization.id,
        )
        persisted = _persist_decision(
            db,
            organization_id=organization.id,
        )
        db.commit()

        client = TestClient(app)
        response = client.get(
            (
                "/api/lineage/invalidation-decisions/"
                f"{persisted.record.id}"
            ),
            headers={
                "X-API-Key": raw_key,
            },
        )

        assert response.status_code == 200
        payload = response.json()

        assert payload["decision"] == {
            "id": str(persisted.record.id),
            "root": {
                "source_external_id": "source-a",
                "asset_external_id": "chunk-a",
                "revision_external_id": "chunk-a-r3",
            },
            "target": {
                "source_external_id": "source-a",
                "asset_external_id": "index-a",
                "revision_external_id": "index-a-r7",
            },
            "policy": {
                "policy_external_id": "rag-invalidation",
                "revision": 7,
            },
            "disposition": "invalidate",
            "reason_code": "causal_dependency_changed",
            "causal_path": [
                {
                    "source_external_id": "source-a",
                    "asset_external_id": "chunk-a",
                    "revision_external_id": "chunk-a-r3",
                },
                {
                    "source_external_id": "source-a",
                    "asset_external_id": "index-a",
                    "revision_external_id": "index-a-r7",
                },
            ],
        }
        assert payload["eligibility"] == {
            "eligible": False,
            "reason_code": "policy_not_active",
        }
        assert payload["execution"] == {
            "intent": None,
            "latest_attempt": None,
            "result": None,
            "verification": None,
        }
    finally:
        db.close()
        _cleanup(organization_ids)


def test_invalidation_decision_read_projects_terminal_execution() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        raw_key = _create_api_key(
            db,
            organization_id=organization.id,
        )
        persisted = _persist_decision(
            db,
            organization_id=organization.id,
        )
        _establish_execution_authority(
            db,
            organization_id=organization.id,
        )
        db.commit()

        intent = authorize_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            decision_record_id=persisted.record.id,
        ).record
        db.commit()

        attempt = claim_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
            lease_seconds=300,
        ).record
        db.commit()

        result = persist_lineage_invalidation_execution_result(
            db,
            organization_id=organization.id,
            attempt_id=attempt.id,
            outcome="succeeded",
        ).record
        db.commit()

        client = TestClient(app)
        response = client.get(
            (
                "/api/lineage/invalidation-decisions/"
                f"{persisted.record.id}"
            ),
            headers={
                "X-API-Key": raw_key,
            },
        )

        assert response.status_code == 200
        payload = response.json()

        assert payload["eligibility"] == {
            "eligible": True,
            "reason_code": "eligible",
        }

        intent_payload = payload["execution"]["intent"]
        assert intent_payload is not None
        assert intent_payload["id"] == str(intent.id)
        assert (
            datetime.fromisoformat(intent_payload["authorized_at"])
            == intent.authorized_at
        )

        attempt_payload = payload["execution"]["latest_attempt"]
        assert attempt_payload is not None
        assert attempt_payload["id"] == str(attempt.id)
        assert attempt_payload["attempt_number"] == 1
        assert attempt_payload["binding_revision"] == 1
        assert (
            datetime.fromisoformat(attempt_payload["claimed_at"])
            == attempt.claimed_at
        )
        assert (
            datetime.fromisoformat(attempt_payload["lease_expires_at"])
            == attempt.lease_expires_at
        )

        result_payload = payload["execution"]["result"]
        assert result_payload is not None
        assert result_payload["id"] == str(result.id)
        assert result_payload["attempt_id"] == str(attempt.id)
        assert result_payload["outcome"] == "succeeded"
        assert (
            datetime.fromisoformat(result_payload["recorded_at"])
            == result.recorded_at
        )
        verification_payload = payload["execution"]["verification"]
        assert verification_payload is not None
        assert verification_payload["state"] == "awaiting_evidence"
        assert verification_payload["execution_result_id"] == str(result.id)
        assert verification_payload["verification_record_id"] is None
        assert verification_payload["observation_id"] is None
    finally:
        db.close()
        _cleanup(organization_ids)


def test_invalidation_decision_read_keeps_terminal_history_when_target_advances() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        raw_key = _create_api_key(
            db,
            organization_id=organization.id,
        )
        persisted = _persist_decision(
            db,
            organization_id=organization.id,
        )
        _establish_execution_authority(
            db,
            organization_id=organization.id,
        )
        db.commit()

        intent = authorize_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            decision_record_id=persisted.record.id,
        ).record
        db.commit()

        attempt = claim_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
            lease_seconds=300,
        ).record
        db.commit()

        result = persist_lineage_invalidation_execution_result(
            db,
            organization_id=organization.id,
            attempt_id=attempt.id,
            outcome="succeeded",
        ).record
        db.commit()

        persist_lineage_observation(
            db,
            organization_id=organization.id,
            observation=_materialization(
                revision_external_id="index-a-r8",
                sequence=8,
            ),
        )
        db.commit()

        client = TestClient(app)
        response = client.get(
            (
                "/api/lineage/invalidation-decisions/"
                f"{persisted.record.id}"
            ),
            headers={
                "X-API-Key": raw_key,
            },
        )

        assert response.status_code == 200
        payload = response.json()

        assert payload["eligibility"] == {
            "eligible": False,
            "reason_code": "target_not_current",
        }
        assert payload["execution"]["result"] is not None
        assert payload["execution"]["result"]["id"] == str(result.id)
        assert payload["execution"]["result"]["outcome"] == "succeeded"
        assert payload["execution"]["verification"] is not None
        assert (
            payload["execution"]["verification"]["state"]
            == "awaiting_evidence"
        )
        assert (
            payload["execution"]["verification"]["execution_result_id"]
            == str(result.id)
        )
    finally:
        db.close()
        _cleanup(organization_ids)


def test_invalidation_decision_read_returns_not_found_for_unknown_decision() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        raw_key = _create_api_key(
            db,
            organization_id=organization.id,
        )
        db.commit()

        client = TestClient(app)
        response = client.get(
            (
                "/api/lineage/invalidation-decisions/"
                f"{uuid.uuid4()}"
            ),
            headers={
                "X-API-Key": raw_key,
            },
        )

        assert response.status_code == 404
        assert response.json() == {
            "detail": {
                "error": {
                    "code": "LINEAGE_004",
                    "message": "Lineage invalidation decision not found.",
                    "details": None,
                }
            }
        }
    finally:
        db.close()
        _cleanup(organization_ids)


def test_invalidation_decision_read_hides_cross_tenant_decision() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        owner = _make_organization(db)
        other = _make_organization(db)
        organization_ids.extend(
            [
                owner.id,
                other.id,
            ]
        )

        persisted = _persist_decision(
            db,
            organization_id=owner.id,
        )
        other_raw_key = _create_api_key(
            db,
            organization_id=other.id,
        )
        db.commit()

        client = TestClient(app)
        response = client.get(
            (
                "/api/lineage/invalidation-decisions/"
                f"{persisted.record.id}"
            ),
            headers={
                "X-API-Key": other_raw_key,
            },
        )

        assert response.status_code == 404
        assert response.json() == {
            "detail": {
                "error": {
                    "code": "LINEAGE_004",
                    "message": "Lineage invalidation decision not found.",
                    "details": None,
                }
            }
        }
    finally:
        db.close()
        _cleanup(organization_ids)


def test_execution_verification_publication_requires_api_key() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/lineage/invalidation-execution-verifications",
        json={
            "execution_result_id": str(uuid.uuid4()),
            "observation_id": str(uuid.uuid4()),
        },
    )

    assert response.status_code == 401


def test_execution_verification_publication_requires_write_permission() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        raw_key = _create_api_key(
            db,
            organization_id=organization.id,
            read=True,
            write=False,
        )
        db.commit()

        client = TestClient(app)
        response = client.post(
            "/api/lineage/invalidation-execution-verifications",
            headers={
                "X-API-Key": raw_key,
            },
            json={
                "execution_result_id": str(uuid.uuid4()),
                "observation_id": str(uuid.uuid4()),
            },
        )

        assert response.status_code == 403
    finally:
        db.close()
        _cleanup(organization_ids)


def test_execution_verification_publication_binds_successful_result() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        raw_key = _create_api_key(
            db,
            organization_id=organization.id,
            write=True,
        )
        _, result = _establish_execution_result(
            db,
            organization_id=organization.id,
        )

        evidence = persist_lineage_observation(
            db,
            organization_id=organization.id,
            observation=_materialization(
                revision_external_id="index-a-r8",
                sequence=8,
            ),
        ).record
        db.commit()

        client = TestClient(app)
        response = client.post(
            "/api/lineage/invalidation-execution-verifications",
            headers={
                "X-API-Key": raw_key,
            },
            json={
                "execution_result_id": str(result.id),
                "observation_id": str(evidence.id),
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["execution_result_id"] == str(result.id)
        assert payload["observation_id"] == str(evidence.id)
        assert payload["created"] is True
        assert payload["id"]
        assert payload["created_at"]
    finally:
        db.close()
        _cleanup(organization_ids)


def test_execution_verification_publication_is_idempotent() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        raw_key = _create_api_key(
            db,
            organization_id=organization.id,
            write=True,
        )
        _, result = _establish_execution_result(
            db,
            organization_id=organization.id,
        )

        evidence = persist_lineage_observation(
            db,
            organization_id=organization.id,
            observation=_materialization(
                revision_external_id="index-a-r8",
                sequence=8,
            ),
        ).record
        db.commit()

        client = TestClient(app)
        request = {
            "execution_result_id": str(result.id),
            "observation_id": str(evidence.id),
        }
        headers = {
            "X-API-Key": raw_key,
        }

        first = client.post(
            "/api/lineage/invalidation-execution-verifications",
            headers=headers,
            json=request,
        )
        second = client.post(
            "/api/lineage/invalidation-execution-verifications",
            headers=headers,
            json=request,
        )

        assert first.status_code == 200
        assert second.status_code == 200

        first_payload = first.json()
        second_payload = second.json()

        assert first_payload["created"] is True
        assert second_payload["created"] is False
        assert second_payload["id"] == first_payload["id"]
        assert (
            second_payload["execution_result_id"]
            == first_payload["execution_result_id"]
        )
        assert (
            second_payload["observation_id"]
            == first_payload["observation_id"]
        )
    finally:
        db.close()
        _cleanup(organization_ids)


def test_execution_verification_api_rejects_old_ingestion_evidence() -> None:
    db = SessionLocal()
    organizations: list[uuid.UUID] = []
    try:
        org = _make_organization(db)
        organizations.append(org.id)
        raw_key = _create_api_key(
            db, organization_id=org.id, write=True,
        )
        _, result = _establish_execution_result(
            db, organization_id=org.id,
        )
        evidence = persist_lineage_observation(
            db, organization_id=org.id,
            observation=_materialization(
                revision_external_id="index-a-r8", sequence=8,
                dependencies=(),
            ),
        ).record
        evidence.created_at = result.recorded_at - timedelta(seconds=1)
        db.commit()

        response = TestClient(app).post(
            "/api/lineage/invalidation-execution-verifications",
            headers={"X-API-Key": raw_key},
            json={
                "execution_result_id": str(result.id),
                "observation_id": str(evidence.id),
            },
        )
        assert response.status_code == 409
        assert response.json()["detail"]["error"]["code"] == "LINEAGE_011"
    finally:
        db.close()
        _cleanup(organizations)


def test_execution_verification_publication_rejects_failed_result() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        raw_key = _create_api_key(
            db,
            organization_id=organization.id,
            write=True,
        )
        _, result = _establish_execution_result(
            db,
            organization_id=organization.id,
            outcome="failed",
        )

        evidence = persist_lineage_observation(
            db,
            organization_id=organization.id,
            observation=_materialization(
                revision_external_id="index-a-r8",
                sequence=8,
            ),
        ).record
        db.commit()

        client = TestClient(app)
        response = client.post(
            "/api/lineage/invalidation-execution-verifications",
            headers={
                "X-API-Key": raw_key,
            },
            json={
                "execution_result_id": str(result.id),
                "observation_id": str(evidence.id),
            },
        )

        assert response.status_code == 409
        assert response.json()["detail"]["error"]["code"] == "LINEAGE_011"
    finally:
        db.close()
        _cleanup(organization_ids)


def test_execution_verification_publication_rejects_conflicting_binding() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        raw_key = _create_api_key(
            db,
            organization_id=organization.id,
            write=True,
        )
        _, result = _establish_execution_result(
            db,
            organization_id=organization.id,
        )

        first_evidence = persist_lineage_observation(
            db,
            organization_id=organization.id,
            observation=_materialization(
                revision_external_id="index-a-r8",
                sequence=8,
            ),
        ).record
        db.commit()

        client = TestClient(app)
        headers = {
            "X-API-Key": raw_key,
        }

        first = client.post(
            "/api/lineage/invalidation-execution-verifications",
            headers=headers,
            json={
                "execution_result_id": str(result.id),
                "observation_id": str(first_evidence.id),
            },
        )
        assert first.status_code == 200

        second_evidence = persist_lineage_observation(
            db,
            organization_id=organization.id,
            observation=_materialization(
                revision_external_id="index-a-r9",
                sequence=9,
            ),
        ).record
        db.commit()

        second = client.post(
            "/api/lineage/invalidation-execution-verifications",
            headers=headers,
            json={
                "execution_result_id": str(result.id),
                "observation_id": str(second_evidence.id),
            },
        )

        assert second.status_code == 409
        error = second.json()["detail"]["error"]
        assert error["code"] == "LINEAGE_012"
        assert error["details"] == {
            "existing_observation_id": str(first_evidence.id),
            "requested_observation_id": str(second_evidence.id),
        }
    finally:
        db.close()
        _cleanup(organization_ids)


def test_execution_verification_publication_hides_cross_tenant_result() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        owner = _make_organization(db)
        caller = _make_organization(db)
        organization_ids.extend(
            [
                owner.id,
                caller.id,
            ]
        )

        _, result = _establish_execution_result(
            db,
            organization_id=owner.id,
        )

        caller_key = _create_api_key(
            db,
            organization_id=caller.id,
            write=True,
        )
        caller_evidence = persist_lineage_observation(
            db,
            organization_id=caller.id,
            observation=_materialization(
                revision_external_id="index-a-r8",
                sequence=8,
            ),
        ).record
        db.commit()

        client = TestClient(app)
        response = client.post(
            "/api/lineage/invalidation-execution-verifications",
            headers={
                "X-API-Key": caller_key,
            },
            json={
                "execution_result_id": str(result.id),
                "observation_id": str(caller_evidence.id),
            },
        )

        assert response.status_code == 404
        assert response.json()["detail"]["error"]["code"] == "LINEAGE_010"
    finally:
        db.close()
        _cleanup(organization_ids)


def test_target_integrity_query_requires_api_key() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/lineage/target-integrity/query",
        json={
            "source_external_id": "source-a",
            "asset_external_id": "index-a",
            "revision_external_id": "index-a-r7",
        },
    )

    assert response.status_code == 401


def test_target_integrity_query_requires_read_permission() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        raw_key = _create_api_key(
            db,
            organization_id=organization.id,
            read=False,
            write=True,
        )
        db.commit()

        client = TestClient(app)
        response = client.post(
            "/api/lineage/target-integrity/query",
            json={
                "source_external_id": "source-a",
                "asset_external_id": "index-a",
                "revision_external_id": "index-a-r7",
            },
            headers={"X-API-Key": raw_key},
        )

        assert response.status_code == 403
    finally:
        db.close()
        _cleanup(organization_ids)


def test_target_integrity_query_returns_unknown_without_evidence() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        raw_key = _create_api_key(
            db,
            organization_id=organization.id,
        )
        db.commit()

        client = TestClient(app)
        response = client.post(
            "/api/lineage/target-integrity/query",
            json={
                "source_external_id": "source-a",
                "asset_external_id": "index-a",
                "revision_external_id": "index-a-r7",
            },
            headers={"X-API-Key": raw_key},
        )

        assert response.status_code == 200
        assert response.json() == {
            "target": {
                "source_external_id": "source-a",
                "asset_external_id": "index-a",
                "revision_external_id": "index-a-r7",
            },
            "status": "unknown",
            "evidence": [],
        }
    finally:
        db.close()
        _cleanup(organization_ids)


def test_target_integrity_query_returns_not_affected_for_no_action_evidence() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        raw_key = _create_api_key(
            db,
            organization_id=organization.id,
        )
        root = _revision("chunk-a", "chunk-a-r3")
        target = _revision("index-a", "index-a-r7")
        persisted = _persist_decision(
            db,
            organization_id=organization.id,
            decision=LineageInvalidationDecision(
                root=root,
                target=target,
                policy=_policy(),
                disposition="no_action",
                reason_code="policy_no_action",
                causal_path=(root, target),
            ),
        )
        db.commit()

        client = TestClient(app)
        response = client.post(
            "/api/lineage/target-integrity/query",
            json={
                "source_external_id": "source-a",
                "asset_external_id": "index-a",
                "revision_external_id": "index-a-r7",
            },
            headers={"X-API-Key": raw_key},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "not_affected"
        assert payload["evidence"] == [
            {
                "decision_id": str(persisted.record.id),
                "root": {
                    "source_external_id": "source-a",
                    "asset_external_id": "chunk-a",
                    "revision_external_id": "chunk-a-r3",
                },
                "disposition": "no_action",
                "reason_code": "policy_no_action",
                "state": "no_action",
            }
        ]
    finally:
        db.close()
        _cleanup(organization_ids)


def test_target_integrity_query_returns_affected_for_unresolved_invalidation(caplog) -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        raw_key = _create_api_key(
            db,
            organization_id=organization.id,
        )
        persisted = _persist_decision(
            db,
            organization_id=organization.id,
        )
        db.commit()

        client = TestClient(app)
        caplog.clear()
        with caplog.at_level(
            logging.INFO,
            logger="src.api.routes.lineage",
        ):
            response = client.post(
                "/api/lineage/target-integrity/query",
            json={
                "source_external_id": "source-a",
                "asset_external_id": "index-a",
                "revision_external_id": "index-a-r7",
            },
            headers={"X-API-Key": raw_key},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "affected"
        assert payload["evidence"] == [
            {
                "decision_id": str(persisted.record.id),
                "root": {
                    "source_external_id": "source-a",
                    "asset_external_id": "chunk-a",
                    "revision_external_id": "chunk-a-r3",
                },
                "disposition": "invalidate",
                "reason_code": "causal_dependency_changed",
                "state": "unresolved",
            }
        ]

        records = _integrity_telemetry_records(caplog)
        assert len(records) == 1
        record = records[0]
        assert record.operation == "target"
        assert record.target_count == 1
        assert record.unknown_count == 0
        assert record.not_affected_count == 0
        assert record.affected_count == 1
        assert record.verified_count == 0
        assert record.has_unknown is False
        assert record.has_unresolved_impact is True
        _assert_integrity_telemetry_is_identifier_free(record)
    finally:
        db.close()
        _cleanup(organization_ids)


def test_target_integrity_query_returns_verified_after_bound_repair_evidence() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        raw_key = _create_api_key(
            db,
            organization_id=organization.id,
        )
        persisted, result = _establish_execution_result(
            db,
            organization_id=organization.id,
            outcome="succeeded",
        )
        repair_observation = persist_lineage_observation(
            db,
            organization_id=organization.id,
            observation=_materialization(
                revision_external_id="index-a-r8",
                sequence=8,
                # The repaired index no longer depends on the stale chunk.
                dependencies=(),
            ),
        ).record
        db.commit()
        persist_lineage_invalidation_execution_verification(
            db,
            organization_id=organization.id,
            execution_result_id=result.id,
            observation_id=repair_observation.id,
        )
        db.commit()

        client = TestClient(app)
        response = client.post(
            "/api/lineage/target-integrity/query",
            json={
                "source_external_id": "source-a",
                "asset_external_id": "index-a",
                "revision_external_id": "index-a-r7",
            },
            headers={"X-API-Key": raw_key},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "verified"
        assert payload["evidence"] == [
            {
                "decision_id": str(persisted.id),
                "root": {
                    "source_external_id": "source-a",
                    "asset_external_id": "chunk-a",
                    "revision_external_id": "chunk-a-r3",
                },
                "disposition": "invalidate",
                "reason_code": "causal_dependency_changed",
                "state": "verified",
            }
        ]
    finally:
        db.close()
        _cleanup(organization_ids)



def test_target_integrity_query_is_tenant_isolated() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        owner = _make_organization(db)
        caller = _make_organization(db)
        organization_ids.extend([owner.id, caller.id])
        _persist_decision(
            db,
            organization_id=owner.id,
        )
        caller_key = _create_api_key(
            db,
            organization_id=caller.id,
        )
        db.commit()

        client = TestClient(app)
        response = client.post(
            "/api/lineage/target-integrity/query",
            json={
                "source_external_id": "source-a",
                "asset_external_id": "index-a",
                "revision_external_id": "index-a-r7",
            },
            headers={"X-API-Key": caller_key},
        )

        assert response.status_code == 200
        assert response.json()["status"] == "unknown"
        assert response.json()["evidence"] == []
    finally:
        db.close()
        _cleanup(organization_ids)


def test_target_integrity_query_keeps_unresolved_history_affected() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        raw_key = _create_api_key(
            db,
            organization_id=organization.id,
        )
        target = _revision("index-a", "index-a-r7")
        first_root = _revision("chunk-a", "chunk-a-r3")
        second_root = _revision("chunk-b", "chunk-b-r4")
        _persist_decision(
            db,
            organization_id=organization.id,
            decision=LineageInvalidationDecision(
                root=first_root,
                target=target,
                policy=_policy(),
                disposition="no_action",
                reason_code="policy_no_action",
                causal_path=(first_root, target),
            ),
        )
        _persist_decision(
            db,
            organization_id=organization.id,
            decision=LineageInvalidationDecision(
                root=second_root,
                target=target,
                policy=_policy(),
                disposition="invalidate",
                reason_code="causal_dependency_changed",
                causal_path=(second_root, target),
            ),
        )
        db.commit()

        client = TestClient(app)
        response = client.post(
            "/api/lineage/target-integrity/query",
            json={
                "source_external_id": "source-a",
                "asset_external_id": "index-a",
                "revision_external_id": "index-a-r7",
            },
            headers={"X-API-Key": raw_key},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "affected"
        assert sorted(item["state"] for item in payload["evidence"]) == [
            "no_action",
            "unresolved",
        ]
    finally:
        db.close()
        _cleanup(organization_ids)


def test_target_integrity_assessment_query_count_is_bounded() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        target = _revision("index-a", "index-a-r7")

        for ordinal in range(3):
            root = _revision(
                f"chunk-{ordinal}",
                f"chunk-{ordinal}-r1",
            )
            _persist_decision(
                db,
                organization_id=organization.id,
                decision=LineageInvalidationDecision(
                    root=root,
                    target=target,
                    policy=_policy(),
                    disposition="invalidate",
                    reason_code="causal_dependency_changed",
                    causal_path=(root, target),
                ),
            )
        db.commit()
        organization_id = organization.id

        select_count = 0

        def count_selects(
            _connection,
            _cursor,
            statement,
            _parameters,
            _context,
            _executemany,
        ) -> None:
            nonlocal select_count
            if statement.lstrip().upper().startswith("SELECT"):
                select_count += 1

        event.listen(engine, "before_cursor_execute", count_selects)
        try:
            assessment = assess_lineage_target_integrity(
                db,
                organization_id=organization_id,
                target=target,
            )
        finally:
            event.remove(engine, "before_cursor_execute", count_selects)

        assert assessment.status == "affected"
        assert len(assessment.evidence) == 3
        assert select_count == 2
    finally:
        db.close()
        _cleanup(organization_ids)



def test_context_integrity_query_requires_api_key() -> None:
    client = TestClient(app)

    response = client.post(
        "/api/lineage/context-integrity/query",
        json={
            "targets": [
                {
                    "source_external_id": "source-a",
                    "asset_external_id": "index-a",
                    "revision_external_id": "index-a-r7",
                }
            ]
        },
    )

    assert response.status_code == 401


def test_context_integrity_query_summarizes_mixed_context(caplog) -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        raw_key = _create_api_key(
            db,
            organization_id=organization.id,
        )
        not_affected = _revision("index-a", "index-a-r1")
        affected = _revision("index-b", "index-b-r2")
        unknown = _revision("index-c", "index-c-r3")
        first_root = _revision("chunk-a", "chunk-a-r1")
        second_root = _revision("chunk-b", "chunk-b-r1")
        _persist_decision(
            db,
            organization_id=organization.id,
            decision=LineageInvalidationDecision(
                root=first_root,
                target=not_affected,
                policy=_policy(),
                disposition="no_action",
                reason_code="policy_no_action",
                causal_path=(first_root, not_affected),
            ),
        )
        _persist_decision(
            db,
            organization_id=organization.id,
            decision=LineageInvalidationDecision(
                root=second_root,
                target=affected,
                policy=_policy(),
                disposition="invalidate",
                reason_code="causal_dependency_changed",
                causal_path=(second_root, affected),
            ),
        )
        db.commit()

        client = TestClient(app)
        caplog.clear()
        with caplog.at_level(
            logging.INFO,
            logger="src.api.routes.lineage",
        ):
            response = client.post(
                "/api/lineage/context-integrity/query",
            json={
                "targets": [
                    {
                        "source_external_id": target.source_external_id,
                        "asset_external_id": target.asset_external_id,
                        "revision_external_id": target.revision_external_id,
                    }
                    for target in (not_affected, affected, unknown)
                ]
            },
            headers={"X-API-Key": raw_key},
        )

        assert response.status_code == 200
        payload = response.json()
        assert [item["status"] for item in payload["assessments"]] == [
            "not_affected",
            "affected",
            "unknown",
        ]
        assert payload["summary"] == {
            "target_count": 3,
            "unknown_count": 1,
            "not_affected_count": 1,
            "affected_count": 1,
            "verified_count": 0,
            "has_unknown": True,
            "has_unresolved_impact": True,
        }

        records = _integrity_telemetry_records(caplog)
        assert len(records) == 1
        record = records[0]
        assert record.operation == "context"
        assert record.target_count == 3
        assert record.unknown_count == 1
        assert record.not_affected_count == 1
        assert record.affected_count == 1
        assert record.verified_count == 0
        assert record.has_unknown is True
        assert record.has_unresolved_impact is True
        _assert_integrity_telemetry_is_identifier_free(record)
    finally:
        db.close()
        _cleanup(organization_ids)


def test_context_integrity_query_is_tenant_isolated() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        owner = _make_organization(db)
        caller = _make_organization(db)
        organization_ids.extend((owner.id, caller.id))
        raw_key = _create_api_key(
            db,
            organization_id=caller.id,
        )
        target = _revision("index-a", "index-a-r7")
        root = _revision("chunk-a", "chunk-a-r3")
        _persist_decision(
            db,
            organization_id=owner.id,
            decision=LineageInvalidationDecision(
                root=root,
                target=target,
                policy=_policy(),
                disposition="invalidate",
                reason_code="causal_dependency_changed",
                causal_path=(root, target),
            ),
        )
        db.commit()

        client = TestClient(app)
        response = client.post(
            "/api/lineage/context-integrity/query",
            json={
                "targets": [
                    {
                        "source_external_id": "source-a",
                        "asset_external_id": "index-a",
                        "revision_external_id": "index-a-r7",
                    }
                ]
            },
            headers={"X-API-Key": raw_key},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["assessments"][0]["status"] == "unknown"
        assert payload["summary"]["unknown_count"] == 1
        assert payload["summary"]["has_unknown"] is True
    finally:
        db.close()
        _cleanup(organization_ids)



def test_langchain_context_bridge_queries_authenticated_runtime_api() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        raw_key = _create_api_key(
            db,
            organization_id=organization.id,
        )
        serving_target = _revision("index-a", "index-a-r7")
        root = _revision("chunk-a", "chunk-a-r3")
        _persist_decision(
            db,
            organization_id=organization.id,
            decision=LineageInvalidationDecision(
                root=root,
                target=serving_target,
                policy=_policy(),
                disposition="invalidate",
                reason_code="causal_dependency_changed",
                causal_path=(root, serving_target),
            ),
        )
        db.commit()

        class RetrievedDocument:
            id = root.asset_external_id
            metadata = {
                SOURCE_EXTERNAL_ID_KEY: root.source_external_id,
                CHUNK_REVISION_EXTERNAL_ID_KEY: root.revision_external_id,
            }
            page_content = "retrieved content"

        api_client = TestClient(app)

        def forward_to_api(request: httpx.Request) -> httpx.Response:
            response = api_client.request(
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

        with SDEIntegrityClient(
            base_url="http://sde.test",
            api_key=raw_key,
            timeout_seconds=1.0,
            transport=httpx.MockTransport(forward_to_api),
        ) as integrity_client:
            assessment = query_langchain_context_integrity(
                client=integrity_client,
                serving_revision=IntegrityRevision(
                    source_external_id=serving_target.source_external_id,
                    asset_external_id=serving_target.asset_external_id,
                    revision_external_id=serving_target.revision_external_id,
                ),
                retrieved_documents=(
                    RetrievedDocument(),
                    RetrievedDocument(),
                ),
            )

        assert len(assessment.assessments) == 2
        serving_assessment, retrieved_assessment = assessment.assessments
        assert serving_assessment.target.asset_external_id == "index-a"
        assert serving_assessment.target.revision_external_id == "index-a-r7"
        assert serving_assessment.status == "affected"
        assert retrieved_assessment.target.asset_external_id == "chunk-a"
        assert retrieved_assessment.target.revision_external_id == "chunk-a-r3"
        assert retrieved_assessment.status == "unknown"
        assert assessment.summary.target_count == 2
        assert assessment.summary.affected_count == 1
        assert assessment.summary.unknown_count == 1
        assert assessment.summary.has_unknown is True
        assert assessment.summary.has_unresolved_impact is True
    finally:
        db.close()
        _cleanup(organization_ids)


def test_context_integrity_assessment_query_count_is_bounded() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        targets = tuple(
            _revision(f"index-{ordinal}", f"index-{ordinal}-r1")
            for ordinal in range(8)
        )
        for ordinal, target in enumerate(targets):
            root = _revision(
                f"chunk-{ordinal}",
                f"chunk-{ordinal}-r1",
            )
            _persist_decision(
                db,
                organization_id=organization.id,
                decision=LineageInvalidationDecision(
                    root=root,
                    target=target,
                    policy=_policy(),
                    disposition="invalidate",
                    reason_code="causal_dependency_changed",
                    causal_path=(root, target),
                ),
            )
        db.commit()
        organization_id = organization.id

        select_count = 0

        def count_selects(
            _connection,
            _cursor,
            statement,
            _parameters,
            _context,
            _executemany,
        ) -> None:
            nonlocal select_count
            if statement.lstrip().upper().startswith("SELECT"):
                select_count += 1

        event.listen(engine, "before_cursor_execute", count_selects)
        try:
            assessment = assess_lineage_context_integrity(
                db,
                organization_id=organization_id,
                targets=targets,
            )
        finally:
            event.remove(engine, "before_cursor_execute", count_selects)

        assert assessment.summary.target_count == 8
        assert assessment.summary.affected_count == 8
        assert assessment.summary.has_unresolved_impact is True
        assert select_count == 2
    finally:
        db.close()
        _cleanup(organization_ids)
