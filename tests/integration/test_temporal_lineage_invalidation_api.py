from __future__ import annotations

import os
import secrets
from types import SimpleNamespace
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

import src.main as main_module

from src.api import temporal_routes
from src.core.database import SessionLocal
from src.core.security import (
    get_current_user,
    token_hash,
)
from src.domain.lineage_execution_target import (
    LineageExecutionTargetBinding,
    LineageExecutionTargetIdentity,
)
from src.domain.lineage_invalidation import (
    LineageInvalidationPolicyIdentity,
)
from src.domain.lineage_observation import (
    LineageObservation,
    LineageRevisionReference,
)
from src.main import app
from src.models import ApiKey
from src.models.entity import Entity
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
from src.models.lineage_observation import (
    LineageObservationRecord,
)
from src.models.monitored_page import MonitoredPage
from src.models.organization import Organization
from src.models.page_snapshot import PageSnapshot
from src.models.semantic_drift_event import (
    SemanticDriftEvent,
    VECTOR_ANALYSIS_STATUS_COMPLETED,
)
from src.models.temporal_lineage_correlation import (
    TemporalLineageCorrelationRecord,
)
from src.models.temporal_lineage_invalidation_decision_provenance import (
    TemporalLineageInvalidationDecisionProvenanceRecord,
)
from src.services.lineage_observation_persistence_service import (
    persist_lineage_observation,
)
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
from src.services.lineage_invalidation_policy_activation_service import (
    activate_lineage_invalidation_policy,
)
from src.services.temporal_lineage_correlation_service import (
    persist_temporal_lineage_correlation,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


_BROWSER_AUTH_HEADERS = {
    "Authorization": "Bearer temporal-integrity-test-token",
}


def _fake_browser_user(
    organization_id: uuid.UUID,
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization_id,
        email="temporal-integrity@example.test",
    )


def _database_override():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


def _allow_browser_auth_middleware(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        main_module,
        "verify_token",
        lambda token: {
            "sub": str(uuid.uuid4()),
        },
    )


def _override_browser_user(
    organization_id: uuid.UUID,
) -> None:
    app.dependency_overrides[
        get_current_user
    ] = lambda: _fake_browser_user(
        organization_id
    )

    app.dependency_overrides[
        temporal_routes.get_db
    ] = _database_override


def _clear_browser_overrides() -> None:
    app.dependency_overrides.pop(
        get_current_user,
        None,
    )
    app.dependency_overrides.pop(
        temporal_routes.get_db,
        None,
    )


def _create_organization(
    db,
    *,
    prefix: str,
) -> Organization:
    suffix = uuid.uuid4().hex[:12]

    organization = Organization(
        name=f"{prefix}-{suffix}",
        slug=f"{prefix}-{suffix}",
        billing_email=f"{prefix}-{suffix}@example.test",
        subscription_tier="free",
        quota_urls=10,
        quota_requests=1000,
        is_active=True,
    )
    db.add(organization)
    db.flush()

    return organization


def _create_api_key(
    db,
    *,
    organization_id: uuid.UUID,
    write: bool,
) -> str:
    raw_key = f"sk-{secrets.token_urlsafe(32)}"

    api_key = ApiKey(
        organization_id=organization_id,
        name="temporal-lineage-invalidation-api",
        key_hash=token_hash(raw_key),
        permissions={
            "read": True,
            "write": write,
        },
        is_active=True,
    )
    db.add(api_key)
    db.flush()

    return raw_key


def _create_page(
    db,
    *,
    organization: Organization,
) -> MonitoredPage:
    suffix = uuid.uuid4().hex

    entity = Entity(
        id=uuid.uuid4(),
        organization_id=organization.id,
        name=f"Entity {suffix[:8]}",
        domain=f"{suffix}.example.test",
        category="test",
    )
    db.add(entity)
    db.flush()

    page = MonitoredPage(
        id=uuid.uuid4(),
        organization_id=organization.id,
        entity_id=entity.id,
        url=f"https://example.test/{suffix}",
        page_type="docs",
        crawl_frequency="daily",
        is_active=True,
    )
    db.add(page)
    db.flush()

    return page


def _create_snapshot(
    db,
    *,
    organization: Organization,
    page: MonitoredPage,
    raw_content: str,
) -> PageSnapshot:
    snapshot = PageSnapshot(
        id=uuid.uuid4(),
        monitored_page_id=page.id,
        organization_id=organization.id,
        content_hash=uuid.uuid4().hex,
        raw_content=raw_content,
        content_size=len(raw_content),
    )
    db.add(snapshot)
    db.flush()

    return snapshot


def _create_event(
    db,
    *,
    organization: Organization,
    page: MonitoredPage,
    old_snapshot: PageSnapshot,
    new_snapshot: PageSnapshot,
    analysis_status: str = "completed",
    vector_analysis_status: str = VECTOR_ANALYSIS_STATUS_COMPLETED,
) -> SemanticDriftEvent:
    event = SemanticDriftEvent(
        id=uuid.uuid4(),
        monitored_page_id=page.id,
        organization_id=organization.id,
        old_snapshot_id=old_snapshot.id,
        new_snapshot_id=new_snapshot.id,
        semantic_delta_json={
            "changes": [
                {
                    "before": "30 days",
                    "after": "14 days",
                }
            ]
        },
        analysis_status=analysis_status,
        vector_analysis_status=vector_analysis_status,
        risk_level="medium",
        confidence=0.95,
        impact_score=50,
        changes_count=1,
    )
    db.add(event)
    db.flush()

    return event


def _root_observation() -> LineageObservation:
    return LineageObservation(
        observation_kind="materialization",
        source_external_id="policy-source",
        snapshot_external_id="external-snapshot-v1",
        asset_external_id="policy-chunk",
        asset_kind="chunk",
        revision_external_id="policy-chunk@v1",
        sequence=1,
    )


def _index_observation() -> LineageObservation:
    return LineageObservation(
        observation_kind="materialization",
        source_external_id="policy-source",
        snapshot_external_id="external-snapshot-v1",
        asset_external_id="policy-index",
        asset_kind="index",
        revision_external_id="policy-index@v1",
        sequence=1,
        dependencies=(
            LineageRevisionReference(
                asset_external_id="policy-chunk",
                revision_external_id="policy-chunk@v1",
                dependency_type="indexed_from",
            ),
        ),
    )


def _setup_event(
    db,
    *,
    organization: Organization,
    correlate_root: bool,
    analysis_status: str = "completed",
    vector_analysis_status: str = VECTOR_ANALYSIS_STATUS_COMPLETED,
) -> SemanticDriftEvent:
    page = _create_page(
        db,
        organization=organization,
    )

    old_snapshot = _create_snapshot(
        db,
        organization=organization,
        page=page,
        raw_content="Refund period is 30 days.",
    )
    new_snapshot = _create_snapshot(
        db,
        organization=organization,
        page=page,
        raw_content="Refund period is 14 days.",
    )

    if correlate_root:
        root = persist_lineage_observation(
            db,
            organization_id=organization.id,
            observation=_root_observation(),
        )
        persist_lineage_observation(
            db,
            organization_id=organization.id,
            observation=_index_observation(),
        )
        persist_temporal_lineage_correlation(
            db,
            organization_id=organization.id,
            page_snapshot_id=old_snapshot.id,
            lineage_observation_id=root.record.id,
        )

    return _create_event(
        db,
        organization=organization,
        page=page,
        old_snapshot=old_snapshot,
        new_snapshot=new_snapshot,
        analysis_status=analysis_status,
        vector_analysis_status=vector_analysis_status,
    )


def _delete_organization(
    organization_id: uuid.UUID,
) -> None:
    with SessionLocal() as db:
        db.rollback()

        snapshot_ids = db.query(PageSnapshot.id).filter(
            PageSnapshot.organization_id == organization_id,
        )

        db.execute(
            delete(TemporalLineageCorrelationRecord).where(
                TemporalLineageCorrelationRecord.page_snapshot_id.in_(
                    snapshot_ids
                )
            )
        )
        db.execute(
            delete(
                TemporalLineageInvalidationDecisionProvenanceRecord
            ).where(
                TemporalLineageInvalidationDecisionProvenanceRecord.organization_id
                == organization_id
            )
        )
        db.execute(
            delete(
                LineageInvalidationExecutionVerificationRecord
            ).where(
                LineageInvalidationExecutionVerificationRecord.organization_id
                == organization_id
            )
        )
        db.execute(
            delete(LineageInvalidationExecutionResultRecord).where(
                LineageInvalidationExecutionResultRecord.organization_id
                == organization_id
            )
        )
        db.execute(
            delete(LineageInvalidationExecutionAttemptRecord).where(
                LineageInvalidationExecutionAttemptRecord.organization_id
                == organization_id
            )
        )
        db.execute(
            delete(LineageInvalidationExecutionIntentRecord).where(
                LineageInvalidationExecutionIntentRecord.organization_id
                == organization_id
            )
        )
        db.execute(
            delete(LineageInvalidationDecisionRecord).where(
                LineageInvalidationDecisionRecord.organization_id
                == organization_id
            )
        )
        db.execute(
            delete(LineageInvalidationPolicyActivationRecord).where(
                LineageInvalidationPolicyActivationRecord.organization_id
                == organization_id
            )
        )
        db.execute(
            delete(LineageObservationRecord).where(
                LineageObservationRecord.organization_id
                == organization_id
            )
        )
        db.execute(
            delete(LineageExecutionTargetBindingRecord).where(
                LineageExecutionTargetBindingRecord.organization_id
                == organization_id
            )
        )
        db.execute(
            delete(SemanticDriftEvent).where(
                SemanticDriftEvent.organization_id
                == organization_id
            )
        )
        db.execute(
            delete(ApiKey).where(
                ApiKey.organization_id == organization_id
            )
        )
        db.execute(
            delete(PageSnapshot).where(
                PageSnapshot.organization_id
                == organization_id
            )
        )
        db.execute(
            delete(MonitoredPage).where(
                MonitoredPage.organization_id
                == organization_id
            )
        )
        db.execute(
            delete(Entity).where(
                Entity.organization_id == organization_id
            )
        )
        db.execute(
            delete(Organization).where(
                Organization.id == organization_id
            )
        )
        db.commit()


def _decision_count(
    organization_id: uuid.UUID,
) -> int:
    with SessionLocal() as db:
        return (
            db.query(LineageInvalidationDecisionRecord)
            .filter(
                LineageInvalidationDecisionRecord.organization_id
                == organization_id
            )
            .count()
        )


def _provenance_count(
    organization_id: uuid.UUID,
    *,
    event_id: uuid.UUID | None = None,
) -> int:
    with SessionLocal() as db:
        query = db.query(
            TemporalLineageInvalidationDecisionProvenanceRecord
        ).filter(
            TemporalLineageInvalidationDecisionProvenanceRecord.organization_id
            == organization_id
        )

        if event_id is not None:
            query = query.filter(
                TemporalLineageInvalidationDecisionProvenanceRecord.semantic_drift_event_id
                == event_id
            )

        return query.count()


def test_temporal_invalidation_analysis_requires_write_permission() -> None:
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="temporal-analysis-read-only",
            )
            organization_id = organization.id
            raw_key = _create_api_key(
                db,
                organization_id=organization.id,
                write=False,
            )
            event = _setup_event(
                db,
                organization=organization,
                correlate_root=False,
            )
            event_id = event.id
            db.commit()

        client = TestClient(app)

        try:
            response = client.post(
                "/api/lineage/temporal-invalidation-analysis",
                headers={
                    "X-API-Key": raw_key,
                },
                json={
                    "event_id": str(event_id),
                },
            )

            assert response.status_code == 403
            assert (
                response.json()["detail"]["error"]["code"]
                == "API_KEY_004"
            )
        finally:
            client.close()
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)


