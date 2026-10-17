"""One real LangChain SDK -> PGVector -> SDE event/worker/HTTP -> model gate.

Test preconditions deliberately supply a completed classification. This proves
consumer enforcement, not autonomous AI semantic-change accuracy or recovery.
All writes require the repository's disposable test DBs and isolated API.
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from urllib.parse import urlsplit

import pytest
from sqlalchemy import text

from scripts.ci_real_lineage_admission_gate import require_disposable_lineage_databases
from scripts.vector_worker import VectorWorker
from src.core.database import SessionLocal
from src.domain.lineage_pgvector_collection import sde_pgvector_collection_metadata
from src.integrations.integrity_client import IntegrityRevision
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
from src.services.temporal_lineage_correlation_service import persist_temporal_lineage_correlation
from tests.integration.test_lineage_invalidation_execution_lifecycle import (
    DOWNSTREAM_DATABASE_URL,
    _chunk_materialization,
    _cleanup,
    _ensure_downstream_schema,
    _make_organization,
    _materialization,
)
from tests.integration.test_lineage_physical_recovery_acceptance import (
    _assert_http_block,
    _http_client,
)
from tests.integration.test_temporal_event_http_admission_acceptance import (
    _cleanup_source_evidence,
    _snapshot,
)

# Avoid a silent pytest skip if the optional real SDK is missing. The strict
# acceptance gate also rejects skipped cases at the JUnit level.
from langchain_core.documents import Document
from langchain_postgres import PGVector
from experiments.external_langchain_rag import DeterministicEmbeddings

def _require_disposable_downstream() -> str:
    require_disposable_lineage_databases()
    target = urlsplit(DOWNSTREAM_DATABASE_URL)
    if not (
        target.scheme == "postgresql"
        and target.hostname == "integration_downstream_db"
        and target.port == 5432
        and target.username == "downstream_test"
        and target.password == "downstream_test"
        and target.path == "/downstream_test"
        and not target.query
        and not target.fragment
    ):
        raise RuntimeError("Joined acceptance refuses non-disposable downstream DB")
    return DOWNSTREAM_DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)


def test_real_sdk_retrieval_is_blocked_by_persisted_event_over_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The LangChain document bytes still exist, but cannot reach the model."""
    pg_url = _require_disposable_downstream()
    authority = SessionLocal()
    org_ids: list[uuid.UUID] = []
    owner_client = None
    other_client = None
    store = None
    collection = f"sde_joined_{uuid.uuid4().hex}"
    try:
        # Uses physical SDK-created collection; no manually inserted fake row.
        from src.services.lineage_downstream_connection_service import (
            create_lineage_downstream_session,
            resolve_lineage_downstream_connection,
        )
        monkeypatch.setattr(
            "src.services.lineage_downstream_connection_service."
            "settings.DOWNSTREAM_DATABASE_URLS",
            json.dumps({"test-downstream": DOWNSTREAM_DATABASE_URL}),
        )
        downstream, downstream_engine = create_lineage_downstream_session(
            resolve_lineage_downstream_connection(connection_ref="test-downstream")
        )
        try:
            _ensure_downstream_schema(downstream)
        finally:
            downstream.close()
            downstream_engine.dispose()

        owner = _make_organization(authority)
        other = _make_organization(authority)
        org_ids.extend((owner.id, other.id))
        owner_client = _http_client(authority, owner.id)
        other_client = _http_client(authority, other.id)
        entity = Entity(
            id=uuid.uuid4(), organization_id=owner.id,
            name="Joined admission policy", domain="example.test", category="test",
        )
        authority.add(entity)
        authority.flush()
        page = MonitoredPage(
            id=uuid.uuid4(), organization_id=owner.id, entity_id=entity.id,
            url=f"https://example.test/joined/{uuid.uuid4().hex}",
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
        assert old_snapshot.content_hash == hashlib.sha256(old_content.encode()).hexdigest()
        assert old_snapshot.content_hash != new_snapshot.content_hash
        root = persist_lineage_observation(
            authority, organization_id=owner.id, observation=_chunk_materialization(),
        )
        persist_lineage_observation(
            authority, organization_id=owner.id, observation=_materialization(),
        )
        persist_temporal_lineage_correlation(
            authority, organization_id=owner.id,
            page_snapshot_id=old_snapshot.id,
            lineage_observation_id=root.record.id,
        )
        authority.commit()

        store = PGVector(
            embeddings=DeterministicEmbeddings(),
            connection=pg_url,
            embedding_length=DeterministicEmbeddings.dimensions,
            collection_name=collection,
            collection_metadata=sde_pgvector_collection_metadata(
                organization_id=owner.id,
                source_external_id="source-a",
                index_external_id="index-a",
            ),
            use_jsonb=True,
            create_extension=False,
        )
        inserted = store.add_documents([Document(
            id="chunk-a",
            page_content=old_content,
            metadata={
                "source_external_id": "source-a",
                "snapshot_external_id": str(old_snapshot.id),
                "chunk_revision_external_id": "chunk-a-r3",
            },
        )])
        assert inserted == ["chunk-a"]
        retrieved = store.similarity_search("What is the refund window?", k=1)
        assert len(retrieved) == 1
        assert retrieved[0].id == "chunk-a"
        assert retrieved[0].page_content == old_content
        assert retrieved[0].metadata["chunk_revision_external_id"] == "chunk-a-r3"
        assert retrieved[0].metadata["snapshot_external_id"] == str(old_snapshot.id)

        serving = IntegrityRevision(
            source_external_id="source-a",
            asset_external_id="index-a",
            revision_external_id="index-a-r7",
        )
        _assert_http_block(owner_client, serving, retrieved[0], "unknown_integrity")

        event = SemanticDriftEvent(
            id=uuid.uuid4(), monitored_page_id=page.id, organization_id=owner.id,
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
            snapshot_limit=1, event_limit=1,
            temporal_invalidation_limit=10, max_attempts=3,
            process_embeddings=False, process_analysis=False,
            process_temporal_invalidation=True, process_risk_scoring=False,
        )
        result = worker.process_temporal_invalidation_events()
        assert (result.selected, result.completed, result.failed) == (1, 1, 0)
        authority.expire_all()
        assert authority.get(SemanticDriftEvent, event.id).temporal_invalidation_status == (
            TEMPORAL_INVALIDATION_STATUS_COMPLETED
        )
        decision = authority.query(LineageInvalidationDecisionRecord).filter_by(
            organization_id=owner.id,
            target_asset_external_id="index-a",
            target_revision_external_id="index-a-r7",
        ).one()
        assert decision.disposition == "invalidate"
        assert decision.reason_code == "causal_dependency_changed"
        assert authority.query(TemporalLineageInvalidationDecisionProvenanceRecord).filter_by(
            organization_id=owner.id,
            semantic_drift_event_id=event.id,
            decision_id=decision.id,
        ).count() == 1
        authority.rollback()

        assert owner_client.query_target_integrity(serving).status == "affected"
        assert other_client.query_target_integrity(serving).status == "unknown"
        # SDK retrieval is genuinely repeated AFTER authority invalidation.
        still_retrieved = store.similarity_search("What is the refund window?", k=1)
        assert len(still_retrieved) == 1
        assert still_retrieved[0].page_content == old_content
        _assert_http_block(owner_client, serving, still_retrieved[0], "unresolved_impact")
        _assert_http_block(other_client, serving, still_retrieved[0], "unknown_integrity")
        assert worker.process_temporal_invalidation_events().selected == 0
    finally:
        if owner_client is not None:
            owner_client.close()
        if other_client is not None:
            other_client.close()
        authority.rollback()
        authority.close()
        if store is not None:
            store.delete_collection()  # only this UUID-randomized collection
        _cleanup_source_evidence(org_ids)
        _cleanup(org_ids)
