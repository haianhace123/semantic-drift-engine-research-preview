"""End-to-end contract: actual LangChain PGVector writes -> causal execution.

Optional adapter dependencies are installed in the dedicated integration gate.
The ordinary core image does not require LangChain at runtime.
"""

from __future__ import annotations

import json
import os
import uuid

import pytest
from src.core.database import SessionLocal
from src.domain.lineage_invalidation import LineageInvalidationDecision
from src.domain.lineage_revision import LineageRevisionIdentity
from src.integrations.langchain import (
    sde_pgvector_collection_metadata,
    index_langchain_documents_with_lineage,
    register_langchain_pgvector_causal_target,
)
from src.services.lineage_invalidation_execution_cycle_service import (
    LineageInvalidationExecutionCycleStatus,
    run_lineage_invalidation_execution_cycle,
)
from src.services.lineage_invalidation_execution_intent_service import (
    authorize_lineage_invalidation_execution,
)
from src.services.lineage_invalidation_persistence_service import (
    persist_lineage_invalidation_decision,
)
from src.services.lineage_invalidation_policy_activation_service import (
    activate_lineage_invalidation_policy,
)
from src.services.lineage_pgvector_preflight_service import (
    inspect_pgvector_causal_target,
)
from src.services.lineage_downstream_connection_service import (
    create_lineage_downstream_session,
    resolve_lineage_downstream_connection,
)
from tests.integration.test_lineage_invalidation_execution_lifecycle import (
    DOWNSTREAM_DATABASE_URL,
    _cleanup,
    _document_exists,
    _ensure_downstream_schema,
    _make_organization,
    _policy,
)


@pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)
def test_real_langchain_pgvector_materialization_executes_causally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delete only the authorized chunk after a real LangChain PGVector write."""
    Document = pytest.importorskip("langchain_core.documents").Document
    Embeddings = pytest.importorskip("langchain_core.embeddings").Embeddings
    PGVector = pytest.importorskip("langchain_postgres").PGVector

    class TestEmbeddings(Embeddings):
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[float(len(s) + 1)] + [0.0] * 7 for s in texts]

        def embed_query(self, text: str) -> list[float]:
            return self.embed_documents([text])[0]

    monkeypatch.setattr(
        "src.services.lineage_downstream_connection_service."
        "settings.DOWNSTREAM_DATABASE_URLS",
        json.dumps({"test-downstream": DOWNSTREAM_DATABASE_URL}),
    )
    connection = resolve_lineage_downstream_connection(
        connection_ref="test-downstream",
    )
    downstream, downstream_engine = create_lineage_downstream_session(connection)
    authority = SessionLocal()
    organization_ids: list[uuid.UUID] = []
    store = None
    collection_name = f"sde-real-pgvector-{uuid.uuid4().hex}"
    try:
        organization = _make_organization(authority)
        organization_ids.append(organization.id)
        _ensure_downstream_schema(downstream)
        psycopg_dsn = DOWNSTREAM_DATABASE_URL.replace(
            "postgresql://", "postgresql+psycopg://", 1
        )
        store = PGVector(
            embeddings=TestEmbeddings(),
            connection=psycopg_dsn,
            embedding_length=8,
            collection_name=collection_name,
            collection_metadata=sde_pgvector_collection_metadata(
                organization_id=organization.id,
                source_external_id="source-a",
                index_external_id="index-a",
            ),
            use_jsonb=True,
            create_extension=False,
        )
        unrelated_id = f"unrelated-{uuid.uuid4().hex}"
        indexed = index_langchain_documents_with_lineage(
            vector_store=store,
            authority_db=authority,
            organization_id=organization.id,
            source_external_id="source-a",
            documents=[
                Document(
                    id="chunk-a",
                    page_content="A changed refund rule.",
                    metadata={
                        "source_external_id": "source-a",
                        "snapshot_external_id": "snapshot-7",
                    },
                ),
            ],
            index_external_id="index-a",
            index_revision_external_id="index-a-r7",
            sequence=7,
            require_physical_readback=True,
        )
        assert indexed.point_in_time_readback_verified is True
        assert indexed.document_ids == ("chunk-a",)
        authorized_revision = indexed.materialization.observations[0].revision_external_id
        unrelated = Document(
            id=unrelated_id,
            page_content="An unrelated billing rule.",
            metadata={
                "source_external_id": "source-a",
                "snapshot_external_id": "snapshot-7",
                "chunk_revision_external_id": authorized_revision,
            },
        )
        assert store.add_documents([unrelated]) == [unrelated_id]
        retrieved = store.similarity_search("refund policy", k=2)
        assert {doc.id for doc in retrieved} == {"chunk-a", unrelated_id}

        # Customer onboarding preflight reads the same real PGVector rows.
        checked = inspect_pgvector_causal_target(
            downstream,
            organization_id=organization.id,
            source_external_id="source-a",
            index_external_id="index-a",
            collection_name=collection_name,
            chunk_id="chunk-a",
            chunk_revision_external_id=authorized_revision,
        )
        assert checked.ready and checked.code == "chunk_ready"
        wrong_index = inspect_pgvector_causal_target(
            downstream,
            organization_id=organization.id,
            source_external_id="source-a",
            index_external_id="wrong-index",
            collection_name=collection_name,
        )
        assert not wrong_index.ready
        assert wrong_index.code == "collection_identity_mismatch"

        # The index materialization is already staged by the public helper.
        assert len(indexed.materialization.observations) == 2
        root = LineageRevisionIdentity(
            source_external_id="source-a",
            asset_external_id="chunk-a",
            revision_external_id=authorized_revision,
        )
        target = LineageRevisionIdentity(
            source_external_id="source-a",
            asset_external_id="index-a",
            revision_external_id="index-a-r7",
        )
        decision = persist_lineage_invalidation_decision(
            authority,
            organization_id=organization.id,
            decision=LineageInvalidationDecision(
                root=root,
                target=target,
                policy=_policy(7),
                disposition="invalidate",
                reason_code="causal_dependency_changed",
                causal_path=(root, target),
            ),
        )
        activate_lineage_invalidation_policy(
            authority,
            organization_id=organization.id,
            policy=_policy(7),
        )
        register_langchain_pgvector_causal_target(
            authority_db=authority,
            organization_id=organization.id,
            source_external_id="source-a",
            index_external_id="index-a",
            binding_revision=1,
            connection_ref="test-downstream",
            collection_name=collection_name,
        )
        intent = authorize_lineage_invalidation_execution(
            authority,
            organization_id=organization.id,
            decision_record_id=decision.record.id,
        ).record
        authority.commit()

        outcome = run_lineage_invalidation_execution_cycle(
            organization_id=organization.id,
        )
        assert outcome.status == LineageInvalidationExecutionCycleStatus.SUCCEEDED
        assert outcome.candidate is not None
        assert outcome.candidate.intent_id == intent.id
        downstream.rollback()
        assert not _document_exists(
            downstream, collection_name=collection_name, document_id="chunk-a"
        )
        assert _document_exists(
            downstream, collection_name=collection_name, document_id=unrelated_id
        )
    finally:
        authority.close()
        downstream.rollback()
        downstream.close()
        downstream_engine.dispose()
        if store is not None:
            store.delete_collection()
        _cleanup(organization_ids)