def test_temporal_invalidation_analysis_persists_exact_decision_and_replays() -> None:
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="temporal-analysis-replay",
            )
            organization_id = organization.id
            raw_key = _create_api_key(
                db,
                organization_id=organization.id,
                write=True,
            )
            event = _setup_event(
                db,
                organization=organization,
                correlate_root=True,
            )
            event_id = event.id
            old_snapshot_id = event.old_snapshot_id
            new_snapshot_id = event.new_snapshot_id
            db.commit()

        client = TestClient(app)

        try:
            first = client.post(
                "/api/lineage/temporal-invalidation-analysis",
                headers={
                    "X-API-Key": raw_key,
                },
                json={
                    "event_id": str(event_id),
                },
            )

            assert first.status_code == 200, first.text

            payload = first.json()

            assert payload["event_id"] == str(event_id)
            assert payload["old_snapshot_id"] == str(old_snapshot_id)
            assert payload["new_snapshot_id"] == str(new_snapshot_id)

            assert len(payload["analyses"]) == 1

            root_analysis = payload["analyses"][0]

            assert root_analysis["root"] == {
                "source_external_id": "policy-source",
                "asset_external_id": "policy-chunk",
                "revision_external_id": "policy-chunk@v1",
            }

            assert len(root_analysis["decisions"]) == 1

            decision = root_analysis["decisions"][0]

            decision_id = uuid.UUID(decision["id"])

            assert decision["target"] == {
                "source_external_id": "policy-source",
                "asset_external_id": "policy-index",
                "revision_external_id": "policy-index@v1",
            }
            assert decision["policy"] == {
                "policy_external_id":
                    "sde-default-lineage-invalidation",
                "revision": 1,
            }
            assert decision["disposition"] == "invalidate"
            assert (
                decision["reason_code"]
                == "causal_dependency_changed"
            )
            assert decision["causal_path"] == [
                {
                    "source_external_id": "policy-source",
                    "asset_external_id": "policy-chunk",
                    "revision_external_id": "policy-chunk@v1",
                },
                {
                    "source_external_id": "policy-source",
                    "asset_external_id": "policy-index",
                    "revision_external_id": "policy-index@v1",
                },
            ]

            assert _decision_count(organization_id) == 1
            assert _provenance_count(
                organization_id,
                event_id=event_id,
            ) == 1

            replay = client.post(
                "/api/lineage/temporal-invalidation-analysis",
                headers={
                    "X-API-Key": raw_key,
                },
                json={
                    "event_id": str(event_id),
                },
            )

            assert replay.status_code == 200, replay.text

            replay_decision = (
                replay.json()["analyses"][0]["decisions"][0]
            )

            assert uuid.UUID(replay_decision["id"]) == decision_id
            assert _decision_count(organization_id) == 1
            assert _provenance_count(
                organization_id,
                event_id=event_id,
            ) == 1
        finally:
            client.close()
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)


