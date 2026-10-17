from __future__ import annotations

import json
import os
import uuid

import pytest

from src.core.database import SessionLocal
from src.models.lineage_invalidation_execution_result import (
    LineageInvalidationExecutionResultRecord,
)
from src.services.lineage_downstream_connection_service import (
    create_lineage_downstream_session,
    resolve_lineage_downstream_connection,
)
from src.services.lineage_invalidation_execution_cycle_service import (
    LineageInvalidationExecutionCycleStatus,
    run_lineage_invalidation_execution_cycle,
)
from tests.integration.test_lineage_invalidation_execution_lifecycle import (
    DOWNSTREAM_DATABASE_URL,
    _cleanup,
    _cleanup_downstream_collections,
    _document_exists,
    _ensure_downstream_schema,
    _establish_claimable_intent,
    _insert_collection,
    _insert_document,
    _make_organization,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="Database integration tests are disabled.",
)


def test_noop_adapter_cannot_publish_success_while_stale_chunk_remains(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A committed no-op cannot masquerade as completed physical invalidation."""
    authority = SessionLocal()
    downstream = None
    engine = None
    organization_ids: list[uuid.UUID] = []
    collection_ids: list[uuid.UUID] = []

    try:
        monkeypatch.setattr(
            "src.services.lineage_downstream_connection_service."
            "settings.DOWNSTREAM_DATABASE_URLS",
            json.dumps({"test-downstream": DOWNSTREAM_DATABASE_URL}),
        )
        connection = resolve_lineage_downstream_connection(
            connection_ref="test-downstream",
        )
        downstream, engine = create_lineage_downstream_session(connection)
        _ensure_downstream_schema(downstream)

        org = _make_organization(authority)
        organization_ids.append(org.id)
        collection_id = uuid.uuid4()
        collection_ids.append(collection_id)
        collection_name = f"sde-noop-confirmation-{uuid.uuid4().hex}"
        _insert_collection(
            downstream,
            collection_id=collection_id,
            collection_name=collection_name,
            organization_id=org.id,
        )
        _insert_document(
            downstream,
            collection_id=collection_id,
            document_id="chunk-a",
        )
        downstream.commit()

        _establish_claimable_intent(
            authority,
            organization_id=org.id,
            collection_name=collection_name,
            document_id="chunk-a",
        )
        authority.commit()

        # Simulate a buggy adapter that claims success but does not delete.
        monkeypatch.setattr(
            "src.services.lineage_invalidation_execution_cycle_service."
            "invalidate_lineage_pgvector_causal_chunk",
            lambda db, *, configuration: None,
        )

        result = run_lineage_invalidation_execution_cycle(
            organization_id=org.id,
        )
        assert result.status == LineageInvalidationExecutionCycleStatus.FAILED
        assert result.reason_code == "downstream_physical_confirmation_failed"
        assert result.attempt_id is not None

        downstream.rollback()
        assert _document_exists(
            downstream,
            collection_name=collection_name,
            document_id="chunk-a",
        )
        authority.rollback()
        persisted = (
            authority.query(LineageInvalidationExecutionResultRecord)
            .filter(
                LineageInvalidationExecutionResultRecord.organization_id
                == org.id,
                LineageInvalidationExecutionResultRecord.attempt_id
                == result.attempt_id,
            )
            .one()
        )
        assert persisted.outcome == "failed"
    finally:
        authority.rollback()
        authority.close()
        if downstream is not None:
            downstream.rollback()
            downstream.close()
        if engine is not None:
            engine.dispose()
        _cleanup_downstream_collections(collection_ids)
        _cleanup(organization_ids)
