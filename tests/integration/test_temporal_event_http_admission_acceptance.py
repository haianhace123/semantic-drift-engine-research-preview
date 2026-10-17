"""A completed semantic event must reach a real external context gate.

All writes occur in disposable authority/downstream test PostgreSQL instances.
The change classification is an explicit, controlled precondition: this test
verifies the event-to-enforcement path, not LLM semantic classification quality.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid

import pytest
from sqlalchemy import delete, text

from scripts.vector_worker import VectorWorker
from src.core.database import SessionLocal
from src.integrations.integrity_client import IntegrityRevision
from src.integrations.langchain.context_admission import admit_langchain_context
from src.models.entity import Entity
from src.models.monitored_page import MonitoredPage
from src.models.page_snapshot import PageSnapshot
from src.models.semantic_drift_event import (
    SemanticDriftEvent,
    TEMPORAL_INVALIDATION_STATUS_COMPLETED,
    VECTOR_ANALYSIS_STATUS_COMPLETED,
)
from src.models.lineage_invalidation_decision import LineageInvalidationDecisionRecord
from src.models.temporal_lineage_invalidation_decision_provenance import (
    TemporalLineageInvalidationDecisionProvenanceRecord,
)
from src.services.lineage_observation_persistence_service import persist_lineage_observation
from src.services.temporal_lineage_correlation_service import (
    persist_temporal_lineage_correlation,
)
from src.services.lineage_downstream_connection_service import (
    create_lineage_downstream_session,
    resolve_lineage_downstream_connection,
)
from tests.integration.test_lineage_invalidation_execution_lifecycle import (
    DOWNSTREAM_DATABASE_URL,
    _chunk_materialization,
    _cleanup,
    _cleanup_downstream_collections,
    _ensure_downstream_schema,
    _insert_collection,
    _insert_document,
    _make_organization,
    _materialization,
)
from tests.integration.test_lineage_physical_recovery_acceptance import (
    _assert_http_block,
    _downstream_document,
    _http_client,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="Database integration tests are disabled.",
)


def _snapshot(db, *, organization_id, monitored_page_id, content: str):
    value = content.encode("utf-8")
    row = PageSnapshot(
        id=uuid.uuid4(),
        organization_id=organization_id,
        monitored_page_id=monitored_page_id,
        content_hash=hashlib.sha256(value).hexdigest(),
        raw_content=content,
        content_size=len(value),
    )
    db.add(row)
    db.flush()
    return row


def _cleanup_source_evidence(organization_ids: list[uuid.UUID]):
    if not organization_ids:
        return
    with SessionLocal() as db:
        # Provenance/correlation tables have ON DELETE CASCADE foreign keys.
        db.execute(delete(SemanticDriftEvent).where(
            SemanticDriftEvent.organization_id.in_(organization_ids)
        ))
        db.execute(delete(PageSnapshot).where(
            PageSnapshot.organization_id.in_(organization_ids)
        ))
        db.execute(delete(MonitoredPage).where(
            MonitoredPage.organization_id.in_(organization_ids)
        ))
        db.execute(delete(Entity).where(
            Entity.organization_id.in_(organization_ids)
        ))
        db.commit()


def test_classified_source_revision_reaches_http_context_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real worker decision blocks stale bytes still present in PGVector."""
    authority = SessionLocal()
    downstream = None
    downstream_engine = None
    organization_ids: list[uuid.UUID] = []
    collection_ids: list[uuid.UUID] = []
    owner_client = None
    other_client = None

    try:
        monkeypatch.setattr(
            "src.services.lineage_downstream_connection_service."
            "settings.DOWNSTREAM_DATABASE_URLS",
            json.dumps({"test-downstream": DOWNSTREAM_DATABASE_URL}),
        )
        downstream, downstream_engine = create_lineage_downstream_session(
            resolve_lineage_downstream_connection(connection_ref="test-downstream")
        )
        _ensure_downstream_schema(downstream)

        owner = _make_organization(authority)
        other = _make_organization(authority)
        organization_ids.extend((owner.id, other.id))
        owner_client = _http_client(authority, owner.id)
        other_client = _http_client(authority, other.id)
        entity = Entity(
            id=uuid.uuid4(), organization_id=owner.id,
            name="Acceptance policy", domain="example.test", category="test",
        )
        authority.add(entity)
        authority.flush()
        page = MonitoredPage(
            id=uuid.uuid4(), organization_id=owner.id, entity_id=entity.id,
            url=f"https://example.test/acceptance/{uuid.uuid4().hex}",
            page_type="docs", crawl_frequency="daily", is_active=True,
        )
        authority.add(page)
        authority.flush()

        old_content = "Refunds are available for 30 days."
        new_content = "Refunds are available for 7 days."
        old_snapshot = _snapshot(
            authority, organization_id=owner.id,
            monitored_page_id=page.id, content=old_content,
        )
        new_snapshot = _snapshot(
            authority, organization_id=owner.id,
            monitored_page_id=page.id, content=new_content,
        )
        assert old_snapshot.content_hash != new_snapshot.content_hash

        root = persist_lineage_observation(
            authority, organization_id=owner.id,
            observation=_chunk_materialization(),
        )
        persist_lineage_observation(
            authority, organization_id=owner.id,
            observation=_materialization(),
        )
        persist_temporal_lineage_correlation(
            authority, organization_id=owner.id,
            page_snapshot_id=old_snapshot.id,
            lineage_observation_id=root.record.id,
        )

        collection_id = uuid.uuid4()
        collection_ids.append(collection_id)
        collection_name = f"sde-temporal-http-{uuid.uuid4().hex}"
        _insert_collection(
            downstream, collection_id=collection_id,
            collection_name=collection_name, organization_id=owner.id,
        )
        _insert_document(
            downstream, collection_id=collection_id,
            document_id="chunk-a", document_revision_external_id="chunk-a-r3",
        )
        downstream.execute(
            text("UPDATE langchain_pg_embedding SET document = :content "
                 "WHERE collection_id = :collection_id AND id = 'chunk-a'"),
            {"content": old_content, "collection_id": collection_id},
        )
        downstream.commit()
        physical_row = downstream.execute(
            text("SELECT cmetadata->>'source_external_id', "
                 "cmetadata->>'chunk_revision_external_id', document "
                 "FROM langchain_pg_embedding WHERE collection_id = :collection_id "
                 "AND id = 'chunk-a'"),
            {"collection_id": collection_id},
        ).one()
        document = _downstream_document(tuple(physical_row))
        assert document.page_content == old_content
        downstream.rollback()

        serving = IntegrityRevision(
            source_external_id="source-a",
            asset_external_id="index-a",
            revision_external_id="index-a-r7",
        )
        _assert_http_block(owner_client, serving, document, "unknown_integrity")

        # Semantic/vector classification is already completed by an upstream
        # authority; this acceptance deliberately does not invoke a provider.
        event = SemanticDriftEvent(
            id=uuid.uuid4(), monitored_page_id=page.id,
            organization_id=owner.id,
            old_snapshot_id=old_snapshot.id, new_snapshot_id=new_snapshot.id,
            semantic_delta_json={"changes": [{
                "before": "30 days", "after": "7 days",
                "kind": "policy_window_changed",
            }]},
            analysis_status="completed",
            vector_analysis_status=VECTOR_ANALYSIS_STATUS_COMPLETED,
            risk_level="high", confidence=1.0, impact_score=90,
            changes_count=1,
        )
        authority.add(event)
        authority.commit()

        worker = VectorWorker(
            snapshot_limit=1,
            event_limit=1,
            temporal_invalidation_limit=10,
            max_attempts=3,
            process_embeddings=False,
            process_analysis=False,
            process_temporal_invalidation=True,
            process_risk_scoring=False,
        )
        result = worker.process_temporal_invalidation_events()
        assert (result.selected, result.completed, result.failed) == (1, 1, 0)
        authority.expire_all()
        assert authority.get(SemanticDriftEvent, event.id).temporal_invalidation_status == (
            TEMPORAL_INVALIDATION_STATUS_COMPLETED
        )
        decisions = authority.query(LineageInvalidationDecisionRecord).filter(
            LineageInvalidationDecisionRecord.organization_id == owner.id,
            LineageInvalidationDecisionRecord.target_asset_external_id == "index-a",
            LineageInvalidationDecisionRecord.target_revision_external_id == "index-a-r7",
        ).all()
        assert len(decisions) == 1
        assert decisions[0].disposition == "invalidate"
        assert decisions[0].reason_code == "causal_dependency_changed"
        assert authority.query(TemporalLineageInvalidationDecisionProvenanceRecord).filter_by(
            organization_id=owner.id, semantic_drift_event_id=event.id,
            decision_id=decisions[0].id,
        ).count() == 1
        authority.rollback()

        assert owner_client.query_target_integrity(serving).status == "affected"
        assert other_client.query_target_integrity(serving).status == "unknown"
        _assert_http_block(owner_client, serving, document, "unresolved_impact")
        _assert_http_block(other_client, serving, document, "unknown_integrity")

        # No physical invalidation was authorized: the stale PGVector row
        # remains and the independent consumer still cannot serve it.
        downstream.rollback()
        still_present = downstream.execute(
            text("SELECT document FROM langchain_pg_embedding "
                 "WHERE collection_id = :collection_id AND id = 'chunk-a'"),
            {"collection_id": collection_id},
        ).scalar_one()
        assert still_present == old_content
        downstream.rollback()
        assert worker.process_temporal_invalidation_events().selected == 0
    finally:
        if owner_client is not None:
            owner_client.close()
        if other_client is not None:
            other_client.close()
        authority.rollback()
        authority.close()
        if downstream is not None:
            downstream.rollback()
            downstream.close()
        if downstream_engine is not None:
            downstream_engine.dispose()
        _cleanup_downstream_collections(collection_ids)
        _cleanup_source_evidence(organization_ids)
        _cleanup(organization_ids)