def test_temporal_invalidation_analysis_is_tenant_isolated() -> None:
    first_organization_id = None
    second_organization_id = None

    try:
        with SessionLocal() as db:
            first_organization = _create_organization(
                db,
                prefix="temporal-analysis-tenant-a",
            )
            first_organization_id = first_organization.id

            second_organization = _create_organization(
                db,
                prefix="temporal-analysis-tenant-b",
            )
            second_organization_id = second_organization.id

            raw_key = _create_api_key(
                db,
                organization_id=second_organization.id,
                write=True,
            )

            event = _setup_event(
                db,
                organization=first_organization,
                correlate_root=False,
            )
            event_id = event.id
            db.commit()

        client = TestClient(app)

        try:
            response = client.post(
                "/api/lineage/temporal-invalidation-analysis",
                headers={
                    "X-API-Key": raw_key,
                },
                json={
                    "event_id": str(event_id),
                },
            )

            assert response.status_code == 404
            assert (
                response.json()["detail"]["error"]["code"]
                == "LINEAGE_008"
            )
        finally:
            client.close()
    finally:
        if first_organization_id is not None:
            _delete_organization(first_organization_id)
        if second_organization_id is not None:
            _delete_organization(second_organization_id)


def test_temporal_invalidation_analysis_fails_closed_when_not_ready() -> None:
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="temporal-analysis-not-ready",
            )
            organization_id = organization.id
            raw_key = _create_api_key(
                db,
                organization_id=organization.id,
                write=True,
            )

            event = _setup_event(
                db,
                organization=organization,
                correlate_root=True,
                analysis_status="pending",
            )
            event_id = event.id
            db.commit()

        client = TestClient(app)

        try:
            response = client.post(
                "/api/lineage/temporal-invalidation-analysis",
                headers={
                    "X-API-Key": raw_key,
                },
                json={
                    "event_id": str(event_id),
                },
            )

            assert response.status_code == 409
            assert (
                response.json()["detail"]["error"]["code"]
                == "LINEAGE_009"
            )
            assert _decision_count(organization_id) == 0
            assert _provenance_count(
                organization_id,
                event_id=event_id,
            ) == 0
        finally:
            client.close()
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)


