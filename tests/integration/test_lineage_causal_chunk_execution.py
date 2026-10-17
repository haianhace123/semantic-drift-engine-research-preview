from __future__ import annotations

import json
import os
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text

from src.core.database import SessionLocal
from src.domain.lineage_execution_target import (
    LineageExecutionTargetBinding,
    LineageExecutionTargetIdentity,
)
from src.domain.lineage_observation import (
    LineageObservation,
    LineageRevisionReference,
)
from src.models.lineage_invalidation_execution_attempt import (
    LineageInvalidationExecutionAttemptRecord,
)
from src.services.lineage_downstream_connection_service import (
    create_lineage_downstream_session,
    resolve_lineage_downstream_connection,
)
from src.services.lineage_execution_target_binding_service import (
    persist_lineage_execution_target_binding,
)
from src.services.lineage_invalidation_execution_claim_service import (
    LineageInvalidationExecutionUnsafeTargetError,
    claim_lineage_invalidation_execution,
)
from src.services.lineage_invalidation_executor_service import (
    LineageInvalidationExecutorUnsupportedTargetError,
    resolve_lineage_invalidation_execution_plan,
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
from src.services.lineage_observation_persistence_service import (
    persist_lineage_observation,
)

from tests.integration.test_lineage_invalidation_execution_lifecycle import (
    DOWNSTREAM_DATABASE_URL,
    _cleanup,
    _cleanup_downstream_collections,
    _document_exists,
    _ensure_downstream_schema,
    _decision,
    _policy,
    _insert_collection,
    _insert_document,
    _make_organization,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


def _establish_causal_intent(
    db, *, organization_id, collection_name: str, include_chunk: bool = True,
    include_dependency: bool = True,
):
    decision = persist_lineage_invalidation_decision(
        db, organization_id=organization_id, decision=_decision()
    )
    activate_lineage_invalidation_policy(
        db, organization_id=organization_id, policy=_policy(7)
    )
    persist_lineage_observation(
        db,
        organization_id=organization_id,
        observation=LineageObservation(
            observation_kind="materialization",
            source_external_id="source-a",
            snapshot_external_id="snapshot-7",
            asset_external_id="index-a",
            asset_kind="index",
            revision_external_id="index-a-r7",
            sequence=7,
            dependencies=(
                (
                    LineageRevisionReference(
                        asset_external_id="chunk-a",
                        revision_external_id="chunk-a-r3",
                        dependency_type="indexed_from",
                    ),
                )
                if include_dependency else ()
            ),
        ),
    )
    if include_chunk:
        persist_lineage_observation(
            db,
            organization_id=organization_id,
            observation=LineageObservation(
                observation_kind="materialization",
                source_external_id="source-a",
                snapshot_external_id="chunk-snapshot-3",
                asset_external_id="chunk-a",
                asset_kind="chunk",
                revision_external_id="chunk-a-r3",
                sequence=3,
            ),
        )
    persist_lineage_execution_target_binding(
        db,
        organization_id=organization_id,
        binding=LineageExecutionTargetBinding(
            identity=LineageExecutionTargetIdentity("source-a", "index-a"),
            binding_revision=1,
            provider="pgvector",
            target_kind="langchain_pgvector_causal_chunk",
            configuration={
                "connection_ref": "test-downstream",
                "collection_name": collection_name,
            },
        ),
    )
    return authorize_lineage_invalidation_execution(
        db, organization_id=organization_id, decision_record_id=decision.record.id
    ).record


@pytest.mark.parametrize(
    ("persisted_source", "expected_status"),
    [
        ("source-a", LineageInvalidationExecutionCycleStatus.SUCCEEDED),
        ("other-source", LineageInvalidationExecutionCycleStatus.FAILED),
        (None, LineageInvalidationExecutionCycleStatus.FAILED),
    ],
)
def test_causal_chunk_preserves_unrelated_same_revision_document(
    monkeypatch,
    persisted_source,
    expected_status,
) -> None:
    authority = SessionLocal()
    downstream = None
    engine = None
    verification = None
    verify_engine = None
    organizations = []
    collections = []
    try:
        monkeypatch.setattr(
            "src.services.lineage_downstream_connection_service."
            "settings.DOWNSTREAM_DATABASE_URLS",
            json.dumps({"test-downstream": DOWNSTREAM_DATABASE_URL}),
        )
        connection = resolve_lineage_downstream_connection(
            connection_ref="test-downstream"
        )
        downstream, engine = create_lineage_downstream_session(connection)
        verification, verify_engine = create_lineage_downstream_session(connection)
        _ensure_downstream_schema(downstream)

        organization = _make_organization(authority)
        organizations.append(organization.id)
        collection_id = uuid.uuid4()
        collections.append(collection_id)
        collection_name = f"sde-causal-chunk-{uuid.uuid4().hex}"
        unrelated_document_id = f"unrelated-{uuid.uuid4().hex}"
        _insert_collection(
            downstream, collection_id=collection_id, collection_name=collection_name,
            organization_id=organization.id,
        )
        _insert_document(
            downstream,
            collection_id=collection_id,
            document_id="chunk-a",
            document_revision_external_id="chunk-a-r3",
            source_external_id=persisted_source,
        )
        _insert_document(
            downstream,
            collection_id=collection_id,
            document_id=unrelated_document_id,
            document_revision_external_id="chunk-a-r3",
        )
        downstream.commit()

        intent = _establish_causal_intent(
            authority,
            organization_id=organization.id,
            collection_name=collection_name,
        )
        authority.commit()

        result = run_lineage_invalidation_execution_cycle(
            organization_id=organization.id
        )
        assert result.status == expected_status
        assert result.candidate is not None
        assert result.candidate.intent_id == intent.id
        verification.rollback()
        assert _document_exists(
            verification,
            collection_name=collection_name,
            document_id=unrelated_document_id,
        )
        assert _document_exists(
            verification,
            collection_name=collection_name,
            document_id="chunk-a",
        ) == (expected_status == LineageInvalidationExecutionCycleStatus.FAILED)
    finally:
        authority.close()
        if verification is not None:
            verification.rollback()
            verification.close()
        if verify_engine is not None:
            verify_engine.dispose()
        if downstream is not None:
            downstream.rollback()
            downstream.close()
        if engine is not None:
            engine.dispose()
        _cleanup_downstream_collections(collections)
        _cleanup(organizations)


@pytest.mark.parametrize(
    "misbinding",
    ["other-index", "other-tenant", "missing-metadata", "missing-document"],
)
def test_causal_chunk_rejects_misbound_pgvector_collection(
    monkeypatch: pytest.MonkeyPatch,
    misbinding: str,
) -> None:
    """An index-A decision must not delete an identical chunk in index B.

    Source, chunk id and revision can legitimately be shared by different
    index materializations. The target index must be physically authenticated.
    """
    authority = SessionLocal()
    downstream = None
    engine = None
    verifier = None
    verifier_engine = None
    organizations = []
    collections = []
    try:
        monkeypatch.setattr(
            "src.services.lineage_downstream_connection_service."
            "settings.DOWNSTREAM_DATABASE_URLS",
            json.dumps({"test-downstream": DOWNSTREAM_DATABASE_URL}),
        )
        connection = resolve_lineage_downstream_connection(
            connection_ref="test-downstream"
        )
        downstream, engine = create_lineage_downstream_session(connection)
        verifier, verifier_engine = create_lineage_downstream_session(connection)
        _ensure_downstream_schema(downstream)

        organization = _make_organization(authority)
        organizations.append(organization.id)
        index_a_collection = uuid.uuid4()
        index_b_collection = uuid.uuid4()
        collections.extend([index_a_collection, index_b_collection])
        index_a_name = f"sde-index-a-{uuid.uuid4().hex}"
        index_b_name = f"sde-index-b-{uuid.uuid4().hex}"
        _insert_collection(
            downstream,
            collection_id=index_a_collection,
            collection_name=index_a_name,
            organization_id=organization.id,
            index_external_id="index-a",
        )
        _insert_collection(
            downstream,
            collection_id=index_b_collection,
            collection_name=index_b_name,
            organization_id=(
                uuid.uuid4() if misbinding == "other-tenant" else organization.id
            ),
            index_external_id=(
                "index-a" if misbinding in {"other-tenant", "missing-metadata"}
                else "index-b"
            ),
        )
        if misbinding == "missing-metadata":
            downstream.execute(
                text("UPDATE langchain_pg_collection "
                     "SET cmetadata = '{}'::json WHERE uuid = :collection_id"),
                {"collection_id": index_b_collection},
            )
        # A PGVector row in B has the exact chunk provenance that index A
        # happens to reference, but B is NOT the authorized index target.
        if misbinding != "missing-document":
            _insert_document(
                downstream,
                collection_id=index_b_collection,
                document_id="chunk-a",
                document_revision_external_id="chunk-a-r3",
                source_external_id="source-a",
            )
        downstream.commit()

        _establish_causal_intent(
            authority,
            organization_id=organization.id,
            collection_name=index_b_name,  # incorrect target binding
        )
        authority.commit()

        result = run_lineage_invalidation_execution_cycle(
            organization_id=organization.id
        )

        assert result.status == LineageInvalidationExecutionCycleStatus.FAILED
        assert result.reason_code == "downstream_execution_failed"
        verifier.rollback()
        assert _document_exists(
            verifier,
            collection_name=index_b_name,
            document_id="chunk-a",
        ) == (misbinding != "missing-document")
    finally:
        authority.close()
        if verifier is not None:
            verifier.rollback()
            verifier.close()
        if verifier_engine is not None:
            verifier_engine.dispose()
        if downstream is not None:
            downstream.rollback()
            downstream.close()
        if engine is not None:
            engine.dispose()
        _cleanup_downstream_collections(collections)
        _cleanup(organizations)


@pytest.mark.parametrize(
    ("include_chunk", "include_dependency"),
    [(False, True), (True, False)],
)
def test_causal_chunk_requires_exact_lineage_evidence_before_claim(
    include_chunk, include_dependency
) -> None:
    db = SessionLocal()
    organizations = []
    try:
        organization = _make_organization(db)
        organizations.append(organization.id)
        _establish_causal_intent(
            db,
            organization_id=organization.id,
            collection_name=f"no-evidence-{uuid.uuid4().hex}",
            include_chunk=include_chunk,
            include_dependency=include_dependency,
        )
        db.commit()
        result = run_lineage_invalidation_execution_cycle(
            organization_id=organization.id
        )
        assert result.status == LineageInvalidationExecutionCycleStatus.SKIPPED
        db.rollback()
        count = (
            db.query(LineageInvalidationExecutionAttemptRecord)
            .filter(
                LineageInvalidationExecutionAttemptRecord.organization_id
                == organization.id
            )
            .count()
        )
        assert count == 0
    finally:
        db.close()
        _cleanup(organizations)


def test_causal_chunk_rejects_consumption_only_index_edge() -> None:
    """An index consumption cannot substitute for physical materialization."""
    db = SessionLocal()
    organizations = []
    try:
        organization = _make_organization(db)
        organizations.append(organization.id)
        intent = _establish_causal_intent(
            db,
            organization_id=organization.id,
            collection_name=f"no-index-edge-{uuid.uuid4().hex}",
            include_dependency=False,
        )
        # The index materialization has no chunk dependency. An otherwise
        # matching consumption observation must not authorize a DELETE.
        persist_lineage_observation(
            db,
            organization_id=organization.id,
            observation=LineageObservation(
                observation_kind="consumption",
                source_external_id="source-a",
                snapshot_external_id="index-consumption-8",
                asset_external_id="index-a",
                asset_kind="index",
                revision_external_id="index-a-r7",
                sequence=8,
                dependencies=(
                    LineageRevisionReference(
                        asset_external_id="chunk-a",
                        revision_external_id="chunk-a-r3",
                        dependency_type="indexed_from",
                    ),
                ),
            ),
        )
        db.commit()

        with pytest.raises(LineageInvalidationExecutionUnsafeTargetError):
            claim_lineage_invalidation_execution(
                db,
                organization_id=organization.id,
                intent_id=intent.id,
            )
        db.rollback()
        assert (
            db.query(LineageInvalidationExecutionAttemptRecord)
            .filter(
                LineageInvalidationExecutionAttemptRecord.organization_id
                == organization.id,
                LineageInvalidationExecutionAttemptRecord.intent_id == intent.id,
            )
            .count()
        ) == 0
    finally:
        db.close()
        _cleanup(organizations)


def _publish_legacy_binding(db, organization_id, *, revision: int) -> None:
    persist_lineage_execution_target_binding(
        db,
        organization_id=organization_id,
        binding=LineageExecutionTargetBinding(
            identity=LineageExecutionTargetIdentity("source-a", "index-a"),
            binding_revision=revision,
            provider="pgvector",
            target_kind="langchain_pgvector_document_revision",
            configuration={
                "connection_ref": "test-downstream",
                "collection_name": "rag-index",
                "document_id": "chunk-a",
                # This revision IS on the authorized path. Revision membership
                # is insufficient to establish physical row identity.
                "document_revision_external_id": "chunk-a-r3",
            },
        ),
    )


def _publish_causal_binding(db, organization_id, *, revision: int) -> None:
    persist_lineage_execution_target_binding(
        db,
        organization_id=organization_id,
        binding=LineageExecutionTargetBinding(
            identity=LineageExecutionTargetIdentity("source-a", "index-a"),
            binding_revision=revision,
            provider="pgvector",
            target_kind="langchain_pgvector_causal_chunk",
            configuration={
                "connection_ref": "test-downstream",
                "collection_name": "rag-index",
            },
        ),
    )


def test_legacy_binding_with_revision_on_causal_path_cannot_claim() -> None:
    db = SessionLocal()
    organizations = []
    try:
        organization = _make_organization(db)
        organizations.append(organization.id)
        intent = _establish_causal_intent(
            db,
            organization_id=organization.id,
            collection_name="rag-index",
        )
        _publish_legacy_binding(db, organization.id, revision=2)
        db.commit()

        with pytest.raises(LineageInvalidationExecutionUnsafeTargetError):
            claim_lineage_invalidation_execution(
                db,
                organization_id=organization.id,
                intent_id=intent.id,
            )
        db.rollback()

        attempts = (
            db.query(LineageInvalidationExecutionAttemptRecord)
            .filter(
                LineageInvalidationExecutionAttemptRecord.organization_id
                == organization.id,
                LineageInvalidationExecutionAttemptRecord.intent_id
                == intent.id,
            )
            .count()
        )
        assert attempts == 0
    finally:
        db.close()
        _cleanup(organizations)


def test_historical_legacy_attempt_remains_blocked_after_safe_upgrade() -> None:
    db = SessionLocal()
    organizations = []
    try:
        organization = _make_organization(db)
        organizations.append(organization.id)
        intent = _establish_causal_intent(
            db,
            organization_id=organization.id,
            collection_name="rag-index",
        )
        _publish_legacy_binding(db, organization.id, revision=2)
        db_now = db.execute(select(func.clock_timestamp())).scalar_one()
        old_attempt = LineageInvalidationExecutionAttemptRecord(
            organization_id=organization.id,
            intent_id=intent.id,
            attempt_number=1,
            binding_revision=2,
            claimed_at=db_now - timedelta(seconds=600),
            lease_expires_at=db_now - timedelta(seconds=300),
        )
        db.add(old_attempt)
        _publish_causal_binding(db, organization.id, revision=3)
        db.commit()

        with pytest.raises(LineageInvalidationExecutorUnsupportedTargetError):
            resolve_lineage_invalidation_execution_plan(
                db,
                organization_id=organization.id,
                attempt_id=old_attempt.id,
            )
        db.rollback()

        claim = claim_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
        )
        assert claim.record.attempt_number == 2
        assert claim.record.binding_revision == 3
        plan = resolve_lineage_invalidation_execution_plan(
            db,
            organization_id=organization.id,
            attempt_id=claim.record.id,
        )
        assert plan.target_kind == "langchain_pgvector_causal_chunk"
        assert plan.target_configuration["document_id"] == "chunk-a"
    finally:
        db.close()
        _cleanup(organizations)