def test_temporal_invalidation_analysis_returns_deterministic_noop_without_roots() -> None:
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="temporal-analysis-noop",
            )
            organization_id = organization.id
            raw_key = _create_api_key(
                db,
                organization_id=organization.id,
                write=True,
            )

            event = _setup_event(
                db,
                organization=organization,
                correlate_root=False,
            )
            event_id = event.id
            old_snapshot_id = event.old_snapshot_id
            new_snapshot_id = event.new_snapshot_id
            db.commit()

        client = TestClient(app)

        try:
            response = client.post(
                "/api/lineage/temporal-invalidation-analysis",
                headers={
                    "X-API-Key": raw_key,
                },
                json={
                    "event_id": str(event_id),
                },
            )

            assert response.status_code == 200, response.text

            payload = response.json()

            assert payload == {
                "event_id": str(event_id),
                "old_snapshot_id": str(old_snapshot_id),
                "new_snapshot_id": str(new_snapshot_id),
                "analyses": [],
            }
            assert _decision_count(organization_id) == 0
            assert _provenance_count(
                organization_id,
                event_id=event_id,
            ) == 0
        finally:
            client.close()
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)


def test_temporal_invalidation_resolution_requires_api_key() -> None:
    client = TestClient(app)

    try:
        response = client.get(
            (
                "/api/lineage/temporal-invalidation-resolution/"
                f"{uuid.uuid4()}"
            )
        )

        assert response.status_code == 401
    finally:
        client.close()


def test_temporal_invalidation_resolution_projects_persisted_decision() -> None:
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="temporal-resolution",
            )
            organization_id = organization.id
            raw_key = _create_api_key(
                db,
                organization_id=organization.id,
                write=True,
            )
            event = _setup_event(
                db,
                organization=organization,
                correlate_root=True,
            )
            event_id = event.id
            old_snapshot_id = event.old_snapshot_id
            new_snapshot_id = event.new_snapshot_id
            db.commit()

        client = TestClient(app)

        try:
            analysis_response = client.post(
                "/api/lineage/temporal-invalidation-analysis",
                headers={
                    "X-API-Key": raw_key,
                },
                json={
                    "event_id": str(event_id),
                },
            )

            assert analysis_response.status_code == 200
            decision_id = (
                analysis_response.json()["analyses"][0]["decisions"][0]["id"]
            )

            response = client.get(
                (
                    "/api/lineage/temporal-invalidation-resolution/"
                    f"{event_id}"
                ),
                headers={
                    "X-API-Key": raw_key,
                },
            )

            assert response.status_code == 200, response.text

            payload = response.json()

            assert payload["event_id"] == str(event_id)
            assert payload["old_snapshot_id"] == str(old_snapshot_id)
            assert payload["new_snapshot_id"] == str(new_snapshot_id)
            assert len(payload["decisions"]) == 1
            assert payload["summary"] == {
                "historical_impact": {
                    "root_count": 1,
                    "target_count": 1,
                    "no_action_count": 0,
                    "invalidate_count": 1,
                    "rematerialize_count": 0,
                },
                "current_control": {
                    "eligible_count": 0,
                    "authorized_count": 0,
                    "attempted_count": 0,
                    "succeeded_count": 0,
                    "failed_count": 0,
                    "awaiting_evidence_count": 0,
                    "stale_dependency_present_count": 0,
                    "verified_count": 0,
                },
                "control_state_distribution": {
                    "historical_no_action_count": 0,
                    "blocked_current_state_count": 1,
                    "eligible_awaiting_authority_count": 0,
                    "authorized_awaiting_execution_count": 0,
                    "execution_attempt_recorded_count": 0,
                    "execution_failed_count": 0,
                    "execution_succeeded_unverified_count": 0,
                    "awaiting_evidence_count": 0,
                    "stale_dependency_present_count": 0,
                    "verified_count": 0,
                },
            }

            projected = payload["decisions"][0]

            assert projected["control_state"] == "blocked_current_state"
            assert projected["decision"]["id"] == decision_id
            assert projected["decision"]["disposition"] == "invalidate"
            assert projected["decision"]["reason_code"] == (
                "causal_dependency_changed"
            )
            assert projected["execution"] == {
                "intent": None,
                "latest_attempt": None,
                "result": None,
                "verification": None,
            }
        finally:
            client.close()
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)


def test_temporal_invalidation_resolution_closes_verified_integrity_loop() -> None:
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="temporal-resolution-integrity-loop",
            )
            organization_id = organization.id
            raw_key = _create_api_key(
                db,
                organization_id=organization.id,
                write=True,
            )
            event = _setup_event(
                db,
                organization=organization,
                correlate_root=True,
            )
            event_id = event.id
            db.commit()

        client = TestClient(app)

        try:
            analysis_response = client.post(
                "/api/lineage/temporal-invalidation-analysis",
                headers={
                    "X-API-Key": raw_key,
                },
                json={
                    "event_id": str(event_id),
                },
            )

            assert analysis_response.status_code == 200, analysis_response.text

            analysis_payload = analysis_response.json()
            decision = analysis_payload["analyses"][0]["decisions"][0]
            decision_id = uuid.UUID(decision["id"])

            assert decision["disposition"] == "invalidate"
            assert decision["causal_path"] == [
                {
                    "source_external_id": "policy-source",
                    "asset_external_id": "policy-chunk",
                    "revision_external_id": "policy-chunk@v1",
                },
                {
                    "source_external_id": "policy-source",
                    "asset_external_id": "policy-index",
                    "revision_external_id": "policy-index@v1",
                },
            ]
            assert _provenance_count(
                organization_id,
                event_id=event_id,
            ) == 1

            with SessionLocal() as db:
                activate_lineage_invalidation_policy(
                    db,
                    organization_id=organization_id,
                    policy=LineageInvalidationPolicyIdentity(
                        policy_external_id="sde-default-lineage-invalidation",
                        revision=1,
                    ),
                )
                persist_lineage_execution_target_binding(
                    db,
                    organization_id=organization_id,
                    binding=LineageExecutionTargetBinding(
                        identity=LineageExecutionTargetIdentity(
                            source_external_id="policy-source",
                            asset_external_id="policy-index",
                        ),
                        binding_revision=1,
                        provider="pgvector",
                        target_kind="langchain_pgvector_causal_chunk",
                        configuration={
                            "connection_ref": "test-downstream",
                            "collection_name": "policy-index",
                        },
                    ),
                )
                intent = authorize_lineage_invalidation_execution(
                    db,
                    organization_id=organization_id,
                    decision_record_id=decision_id,
                )
                db.commit()

                claim = claim_lineage_invalidation_execution(
                    db,
                    organization_id=organization_id,
                    intent_id=intent.record.id,
                    lease_seconds=300,
                )
                db.commit()

                result = persist_lineage_invalidation_execution_result(
                    db,
                    organization_id=organization_id,
                    attempt_id=claim.record.id,
                    outcome="succeeded",
                )
                execution_result_id = result.record.id
                db.commit()

            awaiting_response = client.get(
                (
                    "/api/lineage/temporal-invalidation-resolution/"
                    f"{event_id}"
                ),
                headers={
                    "X-API-Key": raw_key,
                },
            )

            assert awaiting_response.status_code == 200, awaiting_response.text

            awaiting_payload = awaiting_response.json()
            assert len(awaiting_payload["decisions"]) == 1
            assert awaiting_payload["summary"]["current_control"] == {
                "eligible_count": 1,
                "authorized_count": 1,
                "attempted_count": 1,
                "succeeded_count": 1,
                "failed_count": 0,
                "awaiting_evidence_count": 1,
                "stale_dependency_present_count": 0,
                "verified_count": 0,
            }

            assert awaiting_payload["summary"][
                "control_state_distribution"
            ] == {
                "historical_no_action_count": 0,
                "blocked_current_state_count": 0,
                "eligible_awaiting_authority_count": 0,
                "authorized_awaiting_execution_count": 0,
                "execution_attempt_recorded_count": 0,
                "execution_failed_count": 0,
                "execution_succeeded_unverified_count": 0,
                "awaiting_evidence_count": 1,
                "stale_dependency_present_count": 0,
                "verified_count": 0,
            }

            awaiting_decision = awaiting_payload["decisions"][0]
            assert awaiting_decision["control_state"] == "awaiting_evidence"
            assert awaiting_decision["decision"]["id"] == str(decision_id)
            assert awaiting_decision["decision"]["causal_path"] == (
                decision["causal_path"]
            )
            assert awaiting_decision["execution"]["result"]["outcome"] == (
                "succeeded"
            )
            assert (
                awaiting_decision["execution"]["result"]["id"]
                == str(execution_result_id)
            )

            awaiting_verification = (
                awaiting_decision["execution"]["verification"]
            )
            assert awaiting_verification["state"] == "awaiting_evidence"
            assert (
                awaiting_verification["execution_result_id"]
                == str(execution_result_id)
            )
            assert awaiting_verification["verification_record_id"] is None
            assert awaiting_verification["observation_id"] is None
            assert awaiting_verification["stale_dependency"] == {
                "source_external_id": "policy-source",
                "asset_external_id": "policy-chunk",
                "revision_external_id": "policy-chunk@v1",
            }

            with SessionLocal() as db:
                evidence = persist_lineage_observation(
                    db,
                    organization_id=organization_id,
                    observation=LineageObservation(
                        observation_kind="materialization",
                        source_external_id="policy-source",
                        snapshot_external_id="external-snapshot-v2",
                        asset_external_id="policy-index",
                        asset_kind="index",
                        revision_external_id="policy-index@v2",
                        sequence=2,
                        dependencies=(
                            LineageRevisionReference(
                                asset_external_id="policy-chunk",
                                revision_external_id="policy-chunk@v2",
                                dependency_type="indexed_from",
                            ),
                        ),
                    ),
                ).record
                evidence_id = evidence.id
                db.commit()

            publication_response = client.post(
                "/api/lineage/invalidation-execution-verifications",
                headers={
                    "X-API-Key": raw_key,
                },
                json={
                    "execution_result_id": str(execution_result_id),
                    "observation_id": str(evidence_id),
                },
            )

            assert (
                publication_response.status_code == 200
            ), publication_response.text

            publication_payload = publication_response.json()
            assert (
                publication_payload["execution_result_id"]
                == str(execution_result_id)
            )
            assert publication_payload["observation_id"] == str(evidence_id)
            assert publication_payload["created"] is True
            verification_record_id = publication_payload["id"]

            verified_response = client.get(
                (
                    "/api/lineage/temporal-invalidation-resolution/"
                    f"{event_id}"
                ),
                headers={
                    "X-API-Key": raw_key,
                },
            )

            assert verified_response.status_code == 200, verified_response.text

            verified_payload = verified_response.json()
            assert len(verified_payload["decisions"]) == 1
            assert verified_payload["summary"]["current_control"] == {
                "eligible_count": 0,
                "authorized_count": 1,
                "attempted_count": 1,
                "succeeded_count": 1,
                "failed_count": 0,
                "awaiting_evidence_count": 0,
                "stale_dependency_present_count": 0,
                "verified_count": 1,
            }

            assert verified_payload["summary"][
                "control_state_distribution"
            ] == {
                "historical_no_action_count": 0,
                "blocked_current_state_count": 0,
                "eligible_awaiting_authority_count": 0,
                "authorized_awaiting_execution_count": 0,
                "execution_attempt_recorded_count": 0,
                "execution_failed_count": 0,
                "execution_succeeded_unverified_count": 0,
                "awaiting_evidence_count": 0,
                "stale_dependency_present_count": 0,
                "verified_count": 1,
            }

            verified_decision = verified_payload["decisions"][0]

            assert verified_decision["control_state"] == "verified"
            assert verified_decision["decision"]["id"] == str(decision_id)
            assert verified_decision["decision"]["causal_path"] == (
                decision["causal_path"]
            )
            assert verified_decision["execution"]["result"]["outcome"] == (
                "succeeded"
            )

            verification = verified_decision["execution"]["verification"]

            assert verification["state"] == "verified"
            assert (
                verification["execution_result_id"]
                == str(execution_result_id)
            )
            assert (
                verification["verification_record_id"]
                == verification_record_id
            )
            assert verification["observation_id"] == str(evidence_id)
            assert verification["stale_dependency"] == {
                "source_external_id": "policy-source",
                "asset_external_id": "policy-chunk",
                "revision_external_id": "policy-chunk@v1",
            }

            assert "safe" not in verified_payload
            assert "resolved" not in verified_payload
            assert "integrity_restored" not in verified_payload
        finally:
            client.close()
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)


def test_temporal_invalidation_resolution_without_provenance_is_empty() -> None:
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="temporal-resolution-empty",
            )
            organization_id = organization.id
            raw_key = _create_api_key(
                db,
                organization_id=organization.id,
                write=False,
            )
            event = _setup_event(
                db,
                organization=organization,
                correlate_root=False,
            )
            event_id = event.id
            old_snapshot_id = event.old_snapshot_id
            new_snapshot_id = event.new_snapshot_id
            db.commit()

        client = TestClient(app)

        try:
            response = client.get(
                (
                    "/api/lineage/temporal-invalidation-resolution/"
                    f"{event_id}"
                ),
                headers={
                    "X-API-Key": raw_key,
                },
            )

            assert response.status_code == 200, response.text
            assert response.json() == {
                "event_id": str(event_id),
                "old_snapshot_id": str(old_snapshot_id),
                "new_snapshot_id": str(new_snapshot_id),
                "summary": {
                    "historical_impact": {
                        "root_count": 0,
                        "target_count": 0,
                        "no_action_count": 0,
                        "invalidate_count": 0,
                        "rematerialize_count": 0,
                    },
                    "current_control": {
                        "eligible_count": 0,
                        "authorized_count": 0,
                        "attempted_count": 0,
                        "succeeded_count": 0,
                        "failed_count": 0,
                        "awaiting_evidence_count": 0,
                        "stale_dependency_present_count": 0,
                        "verified_count": 0,
                    },
                    "control_state_distribution": {
                        "historical_no_action_count": 0,
                        "blocked_current_state_count": 0,
                        "eligible_awaiting_authority_count": 0,
                        "authorized_awaiting_execution_count": 0,
                        "execution_attempt_recorded_count": 0,
                        "execution_failed_count": 0,
                        "execution_succeeded_unverified_count": 0,
                        "awaiting_evidence_count": 0,
                        "stale_dependency_present_count": 0,
                        "verified_count": 0,
                    },
                },
                "decisions": [],
            }
        finally:
            client.close()
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)


def test_temporal_invalidation_resolution_hides_cross_tenant_event() -> None:
    first_organization_id = None
    second_organization_id = None

    try:
        with SessionLocal() as db:
            first_organization = _create_organization(
                db,
                prefix="temporal-resolution-owner",
            )
            first_organization_id = first_organization.id

            second_organization = _create_organization(
                db,
                prefix="temporal-resolution-other",
            )
            second_organization_id = second_organization.id

            raw_key = _create_api_key(
                db,
                organization_id=second_organization.id,
                write=False,
            )
            event = _setup_event(
                db,
                organization=first_organization,
                correlate_root=False,
            )
            event_id = event.id
            db.commit()

        client = TestClient(app)

        try:
            response = client.get(
                (
                    "/api/lineage/temporal-invalidation-resolution/"
                    f"{event_id}"
                ),
                headers={
                    "X-API-Key": raw_key,
                },
            )

            assert response.status_code == 404
            assert response.json() == {
                "detail": {
                    "error": {
                        "code": "LINEAGE_008",
                        "message": "Temporal drift event not found.",
                        "details": None,
                    }
                }
            }
        finally:
            client.close()
    finally:
        if first_organization_id is not None:
            _delete_organization(first_organization_id)
        if second_organization_id is not None:
            _delete_organization(second_organization_id)


def test_temporal_event_integrity_projects_authenticated_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="browser-temporal-integrity",
            )
            organization_id = organization.id

            raw_key = _create_api_key(
                db,
                organization_id=organization.id,
                write=True,
            )
            event = _setup_event(
                db,
                organization=organization,
                correlate_root=True,
            )
            event_id = event.id
            old_snapshot_id = event.old_snapshot_id
            new_snapshot_id = event.new_snapshot_id
            db.commit()

        public_client = TestClient(app)

        try:
            analysis_response = public_client.post(
                "/api/lineage/temporal-invalidation-analysis",
                headers={
                    "X-API-Key": raw_key,
                },
                json={
                    "event_id": str(event_id),
                },
            )
            assert analysis_response.status_code == 200
            decision_id = (
                analysis_response.json()["analyses"][0]["decisions"][0]["id"]
            )
        finally:
            public_client.close()

        _allow_browser_auth_middleware(
            monkeypatch
        )
        _override_browser_user(
            organization_id
        )

        browser_client = TestClient(app)

        try:
            response = browser_client.get(
                f"/api/temporal/events/{event_id}/integrity",
                headers=_BROWSER_AUTH_HEADERS,
            )

            assert response.status_code == 200, response.text

            payload = response.json()

            assert payload["event_id"] == str(event_id)
            assert payload["old_snapshot_id"] == str(
                old_snapshot_id
            )
            assert payload["new_snapshot_id"] == str(
                new_snapshot_id
            )
            assert len(payload["decisions"]) == 1
            assert payload["summary"] == {
                "historical_impact": {
                    "root_count": 1,
                    "target_count": 1,
                    "no_action_count": 0,
                    "invalidate_count": 1,
                    "rematerialize_count": 0,
                },
                "current_control": {
                    "eligible_count": 0,
                    "authorized_count": 0,
                    "attempted_count": 0,
                    "succeeded_count": 0,
                    "failed_count": 0,
                    "awaiting_evidence_count": 0,
                    "stale_dependency_present_count": 0,
                    "verified_count": 0,
                },
                "control_state_distribution": {
                    "historical_no_action_count": 0,
                    "blocked_current_state_count": 1,
                    "eligible_awaiting_authority_count": 0,
                    "authorized_awaiting_execution_count": 0,
                    "execution_attempt_recorded_count": 0,
                    "execution_failed_count": 0,
                    "execution_succeeded_unverified_count": 0,
                    "awaiting_evidence_count": 0,
                    "stale_dependency_present_count": 0,
                    "verified_count": 0,
                },
            }

            projected = payload["decisions"][0]

            assert projected["control_state"] == "blocked_current_state"
            assert projected["decision"]["id"] == decision_id
            assert (
                projected["decision"]["disposition"]
                == "invalidate"
            )
            assert projected["decision"]["reason_code"] == (
                "causal_dependency_changed"
            )
            assert projected["execution"] == {
                "intent": None,
                "latest_attempt": None,
                "result": None,
                "verification": None,
            }

            for forbidden_field in (
                "safe",
                "resolved",
                "integrity_restored",
                "overall_status",
                "health_score",
            ):
                assert forbidden_field not in payload
        finally:
            browser_client.close()
            _clear_browser_overrides()
    finally:
        if organization_id is not None:
            _delete_organization(
                organization_id
            )


def test_temporal_event_integrity_preserves_empty_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="browser-temporal-integrity-empty",
            )
            organization_id = organization.id

            event = _setup_event(
                db,
                organization=organization,
                correlate_root=False,
            )
            event_id = event.id
            old_snapshot_id = event.old_snapshot_id
            new_snapshot_id = event.new_snapshot_id
            db.commit()

        _allow_browser_auth_middleware(
            monkeypatch
        )
        _override_browser_user(
            organization_id
        )

        client = TestClient(app)

        try:
            response = client.get(
                f"/api/temporal/events/{event_id}/integrity",
                headers=_BROWSER_AUTH_HEADERS,
            )

            assert response.status_code == 200, response.text
            assert response.json() == {
                "event_id": str(event_id),
                "old_snapshot_id": str(old_snapshot_id),
                "new_snapshot_id": str(new_snapshot_id),
                "summary": {
                    "historical_impact": {
                        "root_count": 0,
                        "target_count": 0,
                        "no_action_count": 0,
                        "invalidate_count": 0,
                        "rematerialize_count": 0,
                    },
                    "current_control": {
                        "eligible_count": 0,
                        "authorized_count": 0,
                        "attempted_count": 0,
                        "succeeded_count": 0,
                        "failed_count": 0,
                        "awaiting_evidence_count": 0,
                        "stale_dependency_present_count": 0,
                        "verified_count": 0,
                    },
                    "control_state_distribution": {
                        "historical_no_action_count": 0,
                        "blocked_current_state_count": 0,
                        "eligible_awaiting_authority_count": 0,
                        "authorized_awaiting_execution_count": 0,
                        "execution_attempt_recorded_count": 0,
                        "execution_failed_count": 0,
                        "execution_succeeded_unverified_count": 0,
                        "awaiting_evidence_count": 0,
                        "stale_dependency_present_count": 0,
                        "verified_count": 0,
                    },
                },
                "decisions": [],
            }
        finally:
            client.close()
            _clear_browser_overrides()
    finally:
        if organization_id is not None:
            _delete_organization(
                organization_id
            )


def test_temporal_event_integrity_hides_cross_tenant_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_organization_id = None
    other_organization_id = None

    try:
        with SessionLocal() as db:
            owner_organization = _create_organization(
                db,
                prefix="browser-temporal-integrity-owner",
            )
            owner_organization_id = owner_organization.id

            other_organization = _create_organization(
                db,
                prefix="browser-temporal-integrity-other",
            )
            other_organization_id = other_organization.id

            event = _setup_event(
                db,
                organization=owner_organization,
                correlate_root=False,
            )
            event_id = event.id
            db.commit()

        _allow_browser_auth_middleware(
            monkeypatch
        )
        _override_browser_user(
            other_organization_id
        )

        client = TestClient(app)

        try:
            response = client.get(
                f"/api/temporal/events/{event_id}/integrity",
                headers=_BROWSER_AUTH_HEADERS,
            )

            assert response.status_code == 404
            assert response.json() == {
                "detail": {
                    "error": {
                        "code": "LINEAGE_008",
                        "message": "Temporal drift event not found.",
                        "details": None,
                    }
                }
            }
        finally:
            client.close()
            _clear_browser_overrides()
    finally:
        if owner_organization_id is not None:
            _delete_organization(
                owner_organization_id
            )
        if other_organization_id is not None:
            _delete_organization(
                other_organization_id
            )



def test_temporal_event_evidence_projects_exact_persisted_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="browser-temporal-evidence",
            )
            organization_id = organization.id

            event = _setup_event(
                db,
                organization=organization,
                correlate_root=False,
            )
            event_id = event.id
            old_snapshot_id = event.old_snapshot_id
            new_snapshot_id = event.new_snapshot_id
            expected_delta = event.semantic_delta_json
            expected_analysis_status = event.analysis_status
            db.commit()

        _allow_browser_auth_middleware(
            monkeypatch
        )
        _override_browser_user(
            organization_id
        )

        client = TestClient(app)

        try:
            response = client.get(
                f"/api/temporal/events/{event_id}/evidence",
                headers=_BROWSER_AUTH_HEADERS,
            )

            assert response.status_code == 200, response.text

            payload = response.json()

            assert payload["event_id"] == str(event_id)
            assert payload["analysis_status"] == (
                expected_analysis_status
            )
            assert payload["delta"] == expected_delta

            assert payload["before"]["snapshot_id"] == str(
                old_snapshot_id
            )
            assert payload["after"]["snapshot_id"] == str(
                new_snapshot_id
            )

            assert payload["before"]["content"] == (
                "Refund period is 30 days."
            )
            assert payload["after"]["content"] == (
                "Refund period is 14 days."
            )

            assert "raw_content_path" not in payload["before"]
            assert "raw_content_path" not in payload["after"]
        finally:
            client.close()
            _clear_browser_overrides()
    finally:
        if organization_id is not None:
            _delete_organization(
                organization_id
            )


def test_temporal_event_evidence_hides_cross_tenant_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_organization_id = None
    other_organization_id = None

    try:
        with SessionLocal() as db:
            owner_organization = _create_organization(
                db,
                prefix="browser-temporal-evidence-owner",
            )
            owner_organization_id = owner_organization.id

            other_organization = _create_organization(
                db,
                prefix="browser-temporal-evidence-other",
            )
            other_organization_id = other_organization.id

            event = _setup_event(
                db,
                organization=owner_organization,
                correlate_root=False,
            )
            event_id = event.id
            db.commit()

        _allow_browser_auth_middleware(
            monkeypatch
        )
        _override_browser_user(
            other_organization_id
        )

        client = TestClient(app)

        try:
            response = client.get(
                f"/api/temporal/events/{event_id}/evidence",
                headers=_BROWSER_AUTH_HEADERS,
            )

            assert response.status_code == 404
            assert response.json() == {
                "detail": "Temporal drift event not found.",
            }
        finally:
            client.close()
            _clear_browser_overrides()
    finally:
        if owner_organization_id is not None:
            _delete_organization(
                owner_organization_id
            )
        if other_organization_id is not None:
            _delete_organization(
                other_organization_id
            )


def test_temporal_event_evidence_reports_unavailable_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(
                db,
                prefix="browser-temporal-evidence-unavailable",
            )
            organization_id = organization.id

            event = _setup_event(
                db,
                organization=organization,
                correlate_root=False,
            )
            event_id = event.id

            old_snapshot = db.get(
                PageSnapshot,
                event.old_snapshot_id,
            )
            assert old_snapshot is not None

            old_snapshot.raw_content = None
            old_snapshot.raw_content_path = None

            db.commit()

        _allow_browser_auth_middleware(
            monkeypatch
        )
        _override_browser_user(
            organization_id
        )

        client = TestClient(app)

        try:
            response = client.get(
                f"/api/temporal/events/{event_id}/evidence",
                headers=_BROWSER_AUTH_HEADERS,
            )

            assert response.status_code == 409
            assert response.json() == {
                "detail": (
                    "Persisted temporal event content "
                    "is unavailable."
                ),
            }

            with SessionLocal() as db:
                persisted_event = db.get(
                    SemanticDriftEvent,
                    event_id,
                )

                assert persisted_event is not None
        finally:
            client.close()
            _clear_browser_overrides()
    finally:
        if organization_id is not None:
            _delete_organization(
                organization_id
            )
