from __future__ import annotations

from datetime import timedelta
import json
import os
import threading
import time
import uuid

import pytest
from sqlalchemy import delete, func, text

from src.core.database import SessionLocal
from src.domain.lineage_pgvector_collection import sde_pgvector_collection_metadata
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
from src.models.lineage_invalidation_policy_activation import (
    LineageInvalidationPolicyActivationRecord,
)
from src.models.lineage_observation import LineageObservationRecord
from src.models.organization import Organization
from src.services.lineage_downstream_connection_service import (
    LineageDownstreamConnectionNotFoundError,
    create_lineage_downstream_session,
    resolve_lineage_downstream_connection,
)
from src.services.lineage_execution_target_binding_service import (
    persist_lineage_execution_target_binding,
)
from src.services.lineage_impact_service import analyze_lineage_impact
from src.services.lineage_invalidation_execution_claim_service import (
    LineageInvalidationExecutionClaimTerminalError,
    claim_lineage_invalidation_execution,
)
from src.services.lineage_invalidation_execution_cycle_service import (
    LineageInvalidationExecutionCycleStatus,
    run_lineage_invalidation_execution_cycle,
)
from src.services.lineage_invalidation_execution_worker import (
    LineageInvalidationExecutionWorker,
)
from src.services.lineage_invalidation_execution_intent_service import (
    authorize_lineage_invalidation_execution,
)
from src.services.lineage_invalidation_execution_result_service import (
    persist_lineage_invalidation_execution_result,
)
from src.services.lineage_invalidation_executor_service import (
    resolve_lineage_invalidation_execution_plan,
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
from src.services.lineage_pgvector_invalidation_service import (
    invalidate_lineage_pgvector_causal_chunk,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)

DOWNSTREAM_DATABASE_URL = (
    "postgresql://downstream_test:downstream_test@"
    "integration_downstream_db:5432/downstream_test"
)

def _make_organization(db) -> Organization:
    suffix = uuid.uuid4().hex[:12]
    organization = Organization(
        id=uuid.uuid4(),
        name=f"lineage-execution-lifecycle-{suffix}",
        slug=f"lineage-execution-lifecycle-{suffix}",
        billing_email=f"{suffix}@example.com",
    )
    db.add(organization)
    db.flush()
    return organization


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


def _policy(revision: int) -> LineageInvalidationPolicyIdentity:
    return LineageInvalidationPolicyIdentity(
        policy_external_id="rag-invalidation",
        revision=revision,
    )


def _decision() -> LineageInvalidationDecision:
    root = _revision(
        "chunk-a",
        "chunk-a-r3",
    )
    target = _revision(
        "index-a",
        "index-a-r7",
    )

    return LineageInvalidationDecision(
        root=root,
        target=target,
        policy=_policy(7),
        disposition="invalidate",
        reason_code="causal_dependency_changed",
        causal_path=(
            root,
            target,
        ),
    )


def _materialization() -> LineageObservation:
    return LineageObservation(
        observation_kind="materialization",
        source_external_id="source-a",
        snapshot_external_id="snapshot-7",
        asset_external_id="index-a",
        asset_kind="index",
        revision_external_id="index-a-r7",
        sequence=7,
        dependencies=(
            LineageRevisionReference(
                asset_external_id="chunk-a",
                revision_external_id="chunk-a-r3",
                dependency_type="indexed_from",
            ),
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


def _binding(
    *,
    collection_name: str,
    document_id: str,
    document_revision_external_id: str | None = "chunk-a-r3",
    legacy_target: bool = False,
) -> LineageExecutionTargetBinding:
    if legacy_target or document_revision_external_id is None:
        configuration = {
            "connection_ref": "test-downstream",
            "collection_name": collection_name,
            "document_id": document_id,
        }
        if document_revision_external_id is not None:
            configuration["document_revision_external_id"] = (
                document_revision_external_id
            )
    else:
        configuration = {
            "connection_ref": "test-downstream",
            "collection_name": collection_name,
        }

    return LineageExecutionTargetBinding(
        identity=LineageExecutionTargetIdentity(
            source_external_id="source-a",
            asset_external_id="index-a",
        ),
        binding_revision=1,
        provider="pgvector",
        target_kind=(
            ("langchain_pgvector_document_revision"
             if document_revision_external_id is not None
             else "langchain_pgvector_document")
            if legacy_target or document_revision_external_id is None
            else "langchain_pgvector_causal_chunk"
        ),
        configuration=configuration,
    )


def _establish_claimable_intent(
    db,
    *,
    organization_id: uuid.UUID,
    collection_name: str,
    document_id: str,
    document_revision_external_id: str | None = "chunk-a-r3",
    legacy_target: bool = False,
) -> LineageInvalidationExecutionIntentRecord:
    persisted = persist_lineage_invalidation_decision(
        db,
        organization_id=organization_id,
        decision=_decision(),
    )

    activate_lineage_invalidation_policy(
        db,
        organization_id=organization_id,
        policy=_policy(7),
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
        binding=_binding(
            collection_name=collection_name,
            document_id=document_id,
            document_revision_external_id=(
                document_revision_external_id
            ),
            legacy_target=legacy_target,
        ),
    )

    return authorize_lineage_invalidation_execution(
        db,
        organization_id=organization_id,
        decision_record_id=persisted.record.id,
    ).record


def _ensure_downstream_schema(db) -> None:
    db.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS langchain_pg_collection (
                uuid UUID PRIMARY KEY,
                name VARCHAR NOT NULL UNIQUE,
                cmetadata JSON
            )
            """
        )
    )
    db.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS langchain_pg_embedding (
                id VARCHAR PRIMARY KEY,
                collection_id UUID NOT NULL
                    REFERENCES langchain_pg_collection(uuid)
                    ON DELETE CASCADE,
                embedding VECTOR,
                document VARCHAR,
                cmetadata JSONB
            )
            """
        )
    )
    db.commit()


def _insert_collection(
    db,
    *,
    collection_id: uuid.UUID,
    collection_name: str,
    organization_id: uuid.UUID,
    source_external_id: str = "source-a",
    index_external_id: str = "index-a",
) -> None:
    db.execute(
        text(
            """
            INSERT INTO langchain_pg_collection (
                uuid,
                name,
                cmetadata
            )
            VALUES (
                :collection_id,
                :collection_name,
                CAST(:collection_metadata AS json)
            )
            """
        ),
        {
            "collection_id": collection_id,
            "collection_name": collection_name,
            "collection_metadata": json.dumps(
                sde_pgvector_collection_metadata(
                    organization_id=organization_id,
                    source_external_id=source_external_id,
                    index_external_id=index_external_id,
                )
            ),
        },
    )


def _insert_document(
    db,
    *,
    collection_id: uuid.UUID,
    document_id: str,
    document_revision_external_id: str | None = "chunk-a-r3",
    source_external_id: str | None = "source-a",
) -> None:
    db.execute(
        text(
            """
            INSERT INTO langchain_pg_embedding (
                id,
                collection_id,
                embedding,
                document,
                cmetadata
            )
            VALUES (
                :document_id,
                :collection_id,
                '[0,0,0,0,0,0,0,0]'::vector,
                'lifecycle-test',
                CAST(:cmetadata AS jsonb)
            )
            """
        ),
        {
            "document_id": document_id,
            "collection_id": collection_id,
            "cmetadata": json.dumps(
                {}
                if document_revision_external_id is None
                else {
                    "chunk_revision_external_id": (
                        document_revision_external_id
                    ),
                    **(
                        {"source_external_id": source_external_id}
                        if source_external_id is not None
                        else {}
                    ),
                }
            ),
        },
    )


def _document_exists(
    db,
    *,
    collection_name: str,
    document_id: str,
) -> bool:
    return (
        db.execute(
            text(
                """
                SELECT count(*)
                FROM langchain_pg_embedding AS embedding
                JOIN langchain_pg_collection AS collection
                  ON collection.uuid = embedding.collection_id
                WHERE collection.name = :collection_name
                  AND embedding.id = :document_id
                """
            ),
            {
                "collection_name": collection_name,
                "document_id": document_id,
            },
        ).scalar_one()
        == 1
    )


def _result_count(
    db,
    *,
    organization_id: uuid.UUID,
    attempt_id: uuid.UUID,
) -> int:
    return (
        db.query(func.count(LineageInvalidationExecutionResultRecord.id))
        .filter(
            LineageInvalidationExecutionResultRecord.organization_id
            == organization_id,
            LineageInvalidationExecutionResultRecord.attempt_id == attempt_id,
        )
        .scalar()
    )


def _wait_for_terminal_result(
    db,
    *,
    organization_id: uuid.UUID,
    intent_id: uuid.UUID,
    timeout_seconds: float,
) -> LineageInvalidationExecutionResultRecord:
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        db.rollback()

        result = (
            db.query(LineageInvalidationExecutionResultRecord)
            .join(
                LineageInvalidationExecutionAttemptRecord,
                LineageInvalidationExecutionAttemptRecord.id
                == LineageInvalidationExecutionResultRecord.attempt_id,
            )
            .filter(
                LineageInvalidationExecutionResultRecord.organization_id
                == organization_id,
                LineageInvalidationExecutionAttemptRecord.intent_id
                == intent_id,
            )
            .one_or_none()
        )

        if result is not None:
            return result

        time.sleep(0.05)

    raise AssertionError(
        "Timed out waiting for lineage invalidation execution result"
    )


def _expire_attempt(
    db,
    *,
    attempt_id: uuid.UUID,
) -> None:
    db.execute(
        LineageInvalidationExecutionAttemptRecord.__table__.update()
        .where(
            LineageInvalidationExecutionAttemptRecord.id == attempt_id,
        )
        .values(
            claimed_at=func.clock_timestamp() - timedelta(seconds=600),
            lease_expires_at=func.clock_timestamp() - timedelta(seconds=300),
        )
    )
    db.commit()


def _cleanup(
    organization_ids: list[uuid.UUID],
) -> None:
    db = SessionLocal()

    try:
        db.rollback()

        if organization_ids:
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
                delete(Organization).where(
                    Organization.id.in_(organization_ids)
                )
            )

        db.commit()
    finally:
        db.close()


def _cleanup_collections(
    db,
    collection_ids: list[uuid.UUID],
) -> None:
    try:
        for collection_id in collection_ids:
            db.execute(
                text(
                    """
                    DELETE FROM langchain_pg_collection
                    WHERE uuid = :collection_id
                    """
                ),
                {"collection_id": collection_id},
            )

        db.commit()
    finally:
        db.close()


def _cleanup_downstream_collections(
    collection_ids: list[uuid.UUID],
) -> None:
    if not collection_ids:
        return

    downstream_connection = resolve_lineage_downstream_connection(
        connection_ref="test-downstream",
        registry_json=(
            '{"test-downstream":'
            f'"{DOWNSTREAM_DATABASE_URL}"'
            "}"
        ),
    )
    db, engine = create_lineage_downstream_session(
        downstream_connection
    )

    try:
        _cleanup_collections(db, collection_ids)
    finally:
        engine.dispose()


def test_execution_lifecycle_commits_side_effect_then_terminal_success() -> None:
    authority_db = SessionLocal()
    authority_verify_db = SessionLocal()
    execution_db = None
    execution_engine = None
    downstream_verify_db = None
    downstream_verify_engine = None
    organization_ids: list[uuid.UUID] = []
    collection_ids: list[uuid.UUID] = []

    try:
        downstream_connection = resolve_lineage_downstream_connection(
            connection_ref="test-downstream",
            registry_json=(
                '{"test-downstream":'
                f'"{DOWNSTREAM_DATABASE_URL}"'
                "}"
            ),
        )
        execution_db, execution_engine = create_lineage_downstream_session(
            downstream_connection
        )
        downstream_verify_db, downstream_verify_engine = (
            create_lineage_downstream_session(downstream_connection)
        )
        _ensure_downstream_schema(execution_db)

        organization = _make_organization(authority_db)
        organization_ids.append(organization.id)

        collection_id = uuid.uuid4()
        collection_ids.append(collection_id)
        collection_name = f"sde-lifecycle-{uuid.uuid4().hex}"
        document_id = "chunk-a"

        _insert_collection(
            execution_db,
            collection_id=collection_id,
            collection_name=collection_name,
            organization_id=organization.id,
        )
        _insert_document(
            execution_db,
            collection_id=collection_id,
            document_id=document_id,
        )

        execution_db.commit()

        intent = _establish_claimable_intent(
            authority_db,
            organization_id=organization.id,
            collection_name=collection_name,
            document_id=document_id,
        )
        authority_db.commit()

        claim = claim_lineage_invalidation_execution(
            authority_db,
            organization_id=organization.id,
            intent_id=intent.id,
            lease_seconds=300,
        )
        attempt_id = claim.record.id
        authority_db.commit()

        plan = resolve_lineage_invalidation_execution_plan(
            authority_db,
            organization_id=organization.id,
            attempt_id=attempt_id,
        )
        authority_db.rollback()

        execution = invalidate_lineage_pgvector_causal_chunk(
            execution_db,
            configuration=plan.target_configuration,
        )

        assert execution.deleted is True

        execution_db.commit()

        assert not _document_exists(
            downstream_verify_db,
            collection_name=collection_name,
            document_id=document_id,
        )

        published = persist_lineage_invalidation_execution_result(
            authority_db,
            organization_id=organization.id,
            attempt_id=attempt_id,
            outcome="succeeded",
        )

        assert published.created is True
        authority_db.commit()

        authority_verify_db.rollback()

        assert _result_count(
            authority_verify_db,
            organization_id=organization.id,
            attempt_id=attempt_id,
        ) == 1

        with pytest.raises(LineageInvalidationExecutionClaimTerminalError):
            claim_lineage_invalidation_execution(
                authority_db,
                organization_id=organization.id,
                intent_id=intent.id,
                lease_seconds=300,
            )
        authority_db.rollback()
    finally:
        authority_verify_db.close()

        if downstream_verify_db is not None:
            downstream_verify_db.rollback()
            downstream_verify_db.close()

        if downstream_verify_engine is not None:
            downstream_verify_engine.dispose()

        if execution_db is not None:
            execution_db.rollback()
            execution_db.close()

        if execution_engine is not None:
            execution_engine.dispose()

        authority_db.close()
        _cleanup_downstream_collections(collection_ids)
        _cleanup(organization_ids)


def test_execution_lifecycle_recovers_after_crash_between_delete_and_result() -> None:
    authority_db = SessionLocal()
    authority_verify_db = SessionLocal()
    execution_db = None
    execution_engine = None
    downstream_verify_db = None
    downstream_verify_engine = None
    organization_ids: list[uuid.UUID] = []
    collection_ids: list[uuid.UUID] = []

    try:
        downstream_connection = resolve_lineage_downstream_connection(
            connection_ref="test-downstream",
            registry_json=(
                '{"test-downstream":'
                f'"{DOWNSTREAM_DATABASE_URL}"'
                "}"
            ),
        )
        execution_db, execution_engine = create_lineage_downstream_session(
            downstream_connection
        )
        downstream_verify_db, downstream_verify_engine = (
            create_lineage_downstream_session(downstream_connection)
        )
        _ensure_downstream_schema(execution_db)

        organization = _make_organization(authority_db)
        organization_ids.append(organization.id)

        collection_id = uuid.uuid4()
        collection_ids.append(collection_id)
        collection_name = f"sde-lifecycle-crash-{uuid.uuid4().hex}"
        document_id = "chunk-a"

        _insert_collection(
            execution_db,
            collection_id=collection_id,
            collection_name=collection_name,
            organization_id=organization.id,
        )
        _insert_document(
            execution_db,
            collection_id=collection_id,
            document_id=document_id,
        )

        execution_db.commit()

        intent = _establish_claimable_intent(
            authority_db,
            organization_id=organization.id,
            collection_name=collection_name,
            document_id=document_id,
        )
        authority_db.commit()

        first_claim = claim_lineage_invalidation_execution(
            authority_db,
            organization_id=organization.id,
            intent_id=intent.id,
            lease_seconds=300,
        )
        first_attempt_id = first_claim.record.id
        authority_db.commit()

        first_plan = resolve_lineage_invalidation_execution_plan(
            authority_db,
            organization_id=organization.id,
            attempt_id=first_attempt_id,
        )
        authority_db.rollback()

        first_execution = invalidate_lineage_pgvector_causal_chunk(
            execution_db,
            configuration=first_plan.target_configuration,
        )

        assert first_execution.deleted is True

        execution_db.commit()

        assert not _document_exists(
            downstream_verify_db,
            collection_name=collection_name,
            document_id=document_id,
        )
        authority_verify_db.rollback()

        assert _result_count(
            authority_verify_db,
            organization_id=organization.id,
            attempt_id=first_attempt_id,
        ) == 0

        _expire_attempt(
            authority_db,
            attempt_id=first_attempt_id,
        )

        second_claim = claim_lineage_invalidation_execution(
            authority_db,
            organization_id=organization.id,
            intent_id=intent.id,
            lease_seconds=300,
        )
        second_attempt_id = second_claim.record.id

        assert second_claim.record.attempt_number == 2
        assert second_claim.record.binding_revision == first_claim.record.binding_revision

        authority_db.commit()

        second_plan = resolve_lineage_invalidation_execution_plan(
            authority_db,
            organization_id=organization.id,
            attempt_id=second_attempt_id,
        )
        authority_db.rollback()

        assert second_plan.binding_revision == first_plan.binding_revision
        assert second_plan.connection_ref == first_plan.connection_ref
        assert dict(second_plan.target_configuration) == dict(
            first_plan.target_configuration
        )

        second_execution = invalidate_lineage_pgvector_causal_chunk(
            execution_db,
            configuration=second_plan.target_configuration,
        )

        assert second_execution.deleted is False

        execution_db.commit()

        published = persist_lineage_invalidation_execution_result(
            authority_db,
            organization_id=organization.id,
            attempt_id=second_attempt_id,
            outcome="succeeded",
        )

        assert published.created is True
        authority_db.commit()

        authority_verify_db.rollback()

        assert _result_count(
            authority_verify_db,
            organization_id=organization.id,
            attempt_id=first_attempt_id,
        ) == 0
        assert _result_count(
            authority_verify_db,
            organization_id=organization.id,
            attempt_id=second_attempt_id,
        ) == 1

        with pytest.raises(LineageInvalidationExecutionClaimTerminalError):
            claim_lineage_invalidation_execution(
                authority_db,
                organization_id=organization.id,
                intent_id=intent.id,
                lease_seconds=300,
            )
        authority_db.rollback()
    finally:
        authority_verify_db.close()

        if downstream_verify_db is not None:
            downstream_verify_db.rollback()
            downstream_verify_db.close()

        if downstream_verify_engine is not None:
            downstream_verify_engine.dispose()

        if execution_db is not None:
            execution_db.rollback()
            execution_db.close()

        if execution_engine is not None:
            execution_engine.dispose()

        authority_db.close()
        _cleanup_downstream_collections(collection_ids)
        _cleanup(organization_ids)


def test_targeted_invalidation_only_deletes_causally_affected_target() -> None:
    authority_db = SessionLocal()
    authority_verify_db = SessionLocal()
    downstream_execution_db = None
    downstream_execution_engine = None
    downstream_verify_db = None
    downstream_verify_engine = None
    organization_ids: list[uuid.UUID] = []
    collection_ids: list[uuid.UUID] = []

    try:
        downstream_connection = resolve_lineage_downstream_connection(
            connection_ref="test-downstream",
            registry_json=(
                '{"test-downstream":'
                f'"{DOWNSTREAM_DATABASE_URL}"'
                "}"
            ),
        )
        downstream_execution_db, downstream_execution_engine = (
            create_lineage_downstream_session(downstream_connection)
        )
        downstream_verify_db, downstream_verify_engine = (
            create_lineage_downstream_session(downstream_connection)
        )
        _ensure_downstream_schema(downstream_execution_db)

        authority_database = authority_db.execute(
            text("SELECT current_database()")
        ).scalar_one()
        downstream_database = downstream_execution_db.execute(
            text("SELECT current_database()")
        ).scalar_one()

        assert authority_database != downstream_database
        assert downstream_database == "downstream_test"
        organization = _make_organization(authority_db)
        organization_ids.append(organization.id)

        policy_source = "policy-source"
        unrelated_source = "unrelated-source"

        # PGVector embedding.id is globally unique, not collection-scoped.
        # Give each test run its own chunk identities while keeping the
        # physical document IDs identical to the recorded lineage assets.
        refund_chunk_id = f"refund-rule-{uuid.uuid4().hex}"
        shipping_chunk_id = f"shipping-rule-{uuid.uuid4().hex}"
        escalation_chunk_id = f"escalation-rule-{uuid.uuid4().hex}"
        other_chunk_id = f"other-rule-{uuid.uuid4().hex}"

        root = _revision(
            refund_chunk_id,
            "refund-rule@v1",
            source_external_id=policy_source,
        )

        targets = (
            (
                policy_source,
                "target-refund",
                "target-refund@v1",
                refund_chunk_id,
                "refund-rule@v1",
            ),
            (
                policy_source,
                "target-shipping",
                "target-shipping@v1",
                shipping_chunk_id,
                "shipping-rule@v1",
            ),
            (
                policy_source,
                "target-escalation",
                "target-escalation@v1",
                escalation_chunk_id,
                "escalation-rule@v1",
            ),
            (
                unrelated_source,
                "target-other",
                "target-other@v1",
                other_chunk_id,
                "other-rule@v1",
            ),
        )

        physical_targets: dict[
            tuple[str, str],
            tuple[uuid.UUID, str, str],
        ] = {}

        for (
            source_external_id,
            target_asset_external_id,
            target_revision_external_id,
            dependency_asset_external_id,
            dependency_revision_external_id,
        ) in targets:
            collection_id = uuid.uuid4()
            collection_name = (
                f"sde-targeted-{target_asset_external_id}-"
                f"{uuid.uuid4().hex}"
            )
            document_id = dependency_asset_external_id

            _insert_collection(
                downstream_execution_db,
                collection_id=collection_id,
                collection_name=collection_name,
                organization_id=organization.id,
                source_external_id=source_external_id,
                index_external_id=target_asset_external_id,
            )
            _insert_document(
                downstream_execution_db,
                collection_id=collection_id,
                document_id=document_id,
                document_revision_external_id=dependency_revision_external_id,
                source_external_id=source_external_id,
            )

            persist_lineage_observation(
                authority_db,
                organization_id=organization.id,
                observation=LineageObservation(
                    observation_kind="materialization",
                    source_external_id=source_external_id,
                    snapshot_external_id=(
                        f"snapshot-{target_asset_external_id}"
                    ),
                    asset_external_id=target_asset_external_id,
                    asset_kind="index",
                    revision_external_id=target_revision_external_id,
                    sequence=1,
                    dependencies=(
                        LineageRevisionReference(
                            asset_external_id=(
                                dependency_asset_external_id
                            ),
                            revision_external_id=(
                                dependency_revision_external_id
                            ),
                            dependency_type="derived_from",
                        ),
                    ),
                ),
            )

            persist_lineage_observation(
                authority_db,
                organization_id=organization.id,
                observation=LineageObservation(
                    observation_kind="materialization",
                    source_external_id=source_external_id,
                    snapshot_external_id=f"chunk-{target_asset_external_id}",
                    asset_external_id=dependency_asset_external_id,
                    asset_kind="chunk",
                    revision_external_id=dependency_revision_external_id,
                    sequence=1,
                ),
            )

            persist_lineage_execution_target_binding(
                authority_db,
                organization_id=organization.id,
                binding=LineageExecutionTargetBinding(
                    identity=LineageExecutionTargetIdentity(
                        source_external_id=source_external_id,
                        asset_external_id=target_asset_external_id,
                    ),
                    binding_revision=1,
                    provider="pgvector",
                    target_kind="langchain_pgvector_causal_chunk",
                    configuration={
                        "connection_ref": "test-downstream",
                        "collection_name": collection_name,
                    },
                ),
            )

            physical_targets[
                (
                    source_external_id,
                    target_asset_external_id,
                )
            ] = (
                collection_id,
                collection_name,
                document_id,
            )
        downstream_execution_db.commit()
        authority_db.commit()

        impact = analyze_lineage_impact(
            authority_db,
            organization_id=organization.id,
            root=root,
        )

        expected_target = _revision(
            "target-refund",
            "target-refund@v1",
            source_external_id=policy_source,
        )

        assert impact.impacted_revisions == (
            expected_target,
        )
        assert len(impact.paths) == 1
        assert impact.paths[0].root == root
        assert impact.paths[0].target == expected_target
        assert impact.paths[0].revisions == (
            root,
            expected_target,
        )

        total_targets = len(targets)
        affected_targets = len(impact.impacted_revisions)
        naive_source_rebuild_targets = sum(
            1
            for source_external_id, *_ in targets
            if source_external_id == policy_source
        )
        avoided_rebuild_targets = (
            naive_source_rebuild_targets - affected_targets
        )
        avoided_rebuild_ratio = (
            avoided_rebuild_targets
            / naive_source_rebuild_targets
        )

        assert total_targets == 4
        assert affected_targets == 1
        assert naive_source_rebuild_targets == 3
        assert avoided_rebuild_targets == 2
        assert avoided_rebuild_ratio == pytest.approx(2 / 3)

        decision = LineageInvalidationDecision(
            root=impact.paths[0].root,
            target=impact.paths[0].target,
            policy=_policy(7),
            disposition="invalidate",
            reason_code="causal_dependency_changed",
            causal_path=impact.paths[0].revisions,
        )

        persisted_decision = persist_lineage_invalidation_decision(
            authority_db,
            organization_id=organization.id,
            decision=decision,
        )

        activate_lineage_invalidation_policy(
            authority_db,
            organization_id=organization.id,
            policy=_policy(7),
        )

        intent = authorize_lineage_invalidation_execution(
            authority_db,
            organization_id=organization.id,
            decision_record_id=persisted_decision.record.id,
        ).record

        authority_db.commit()

        claim = claim_lineage_invalidation_execution(
            authority_db,
            organization_id=organization.id,
            intent_id=intent.id,
            lease_seconds=300,
        )
        attempt_id = claim.record.id
        authority_db.commit()

        plan = resolve_lineage_invalidation_execution_plan(
            authority_db,
            organization_id=organization.id,
            attempt_id=attempt_id,
        )
        authority_db.rollback()

        refund_physical_target = physical_targets[
            (
                policy_source,
                "target-refund",
            )
        ]

        assert plan.binding_revision == 1
        assert plan.connection_ref == "test-downstream"
        assert dict(plan.target_configuration) == {
            "collection_name": refund_physical_target[1],
            "document_id": refund_physical_target[2],
            "document_revision_external_id": "refund-rule@v1",
            "source_external_id": policy_source,
            "organization_id": str(organization.id),
            "index_external_id": "target-refund",
        }

        execution_connection = resolve_lineage_downstream_connection(
            connection_ref=plan.connection_ref,
            registry_json=(
                '{"test-downstream":'
                f'"{DOWNSTREAM_DATABASE_URL}"'
                "}"
            ),
        )

        assert execution_connection.connection_ref == plan.connection_ref

        downstream_execution_db.close()
        downstream_execution_engine.dispose()
        downstream_execution_db, downstream_execution_engine = (
            create_lineage_downstream_session(execution_connection)
        )

        execution = invalidate_lineage_pgvector_causal_chunk(
            downstream_execution_db,
            configuration=plan.target_configuration,
        )

        assert execution.deleted is True

        downstream_execution_db.commit()

        invalidated_targets = 0
        unaffected_targets_preserved = 0

        for (
            source_external_id,
            target_asset_external_id,
            _,
            _,
            _,
        ) in targets:
            _, collection_name, document_id = physical_targets[
                (
                    source_external_id,
                    target_asset_external_id,
                )
            ]

            downstream_verify_db.rollback()
            exists = _document_exists(
                downstream_verify_db,
                collection_name=collection_name,
                document_id=document_id,
            )

            if (
                source_external_id == policy_source
                and target_asset_external_id == "target-refund"
            ):
                assert exists is False
                invalidated_targets += 1
            else:
                assert exists is True
                unaffected_targets_preserved += 1

        assert invalidated_targets == 1
        assert unaffected_targets_preserved == 3

        published = persist_lineage_invalidation_execution_result(
            authority_db,
            organization_id=organization.id,
            attempt_id=attempt_id,
            outcome="succeeded",
        )

        assert published.created is True
        authority_db.commit()

        authority_verify_db.rollback()

        assert _result_count(
            authority_verify_db,
            organization_id=organization.id,
            attempt_id=attempt_id,
        ) == 1

        with pytest.raises(
            LineageInvalidationExecutionClaimTerminalError
        ):
            claim_lineage_invalidation_execution(
                authority_db,
                organization_id=organization.id,
                intent_id=intent.id,
                lease_seconds=300,
            )
        authority_db.rollback()
    finally:
        authority_verify_db.close()
        authority_db.close()

        if downstream_verify_db is not None:
            downstream_verify_db.rollback()
            downstream_verify_db.close()

        if downstream_verify_engine is not None:
            downstream_verify_engine.dispose()

        if downstream_execution_db is not None:
            downstream_execution_db.rollback()
            downstream_execution_db.close()

        if downstream_execution_engine is not None:
            downstream_execution_engine.dispose()

        _cleanup_downstream_collections(collection_ids)
        _cleanup(organization_ids)


def test_execution_worker_autonomously_executes_authorized_intent_end_to_end(
    monkeypatch,
) -> None:
    authority_db = SessionLocal()
    authority_verify_db = SessionLocal()
    downstream_setup_db = None
    downstream_setup_engine = None
    downstream_verify_db = None
    downstream_verify_engine = None
    worker = LineageInvalidationExecutionWorker(
        poll_seconds=0.05,
    )
    worker_thread = threading.Thread(
        target=worker.run,
        name="lineage-invalidation-execution-worker-test",
    )
    organization_ids: list[uuid.UUID] = []
    collection_ids: list[uuid.UUID] = []

    try:
        monkeypatch.setattr(
            "src.services.lineage_downstream_connection_service."
            "settings.DOWNSTREAM_DATABASE_URLS",
            (
                '{"test-downstream":"'
                + DOWNSTREAM_DATABASE_URL
                + '"}'
            ),
        )

        downstream_connection = resolve_lineage_downstream_connection(
            connection_ref="test-downstream",
        )
        downstream_setup_db, downstream_setup_engine = (
            create_lineage_downstream_session(
                downstream_connection
            )
        )
        downstream_verify_db, downstream_verify_engine = (
            create_lineage_downstream_session(
                downstream_connection
            )
        )

        _ensure_downstream_schema(downstream_setup_db)

        organization = _make_organization(authority_db)
        organization_ids.append(organization.id)

        collection_id = uuid.uuid4()
        collection_ids.append(collection_id)
        collection_name = f"sde-worker-{uuid.uuid4().hex}"
        document_id = "chunk-a"

        _insert_collection(
            downstream_setup_db,
            collection_id=collection_id,
            collection_name=collection_name,
            organization_id=organization.id,
        )
        _insert_document(
            downstream_setup_db,
            collection_id=collection_id,
            document_id=document_id,
        )
        downstream_setup_db.commit()

        intent = _establish_claimable_intent(
            authority_db,
            organization_id=organization.id,
            collection_name=collection_name,
            document_id=document_id,
        )
        authority_db.commit()

        worker_thread.start()

        terminal_result = _wait_for_terminal_result(
            authority_verify_db,
            organization_id=organization.id,
            intent_id=intent.id,
            timeout_seconds=10.0,
        )

        assert terminal_result.outcome == "succeeded"

        downstream_verify_db.rollback()
        assert not _document_exists(
            downstream_verify_db,
            collection_name=collection_name,
            document_id=document_id,
        )

        assert worker_thread.is_alive()

        attempt = (
            authority_verify_db.query(
                LineageInvalidationExecutionAttemptRecord
            )
            .filter(
                LineageInvalidationExecutionAttemptRecord.organization_id
                == organization.id,
                LineageInvalidationExecutionAttemptRecord.intent_id
                == intent.id,
            )
            .one()
        )

        assert attempt.id == terminal_result.attempt_id
        assert attempt.attempt_number == 1

    finally:
        worker.stop()

        if worker_thread.is_alive():
            worker_thread.join(timeout=5.0)

        assert not worker_thread.is_alive()

        authority_verify_db.close()
        authority_db.close()

        if downstream_verify_db is not None:
            downstream_verify_db.rollback()
            downstream_verify_db.close()

        if downstream_verify_engine is not None:
            downstream_verify_engine.dispose()

        if downstream_setup_db is not None:
            downstream_setup_db.rollback()
            downstream_setup_db.close()

        if downstream_setup_engine is not None:
            downstream_setup_engine.dispose()

        _cleanup_downstream_collections(collection_ids)
        _cleanup(organization_ids)


def test_execution_cycle_executes_authorized_intent_end_to_end(
    monkeypatch,
) -> None:
    authority_db = SessionLocal()
    downstream_setup_db = None
    downstream_setup_engine = None
    downstream_verify_db = None
    downstream_verify_engine = None
    organization_ids: list[uuid.UUID] = []
    collection_ids: list[uuid.UUID] = []

    try:
        monkeypatch.setattr(
            "src.services.lineage_downstream_connection_service."
            "settings.DOWNSTREAM_DATABASE_URLS",
            (
                '{"test-downstream":"'
                + DOWNSTREAM_DATABASE_URL
                + '"}'
            ),
        )

        downstream_connection = resolve_lineage_downstream_connection(
            connection_ref="test-downstream",
        )
        downstream_setup_db, downstream_setup_engine = (
            create_lineage_downstream_session(
                downstream_connection
            )
        )
        downstream_verify_db, downstream_verify_engine = (
            create_lineage_downstream_session(
                downstream_connection
            )
        )

        _ensure_downstream_schema(downstream_setup_db)

        organization = _make_organization(authority_db)
        organization_ids.append(organization.id)

        collection_id = uuid.uuid4()
        collection_ids.append(collection_id)
        collection_name = f"sde-cycle-{uuid.uuid4().hex}"
        document_id = "chunk-a"

        _insert_collection(
            downstream_setup_db,
            collection_id=collection_id,
            collection_name=collection_name,
            organization_id=organization.id,
        )
        _insert_document(
            downstream_setup_db,
            collection_id=collection_id,
            document_id=document_id,
        )
        downstream_setup_db.commit()

        intent = _establish_claimable_intent(
            authority_db,
            organization_id=organization.id,
            collection_name=collection_name,
            document_id=document_id,
        )
        authority_db.commit()

        result = run_lineage_invalidation_execution_cycle(
            organization_id=organization.id,
        )

        assert (
            result.status
            == LineageInvalidationExecutionCycleStatus.SUCCEEDED
        )
        assert result.candidate is not None
        assert result.candidate.organization_id == organization.id
        assert result.candidate.intent_id == intent.id
        assert result.attempt_id is not None
        assert result.reason_code is None

        downstream_verify_db.rollback()
        assert not _document_exists(
            downstream_verify_db,
            collection_name=collection_name,
            document_id=document_id,
        )

        authority_db.rollback()
        assert _result_count(
            authority_db,
            organization_id=organization.id,
            attempt_id=result.attempt_id,
        ) == 1

        terminal_result = (
            authority_db.query(
                LineageInvalidationExecutionResultRecord
            )
            .filter(
                LineageInvalidationExecutionResultRecord.organization_id
                == organization.id,
                LineageInvalidationExecutionResultRecord.attempt_id
                == result.attempt_id,
            )
            .one()
        )
        assert terminal_result.outcome == "succeeded"

    finally:
        authority_db.close()

        if downstream_verify_db is not None:
            downstream_verify_db.rollback()
            downstream_verify_db.close()

        if downstream_verify_engine is not None:
            downstream_verify_engine.dispose()

        if downstream_setup_db is not None:
            downstream_setup_db.rollback()
            downstream_setup_db.close()

        if downstream_setup_engine is not None:
            downstream_setup_engine.dispose()

        _cleanup_downstream_collections(collection_ids)
        _cleanup(organization_ids)


def test_execution_cycle_fails_closed_when_document_revision_advanced(
    monkeypatch,
) -> None:
    authority_db = SessionLocal()
    downstream_setup_db = None
    downstream_setup_engine = None
    downstream_verify_db = None
    downstream_verify_engine = None
    organization_ids: list[uuid.UUID] = []
    collection_ids: list[uuid.UUID] = []

    expected_revision = "chunk-a-r3"
    persisted_revision = "chunk-a-r4"

    try:
        monkeypatch.setattr(
            "src.services.lineage_downstream_connection_service."
            "settings.DOWNSTREAM_DATABASE_URLS",
            (
                '{"test-downstream":"'
                + DOWNSTREAM_DATABASE_URL
                + '"}'
            ),
        )

        downstream_connection = resolve_lineage_downstream_connection(
            connection_ref="test-downstream",
        )
        downstream_setup_db, downstream_setup_engine = (
            create_lineage_downstream_session(
                downstream_connection
            )
        )
        downstream_verify_db, downstream_verify_engine = (
            create_lineage_downstream_session(
                downstream_connection
            )
        )

        _ensure_downstream_schema(downstream_setup_db)

        organization = _make_organization(authority_db)
        organization_ids.append(organization.id)

        collection_id = uuid.uuid4()
        collection_ids.append(collection_id)
        collection_name = (
            f"sde-cycle-revision-fence-{uuid.uuid4().hex}"
        )
        document_id = "chunk-a"

        _insert_collection(
            downstream_setup_db,
            collection_id=collection_id,
            collection_name=collection_name,
            organization_id=organization.id,
        )
        _insert_document(
            downstream_setup_db,
            collection_id=collection_id,
            document_id=document_id,
            document_revision_external_id=persisted_revision,
        )
        downstream_setup_db.commit()

        intent = _establish_claimable_intent(
            authority_db,
            organization_id=organization.id,
            collection_name=collection_name,
            document_id=document_id,
            document_revision_external_id=expected_revision,
        )
        authority_db.commit()

        result = run_lineage_invalidation_execution_cycle(
            organization_id=organization.id,
        )

        assert (
            result.status
            == LineageInvalidationExecutionCycleStatus.FAILED
        )
        assert result.candidate is not None
        assert result.candidate.organization_id == organization.id
        assert result.candidate.intent_id == intent.id
        assert result.attempt_id is not None
        assert result.reason_code == "downstream_execution_failed"

        downstream_verify_db.rollback()
        assert _document_exists(
            downstream_verify_db,
            collection_name=collection_name,
            document_id=document_id,
        )

        persisted_document_revision = downstream_verify_db.execute(
            text(
                """
                SELECT
                    embedding.cmetadata
                    ->> 'chunk_revision_external_id'
                FROM langchain_pg_embedding AS embedding
                JOIN langchain_pg_collection AS collection
                  ON collection.uuid = embedding.collection_id
                WHERE collection.name = :collection_name
                  AND embedding.id = :document_id
                """
            ),
            {
                "collection_name": collection_name,
                "document_id": document_id,
            },
        ).scalar_one()

        assert persisted_document_revision == persisted_revision

        authority_db.rollback()
        assert _result_count(
            authority_db,
            organization_id=organization.id,
            attempt_id=result.attempt_id,
        ) == 1

        terminal_result = (
            authority_db.query(
                LineageInvalidationExecutionResultRecord
            )
            .filter(
                LineageInvalidationExecutionResultRecord.organization_id
                == organization.id,
                LineageInvalidationExecutionResultRecord.attempt_id
                == result.attempt_id,
            )
            .one()
        )
        assert terminal_result.outcome == "failed"

        with pytest.raises(
            LineageInvalidationExecutionClaimTerminalError
        ):
            claim_lineage_invalidation_execution(
                authority_db,
                organization_id=organization.id,
                intent_id=intent.id,
                lease_seconds=300,
            )
        authority_db.rollback()
    finally:
        authority_db.close()

        if downstream_verify_db is not None:
            downstream_verify_db.rollback()
            downstream_verify_db.close()

        if downstream_verify_engine is not None:
            downstream_verify_engine.dispose()

        if downstream_setup_db is not None:
            downstream_setup_db.rollback()
            downstream_setup_db.close()

        if downstream_setup_engine is not None:
            downstream_setup_engine.dispose()

        _cleanup_downstream_collections(collection_ids)
        _cleanup(organization_ids)


def test_execution_cycle_is_idle_without_authorized_intent() -> None:
    authority_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(authority_db)
        organization_ids.append(organization.id)
        authority_db.commit()

        result = run_lineage_invalidation_execution_cycle(
            organization_id=organization.id,
        )

        assert (
            result.status
            == LineageInvalidationExecutionCycleStatus.IDLE
        )
        assert result.candidate is None
        assert result.attempt_id is None
        assert result.reason_code is None

    finally:
        authority_db.close()
        _cleanup(organization_ids)


def test_execution_cycle_does_not_terminalize_connection_resolution_failure(
    monkeypatch,
) -> None:
    authority_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        monkeypatch.setattr(
            "src.services.lineage_downstream_connection_service."
            "settings.DOWNSTREAM_DATABASE_URLS",
            "{}",
        )

        organization = _make_organization(authority_db)
        organization_ids.append(organization.id)

        intent = _establish_claimable_intent(
            authority_db,
            organization_id=organization.id,
            collection_name=f"missing-{uuid.uuid4().hex}",
            document_id=f"document-{uuid.uuid4().hex}",
        )
        authority_db.commit()

        with pytest.raises(LineageDownstreamConnectionNotFoundError):
            run_lineage_invalidation_execution_cycle(
                organization_id=organization.id,
            )

        authority_db.rollback()

        attempt = (
            authority_db.query(
                LineageInvalidationExecutionAttemptRecord
            )
            .filter(
                LineageInvalidationExecutionAttemptRecord.organization_id
                == organization.id,
                LineageInvalidationExecutionAttemptRecord.intent_id
                == intent.id,
            )
            .one()
        )

        assert _result_count(
            authority_db,
            organization_id=organization.id,
            attempt_id=attempt.id,
        ) == 0

    finally:
        authority_db.close()
        _cleanup(organization_ids)


def test_execution_cycle_does_not_terminalize_ambiguous_downstream_commit(
    monkeypatch,
) -> None:
    authority_db = SessionLocal()
    downstream_setup_db = None
    downstream_setup_engine = None
    downstream_verify_db = None
    downstream_verify_engine = None
    organization_ids: list[uuid.UUID] = []
    collection_ids: list[uuid.UUID] = []

    try:
        monkeypatch.setattr(
            "src.services.lineage_downstream_connection_service."
            "settings.DOWNSTREAM_DATABASE_URLS",
            (
                '{"test-downstream":"'
                + DOWNSTREAM_DATABASE_URL
                + '"}'
            ),
        )

        downstream_connection = resolve_lineage_downstream_connection(
            connection_ref="test-downstream",
        )
        downstream_setup_db, downstream_setup_engine = (
            create_lineage_downstream_session(
                downstream_connection
            )
        )
        downstream_verify_db, downstream_verify_engine = (
            create_lineage_downstream_session(
                downstream_connection
            )
        )

        _ensure_downstream_schema(downstream_setup_db)

        organization = _make_organization(authority_db)
        organization_ids.append(organization.id)

        collection_id = uuid.uuid4()
        collection_ids.append(collection_id)
        collection_name = f"sde-cycle-commit-failure-{uuid.uuid4().hex}"
        document_id = "chunk-a"

        _insert_collection(
            downstream_setup_db,
            collection_id=collection_id,
            collection_name=collection_name,
            organization_id=organization.id,
        )
        _insert_document(
            downstream_setup_db,
            collection_id=collection_id,
            document_id=document_id,
        )
        downstream_setup_db.commit()

        intent = _establish_claimable_intent(
            authority_db,
            organization_id=organization.id,
            collection_name=collection_name,
            document_id=document_id,
        )
        authority_db.commit()

        real_create_downstream_session = (
            create_lineage_downstream_session
        )

        def create_downstream_session_with_failing_commit(connection):
            downstream_db, downstream_engine = (
                real_create_downstream_session(connection)
            )

            def fail_commit() -> None:
                raise RuntimeError("simulated downstream commit failure")

            downstream_db.commit = fail_commit
            return downstream_db, downstream_engine

        monkeypatch.setattr(
            "src.services.lineage_invalidation_execution_cycle_service."
            "create_lineage_downstream_session",
            create_downstream_session_with_failing_commit,
        )

        with pytest.raises(
            RuntimeError,
            match="simulated downstream commit failure",
        ):
            run_lineage_invalidation_execution_cycle(
                organization_id=organization.id,
            )

        downstream_verify_db.rollback()
        assert _document_exists(
            downstream_verify_db,
            collection_name=collection_name,
            document_id=document_id,
        )

        authority_db.rollback()

        attempt = (
            authority_db.query(LineageInvalidationExecutionAttemptRecord)
            .filter(
                LineageInvalidationExecutionAttemptRecord.organization_id
                == organization.id,
            )
            .one()
        )

        assert (
            _result_count(
                authority_db,
                organization_id=organization.id,
                attempt_id=attempt.id,
            )
            == 0
        )
    finally:
        authority_db.rollback()

        if downstream_setup_db is not None:
            downstream_setup_db.rollback()
            downstream_setup_db.close()

        if downstream_setup_engine is not None:
            downstream_setup_engine.dispose()

        if downstream_verify_db is not None:
            downstream_verify_db.rollback()
            downstream_verify_db.close()

        if downstream_verify_engine is not None:
            downstream_verify_engine.dispose()

        _cleanup_downstream_collections(collection_ids)
        _cleanup(organization_ids)


def test_execution_cycle_recovers_after_result_publication_failure(
    monkeypatch,
) -> None:
    authority_db = SessionLocal()
    downstream_setup_db = None
    downstream_setup_engine = None
    downstream_verify_db = None
    downstream_verify_engine = None
    organization_ids: list[uuid.UUID] = []
    collection_ids: list[uuid.UUID] = []

    try:
        monkeypatch.setattr(
            "src.services.lineage_downstream_connection_service."
            "settings.DOWNSTREAM_DATABASE_URLS",
            (
                '{"test-downstream":"'
                + DOWNSTREAM_DATABASE_URL
                + '"}'
            ),
        )

        downstream_connection = resolve_lineage_downstream_connection(
            connection_ref="test-downstream",
        )
        downstream_setup_db, downstream_setup_engine = (
            create_lineage_downstream_session(
                downstream_connection
            )
        )
        downstream_verify_db, downstream_verify_engine = (
            create_lineage_downstream_session(
                downstream_connection
            )
        )

        _ensure_downstream_schema(downstream_setup_db)

        organization = _make_organization(authority_db)
        organization_ids.append(organization.id)

        collection_id = uuid.uuid4()
        collection_ids.append(collection_id)
        collection_name = (
            f"sde-cycle-publication-recovery-{uuid.uuid4().hex}"
        )
        document_id = "chunk-a"

        _insert_collection(
            downstream_setup_db,
            collection_id=collection_id,
            collection_name=collection_name,
            organization_id=organization.id,
        )
        _insert_document(
            downstream_setup_db,
            collection_id=collection_id,
            document_id=document_id,
        )
        downstream_setup_db.commit()

        intent = _establish_claimable_intent(
            authority_db,
            organization_id=organization.id,
            collection_name=collection_name,
            document_id=document_id,
        )
        authority_db.commit()

        from src.services import (
            lineage_invalidation_execution_cycle_service as cycle_service,
        )

        real_publish_result = cycle_service._publish_result

        def fail_result_publication(
            *,
            organization_id: uuid.UUID,
            attempt_id: uuid.UUID,
            outcome: str,
        ) -> None:
            assert organization_id == organization.id
            assert outcome == "succeeded"
            raise RuntimeError("simulated result publication failure")

        monkeypatch.setattr(
            cycle_service,
            "_publish_result",
            fail_result_publication,
        )

        with pytest.raises(
            RuntimeError,
            match="simulated result publication failure",
        ):
            run_lineage_invalidation_execution_cycle(
                organization_id=organization.id,
            )

        downstream_verify_db.rollback()
        assert not _document_exists(
            downstream_verify_db,
            collection_name=collection_name,
            document_id=document_id,
        )

        authority_db.rollback()

        first_attempt = (
            authority_db.query(LineageInvalidationExecutionAttemptRecord)
            .filter(
                LineageInvalidationExecutionAttemptRecord.organization_id
                == organization.id,
                LineageInvalidationExecutionAttemptRecord.intent_id
                == intent.id,
            )
            .one()
        )

        assert first_attempt.attempt_number == 1
        assert (
            _result_count(
                authority_db,
                organization_id=organization.id,
                attempt_id=first_attempt.id,
            )
            == 0
        )

        _expire_attempt(
            authority_db,
            attempt_id=first_attempt.id,
        )

        monkeypatch.setattr(
            cycle_service,
            "_publish_result",
            real_publish_result,
        )

        replay = run_lineage_invalidation_execution_cycle(
            organization_id=organization.id,
        )

        assert replay.status == LineageInvalidationExecutionCycleStatus.SUCCEEDED
        assert replay.candidate is not None
        assert replay.candidate.organization_id == organization.id
        assert replay.candidate.intent_id == intent.id
        assert replay.attempt_id is not None
        assert replay.attempt_id != first_attempt.id

        authority_db.rollback()

        attempts = (
            authority_db.query(LineageInvalidationExecutionAttemptRecord)
            .filter(
                LineageInvalidationExecutionAttemptRecord.organization_id
                == organization.id,
                LineageInvalidationExecutionAttemptRecord.intent_id
                == intent.id,
            )
            .order_by(
                LineageInvalidationExecutionAttemptRecord.attempt_number.asc(),
            )
            .all()
        )

        assert len(attempts) == 2
        assert attempts[0].id == first_attempt.id
        assert attempts[0].attempt_number == 1
        assert attempts[1].id == replay.attempt_id
        assert attempts[1].attempt_number == 2
        assert (
            attempts[1].binding_revision
            == attempts[0].binding_revision
        )

        assert (
            _result_count(
                authority_db,
                organization_id=organization.id,
                attempt_id=attempts[0].id,
            )
            == 0
        )
        assert (
            _result_count(
                authority_db,
                organization_id=organization.id,
                attempt_id=attempts[1].id,
            )
            == 1
        )

        replay_result = (
            authority_db.query(LineageInvalidationExecutionResultRecord)
            .filter(
                LineageInvalidationExecutionResultRecord.organization_id
                == organization.id,
                LineageInvalidationExecutionResultRecord.attempt_id
                == attempts[1].id,
            )
            .one()
        )

        assert replay_result.outcome == "succeeded"

        downstream_verify_db.rollback()
        assert not _document_exists(
            downstream_verify_db,
            collection_name=collection_name,
            document_id=document_id,
        )
    finally:
        authority_db.rollback()

        if downstream_setup_db is not None:
            downstream_setup_db.rollback()
            downstream_setup_db.close()

        if downstream_setup_engine is not None:
            downstream_setup_engine.dispose()

        if downstream_verify_db is not None:
            downstream_verify_db.rollback()
            downstream_verify_db.close()

        if downstream_verify_engine is not None:
            downstream_verify_engine.dispose()

        _cleanup_downstream_collections(collection_ids)
        _cleanup(organization_ids)


def test_retry_preserves_new_document_revision_after_publication_failure(
    monkeypatch,
) -> None:
    authority_db = SessionLocal()
    downstream_setup_db = None
    downstream_setup_engine = None
    downstream_verify_db = None
    downstream_verify_engine = None
    organization_ids: list[uuid.UUID] = []
    collection_ids: list[uuid.UUID] = []

    try:
        monkeypatch.setattr(
            "src.services.lineage_downstream_connection_service."
            "settings.DOWNSTREAM_DATABASE_URLS",
            (
                '{"test-downstream":"'
                + DOWNSTREAM_DATABASE_URL
                + '"}'
            ),
        )

        downstream_connection = resolve_lineage_downstream_connection(
            connection_ref="test-downstream",
        )
        downstream_setup_db, downstream_setup_engine = (
            create_lineage_downstream_session(
                downstream_connection
            )
        )
        downstream_verify_db, downstream_verify_engine = (
            create_lineage_downstream_session(
                downstream_connection
            )
        )

        _ensure_downstream_schema(downstream_setup_db)

        organization = _make_organization(authority_db)
        organization_ids.append(organization.id)

        collection_id = uuid.uuid4()
        collection_ids.append(collection_id)
        collection_name = (
            f"sde-cycle-publication-recovery-{uuid.uuid4().hex}"
        )
        document_id = "chunk-a"

        _insert_collection(
            downstream_setup_db,
            collection_id=collection_id,
            collection_name=collection_name,
            organization_id=organization.id,
        )
        _insert_document(
            downstream_setup_db,
            collection_id=collection_id,
            document_id=document_id,
        )
        downstream_setup_db.commit()

        intent = _establish_claimable_intent(
            authority_db,
            organization_id=organization.id,
            collection_name=collection_name,
            document_id=document_id,
        )
        authority_db.commit()

        from src.services import (
            lineage_invalidation_execution_cycle_service as cycle_service,
        )

        real_publish_result = cycle_service._publish_result

        def fail_result_publication(
            *,
            organization_id: uuid.UUID,
            attempt_id: uuid.UUID,
            outcome: str,
        ) -> None:
            assert organization_id == organization.id
            assert outcome == "succeeded"
            raise RuntimeError("simulated result publication failure")

        monkeypatch.setattr(
            cycle_service,
            "_publish_result",
            fail_result_publication,
        )

        with pytest.raises(
            RuntimeError,
            match="simulated result publication failure",
        ):
            run_lineage_invalidation_execution_cycle(
                organization_id=organization.id,
            )

        downstream_verify_db.rollback()
        assert not _document_exists(
            downstream_verify_db,
            collection_name=collection_name,
            document_id=document_id,
        )

        authority_db.rollback()

        first_attempt = (
            authority_db.query(LineageInvalidationExecutionAttemptRecord)
            .filter(
                LineageInvalidationExecutionAttemptRecord.organization_id
                == organization.id,
                LineageInvalidationExecutionAttemptRecord.intent_id
                == intent.id,
            )
            .one()
        )

        assert first_attempt.attempt_number == 1
        assert (
            _result_count(
                authority_db,
                organization_id=organization.id,
                attempt_id=first_attempt.id,
            )
            == 0
        )

        _expire_attempt(
            authority_db,
            attempt_id=first_attempt.id,
        )

        _insert_document(
            downstream_setup_db,
            collection_id=collection_id,
            document_id=document_id,
            document_revision_external_id="chunk-a-r4",
        )
        downstream_setup_db.commit()

        monkeypatch.setattr(
            cycle_service,
            "_publish_result",
            real_publish_result,
        )

        replay = run_lineage_invalidation_execution_cycle(
            organization_id=organization.id,
        )

        assert replay.status == LineageInvalidationExecutionCycleStatus.FAILED
        assert replay.reason_code == "downstream_execution_failed"
        assert replay.candidate is not None
        assert replay.candidate.organization_id == organization.id
        assert replay.candidate.intent_id == intent.id
        assert replay.attempt_id is not None
        assert replay.attempt_id != first_attempt.id

        authority_db.rollback()

        attempts = (
            authority_db.query(LineageInvalidationExecutionAttemptRecord)
            .filter(
                LineageInvalidationExecutionAttemptRecord.organization_id
                == organization.id,
                LineageInvalidationExecutionAttemptRecord.intent_id
                == intent.id,
            )
            .order_by(
                LineageInvalidationExecutionAttemptRecord.attempt_number.asc(),
            )
            .all()
        )

        assert len(attempts) == 2
        assert attempts[0].id == first_attempt.id
        assert attempts[0].attempt_number == 1
        assert attempts[1].id == replay.attempt_id
        assert attempts[1].attempt_number == 2
        assert (
            attempts[1].binding_revision
            == attempts[0].binding_revision
        )

        assert (
            _result_count(
                authority_db,
                organization_id=organization.id,
                attempt_id=attempts[0].id,
            )
            == 0
        )
        assert (
            _result_count(
                authority_db,
                organization_id=organization.id,
                attempt_id=attempts[1].id,
            )
            == 1
        )

        replay_result = (
            authority_db.query(LineageInvalidationExecutionResultRecord)
            .filter(
                LineageInvalidationExecutionResultRecord.organization_id
                == organization.id,
                LineageInvalidationExecutionResultRecord.attempt_id
                == attempts[1].id,
            )
            .one()
        )

        assert replay_result.outcome == "failed"

        downstream_verify_db.rollback()
        assert _document_exists(
            downstream_verify_db,
            collection_name=collection_name,
            document_id=document_id,
        )
    finally:
        authority_db.rollback()

        if downstream_setup_db is not None:
            downstream_setup_db.rollback()
            downstream_setup_db.close()

        if downstream_setup_engine is not None:
            downstream_setup_engine.dispose()

        if downstream_verify_db is not None:
            downstream_verify_db.rollback()
            downstream_verify_db.close()

        if downstream_verify_engine is not None:
            downstream_verify_engine.dispose()

        _cleanup_downstream_collections(collection_ids)
        _cleanup(organization_ids)


def test_historical_unsafe_attempt_recovers_after_safe_binding_upgrade(
    monkeypatch,
) -> None:
    authority_db = SessionLocal()
    downstream_setup_db = None
    downstream_setup_engine = None
    downstream_verify_db = None
    downstream_verify_engine = None
    organization_ids: list[uuid.UUID] = []
    collection_ids: list[uuid.UUID] = []

    try:
        monkeypatch.setattr(
            "src.services.lineage_downstream_connection_service."
            "settings.DOWNSTREAM_DATABASE_URLS",
            (
                '{"test-downstream":"'
                + DOWNSTREAM_DATABASE_URL
                + '"}'
            ),
        )

        downstream_connection = resolve_lineage_downstream_connection(
            connection_ref="test-downstream",
        )
        downstream_setup_db, downstream_setup_engine = (
            create_lineage_downstream_session(downstream_connection)
        )
        downstream_verify_db, downstream_verify_engine = (
            create_lineage_downstream_session(downstream_connection)
        )

        _ensure_downstream_schema(downstream_setup_db)

        organization = _make_organization(authority_db)
        organization_ids.append(organization.id)

        collection_id = uuid.uuid4()
        collection_ids.append(collection_id)
        collection_name = f"sde-historical-recovery-{uuid.uuid4().hex}"
        document_id = "chunk-a"

        _insert_collection(
            downstream_setup_db,
            collection_id=collection_id,
            collection_name=collection_name,
            organization_id=organization.id,
        )
        _insert_document(
            downstream_setup_db,
            collection_id=collection_id,
            document_id=document_id,
            document_revision_external_id="chunk-a-r3",
        )
        downstream_setup_db.commit()

        intent = _establish_claimable_intent(
            authority_db,
            organization_id=organization.id,
            collection_name=collection_name,
            document_id=document_id,
            document_revision_external_id=None,
        )
        authority_db.commit()

        now = authority_db.execute(
            text("SELECT clock_timestamp()")
        ).scalar_one()

        historical_attempt = LineageInvalidationExecutionAttemptRecord(
            organization_id=organization.id,
            intent_id=intent.id,
            attempt_number=1,
            binding_revision=1,
            claimed_at=now,
            lease_expires_at=now + timedelta(seconds=300),
        )
        authority_db.add(historical_attempt)
        authority_db.commit()
        historical_attempt_id = historical_attempt.id

        persist_lineage_execution_target_binding(
            authority_db,
            organization_id=organization.id,
            binding=LineageExecutionTargetBinding(
                identity=LineageExecutionTargetIdentity(
                    source_external_id="source-a",
                    asset_external_id="index-a",
                ),
                binding_revision=2,
                provider="pgvector",
                target_kind="langchain_pgvector_causal_chunk",
                configuration={
                    "connection_ref": "test-downstream",
                    "collection_name": collection_name,
                },
            ),
        )
        authority_db.commit()

        blocked = run_lineage_invalidation_execution_cycle(
            organization_id=organization.id,
        )

        assert blocked.status == LineageInvalidationExecutionCycleStatus.IDLE
        assert blocked.attempt_id is None

        authority_db.rollback()

        attempts_before_expiry = (
            authority_db.query(LineageInvalidationExecutionAttemptRecord)
            .filter(
                LineageInvalidationExecutionAttemptRecord.organization_id
                == organization.id,
                LineageInvalidationExecutionAttemptRecord.intent_id
                == intent.id,
            )
            .all()
        )

        assert len(attempts_before_expiry) == 1
        assert attempts_before_expiry[0].id == historical_attempt_id
        assert attempts_before_expiry[0].binding_revision == 1

        downstream_verify_db.rollback()
        assert _document_exists(
            downstream_verify_db,
            collection_name=collection_name,
            document_id=document_id,
        )

        _expire_attempt(
            authority_db,
            attempt_id=historical_attempt_id,
        )

        recovered = run_lineage_invalidation_execution_cycle(
            organization_id=organization.id,
        )

        assert recovered.status == LineageInvalidationExecutionCycleStatus.SUCCEEDED
        assert recovered.attempt_id is not None
        assert recovered.attempt_id != historical_attempt_id
        assert recovered.reason_code is None

        authority_db.rollback()

        attempts = (
            authority_db.query(LineageInvalidationExecutionAttemptRecord)
            .filter(
                LineageInvalidationExecutionAttemptRecord.organization_id
                == organization.id,
                LineageInvalidationExecutionAttemptRecord.intent_id
                == intent.id,
            )
            .order_by(
                LineageInvalidationExecutionAttemptRecord.attempt_number.asc(),
            )
            .all()
        )

        assert len(attempts) == 2
        assert attempts[0].id == historical_attempt_id
        assert attempts[0].attempt_number == 1
        assert attempts[0].binding_revision == 1
        assert attempts[1].id == recovered.attempt_id
        assert attempts[1].attempt_number == 2
        assert attempts[1].binding_revision == 2

        assert _result_count(
            authority_db,
            organization_id=organization.id,
            attempt_id=historical_attempt_id,
        ) == 0

        recovered_result = (
            authority_db.query(LineageInvalidationExecutionResultRecord)
            .filter(
                LineageInvalidationExecutionResultRecord.organization_id
                == organization.id,
                LineageInvalidationExecutionResultRecord.attempt_id
                == recovered.attempt_id,
            )
            .one()
        )

        assert recovered_result.outcome == "succeeded"

        downstream_verify_db.rollback()
        assert not _document_exists(
            downstream_verify_db,
            collection_name=collection_name,
            document_id=document_id,
        )

    finally:
        authority_db.rollback()
        authority_db.close()

        if downstream_verify_db is not None:
            downstream_verify_db.rollback()
            downstream_verify_db.close()

        if downstream_verify_engine is not None:
            downstream_verify_engine.dispose()

        if downstream_setup_db is not None:
            downstream_setup_db.rollback()
            downstream_setup_db.close()

        if downstream_setup_engine is not None:
            downstream_setup_engine.dispose()

        _cleanup_downstream_collections(collection_ids)
        _cleanup(organization_ids)


def test_execution_cycle_terminalizes_definite_downstream_failure(
    monkeypatch,
) -> None:
    authority_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []
    collection_ids: list[uuid.UUID] = []

    try:
        monkeypatch.setattr(
            "src.services.lineage_downstream_connection_service."
            "settings.DOWNSTREAM_DATABASE_URLS",
            (
                '{"test-downstream":"'
                + DOWNSTREAM_DATABASE_URL
                + '"}'
            ),
        )

        organization = _make_organization(authority_db)
        organization_ids.append(organization.id)

        collection_name = f"sde-cycle-failure-{uuid.uuid4().hex}"
        document_id = "chunk-a"

        _establish_claimable_intent(
            authority_db,
            organization_id=organization.id,
            collection_name=collection_name,
            document_id=document_id,
        )
        authority_db.commit()

        def fail_invalidation(
            db,
            *,
            configuration,
        ):
            raise RuntimeError("simulated definite downstream failure")

        monkeypatch.setattr(
            "src.services.lineage_invalidation_execution_cycle_service."
            "invalidate_lineage_pgvector_causal_chunk",
            fail_invalidation,
        )

        result = run_lineage_invalidation_execution_cycle(
            organization_id=organization.id,
        )

        assert result.status == LineageInvalidationExecutionCycleStatus.FAILED
        assert result.candidate is not None
        assert result.candidate.organization_id == organization.id
        assert result.attempt_id is not None
        assert result.reason_code == "downstream_execution_failed"

        authority_db.rollback()

        persisted_result = (
            authority_db.query(LineageInvalidationExecutionResultRecord)
            .filter(
                LineageInvalidationExecutionResultRecord.organization_id
                == organization.id,
                LineageInvalidationExecutionResultRecord.attempt_id
                == result.attempt_id,
            )
            .one()
        )

        assert persisted_result.outcome == "failed"
    finally:
        authority_db.rollback()
        _cleanup_downstream_collections(collection_ids)
        _cleanup(organization_ids)


def test_execution_cycle_skips_unclaimable_oldest_candidate(
    monkeypatch,
) -> None:
    authority_db = SessionLocal()
    downstream_setup_db = None
    downstream_setup_engine = None
    downstream_verify_db = None
    downstream_verify_engine = None
    organization_ids: list[uuid.UUID] = []
    collection_ids: list[uuid.UUID] = []

    try:
        monkeypatch.setattr(
            "src.services.lineage_downstream_connection_service."
            "settings.DOWNSTREAM_DATABASE_URLS",
            (
                '{"test-downstream":"'
                + DOWNSTREAM_DATABASE_URL
                + '"}'
            ),
        )

        downstream_connection = resolve_lineage_downstream_connection(
            connection_ref="test-downstream",
        )
        downstream_setup_db, downstream_setup_engine = (
            create_lineage_downstream_session(
                downstream_connection
            )
        )
        downstream_verify_db, downstream_verify_engine = (
            create_lineage_downstream_session(
                downstream_connection
            )
        )

        _ensure_downstream_schema(downstream_setup_db)

        stale_organization = _make_organization(authority_db)
        valid_organization = _make_organization(authority_db)
        organization_ids.extend(
            [
                stale_organization.id,
                valid_organization.id,
            ]
        )

        stale_intent = _establish_claimable_intent(
            authority_db,
            organization_id=stale_organization.id,
            collection_name=f"stale-{uuid.uuid4().hex}",
            document_id=f"stale-document-{uuid.uuid4().hex}",
        )
        authority_db.commit()

        activate_lineage_invalidation_policy(
            authority_db,
            organization_id=stale_organization.id,
            policy=_policy(8),
        )
        authority_db.commit()

        collection_id = uuid.uuid4()
        collection_ids.append(collection_id)
        collection_name = f"sde-cycle-starvation-{uuid.uuid4().hex}"
        document_id = "chunk-a"

        _insert_collection(
            downstream_setup_db,
            collection_id=collection_id,
            collection_name=collection_name,
            organization_id=valid_organization.id,
        )
        _insert_document(
            downstream_setup_db,
            collection_id=collection_id,
            document_id=document_id,
        )
        downstream_setup_db.commit()

        valid_intent = _establish_claimable_intent(
            authority_db,
            organization_id=valid_organization.id,
            collection_name=collection_name,
            document_id=document_id,
        )
        authority_db.commit()

        result = run_lineage_invalidation_execution_cycle()

        assert result.status == LineageInvalidationExecutionCycleStatus.SUCCEEDED
        assert result.candidate is not None
        assert result.candidate.organization_id == valid_organization.id
        assert result.candidate.intent_id == valid_intent.id
        assert result.attempt_id is not None

        authority_db.rollback()

        stale_attempt_count = (
            authority_db.query(
                func.count(LineageInvalidationExecutionAttemptRecord.id)
            )
            .filter(
                LineageInvalidationExecutionAttemptRecord.organization_id
                == stale_organization.id,
                LineageInvalidationExecutionAttemptRecord.intent_id
                == stale_intent.id,
            )
            .scalar()
        )

        valid_attempt_count = (
            authority_db.query(
                func.count(LineageInvalidationExecutionAttemptRecord.id)
            )
            .filter(
                LineageInvalidationExecutionAttemptRecord.organization_id
                == valid_organization.id,
                LineageInvalidationExecutionAttemptRecord.intent_id
                == valid_intent.id,
            )
            .scalar()
        )

        assert stale_attempt_count == 0
        assert valid_attempt_count == 1

        assert (
            _result_count(
                authority_db,
                organization_id=valid_organization.id,
                attempt_id=result.attempt_id,
            )
            == 1
        )

        downstream_verify_db.rollback()
        assert not _document_exists(
            downstream_verify_db,
            collection_name=collection_name,
            document_id=document_id,
        )
    finally:
        authority_db.rollback()

        if downstream_setup_db is not None:
            downstream_setup_db.rollback()
            downstream_setup_db.close()

        if downstream_setup_engine is not None:
            downstream_setup_engine.dispose()

        if downstream_verify_db is not None:
            downstream_verify_db.rollback()
            downstream_verify_db.close()

        if downstream_verify_engine is not None:
            downstream_verify_engine.dispose()

        _cleanup_downstream_collections(collection_ids)
        _cleanup(organization_ids)


def test_execution_cycle_pages_past_full_unclaimable_batch(
    monkeypatch,
) -> None:
    authority_db = SessionLocal()
    downstream_setup_db = None
    downstream_setup_engine = None
    downstream_verify_db = None
    downstream_verify_engine = None
    organization_ids: list[uuid.UUID] = []
    collection_ids: list[uuid.UUID] = []

    try:
        monkeypatch.setattr(
            "src.services.lineage_downstream_connection_service."
            "settings.DOWNSTREAM_DATABASE_URLS",
            (
                '{"test-downstream":"'
                + DOWNSTREAM_DATABASE_URL
                + '"}'
            ),
        )

        downstream_connection = resolve_lineage_downstream_connection(
            connection_ref="test-downstream",
        )
        downstream_setup_db, downstream_setup_engine = (
            create_lineage_downstream_session(
                downstream_connection
            )
        )
        downstream_verify_db, downstream_verify_engine = (
            create_lineage_downstream_session(
                downstream_connection
            )
        )

        _ensure_downstream_schema(downstream_setup_db)

        stale_intents: list[
            tuple[uuid.UUID, uuid.UUID]
        ] = []

        for _ in range(33):
            stale_organization = _make_organization(authority_db)
            organization_ids.append(stale_organization.id)

            stale_intent = _establish_claimable_intent(
                authority_db,
                organization_id=stale_organization.id,
                collection_name=f"stale-{uuid.uuid4().hex}",
                document_id=f"stale-document-{uuid.uuid4().hex}",
            )
            authority_db.commit()

            activate_lineage_invalidation_policy(
                authority_db,
                organization_id=stale_organization.id,
                policy=_policy(8),
            )
            authority_db.commit()

            stale_intents.append(
                (
                    stale_organization.id,
                    stale_intent.id,
                )
            )

        valid_organization = _make_organization(authority_db)
        organization_ids.append(valid_organization.id)

        collection_id = uuid.uuid4()
        collection_ids.append(collection_id)
        collection_name = (
            f"sde-cycle-pagination-{uuid.uuid4().hex}"
        )
        document_id = "chunk-a"

        _insert_collection(
            downstream_setup_db,
            collection_id=collection_id,
            collection_name=collection_name,
            organization_id=valid_organization.id,
        )
        _insert_document(
            downstream_setup_db,
            collection_id=collection_id,
            document_id=document_id,
        )
        downstream_setup_db.commit()

        valid_intent = _establish_claimable_intent(
            authority_db,
            organization_id=valid_organization.id,
            collection_name=collection_name,
            document_id=document_id,
        )
        authority_db.commit()

        result = run_lineage_invalidation_execution_cycle()

        assert result.status == LineageInvalidationExecutionCycleStatus.SUCCEEDED
        assert result.candidate is not None
        assert result.candidate.organization_id == valid_organization.id
        assert result.candidate.intent_id == valid_intent.id
        assert result.attempt_id is not None

        authority_db.rollback()

        for stale_organization_id, stale_intent_id in stale_intents:
            stale_attempt_count = (
                authority_db.query(
                    func.count(
                        LineageInvalidationExecutionAttemptRecord.id
                    )
                )
                .filter(
                    LineageInvalidationExecutionAttemptRecord.organization_id
                    == stale_organization_id,
                    LineageInvalidationExecutionAttemptRecord.intent_id
                    == stale_intent_id,
                )
                .scalar()
            )

            assert stale_attempt_count == 0

        valid_attempt_count = (
            authority_db.query(
                func.count(LineageInvalidationExecutionAttemptRecord.id)
            )
            .filter(
                LineageInvalidationExecutionAttemptRecord.organization_id
                == valid_organization.id,
                LineageInvalidationExecutionAttemptRecord.intent_id
                == valid_intent.id,
            )
            .scalar()
        )

        assert valid_attempt_count == 1

        assert (
            _result_count(
                authority_db,
                organization_id=valid_organization.id,
                attempt_id=result.attempt_id,
            )
            == 1
        )

        downstream_verify_db.rollback()
        assert not _document_exists(
            downstream_verify_db,
            collection_name=collection_name,
            document_id=document_id,
        )
    finally:
        authority_db.rollback()

        if downstream_setup_db is not None:
            downstream_setup_db.rollback()
            downstream_setup_db.close()

        if downstream_setup_engine is not None:
            downstream_setup_engine.dispose()

        if downstream_verify_db is not None:
            downstream_verify_db.rollback()
            downstream_verify_db.close()

        if downstream_verify_engine is not None:
            downstream_verify_engine.dispose()

        _cleanup_downstream_collections(collection_ids)
        _cleanup(organization_ids)

def test_execution_cycle_rejects_unrelated_physical_document_binding(
    monkeypatch,
) -> None:
    """An index decision must not delete a document outside its causal path."""
    authority_db = SessionLocal()
    downstream_db = None
    downstream_engine = None
    verify_db = None
    verify_engine = None
    organization_ids: list[uuid.UUID] = []
    collection_ids: list[uuid.UUID] = []

    try:
        monkeypatch.setattr(
            "src.services.lineage_downstream_connection_service."
            "settings.DOWNSTREAM_DATABASE_URLS",
            '{"test-downstream":"' + DOWNSTREAM_DATABASE_URL + '"}',
        )

        connection = resolve_lineage_downstream_connection(
            connection_ref="test-downstream",
        )
        downstream_db, downstream_engine = create_lineage_downstream_session(
            connection,
        )
        verify_db, verify_engine = create_lineage_downstream_session(
            connection,
        )
        _ensure_downstream_schema(downstream_db)

        organization = _make_organization(authority_db)
        organization_ids.append(organization.id)
        collection_id = uuid.uuid4()
        collection_ids.append(collection_id)
        collection_name = f"sde-causal-binding-{uuid.uuid4().hex}"
        impacted_document_id = f"chunk-a-{uuid.uuid4().hex}"
        unrelated_document_id = f"unrelated-{uuid.uuid4().hex}"

        _insert_collection(
            downstream_db,
            collection_id=collection_id,
            collection_name=collection_name,
            organization_id=organization.id,
        )
        _insert_document(
            downstream_db,
            collection_id=collection_id,
            document_id=impacted_document_id,
            document_revision_external_id="chunk-a-r3",
        )
        _insert_document(
            downstream_db,
            collection_id=collection_id,
            document_id=unrelated_document_id,
            document_revision_external_id="unrelated-r1",
        )
        downstream_db.commit()

        intent = _establish_claimable_intent(
            authority_db,
            organization_id=organization.id,
            collection_name=collection_name,
            document_id=unrelated_document_id,
            document_revision_external_id="unrelated-r1",
            legacy_target=True,
        )
        authority_db.commit()

        result = run_lineage_invalidation_execution_cycle(
            organization_id=organization.id,
        )

        # The immutable decision's causal path is chunk-a-r3 -> index-a-r7.
        # The configured physical row is not part of that path, even though
        # its stored revision matches the configuration's own revision.
        assert result.status != LineageInvalidationExecutionCycleStatus.SUCCEEDED

        verify_db.rollback()
        assert _document_exists(
            verify_db,
            collection_name=collection_name,
            document_id=impacted_document_id,
        )
        assert _document_exists(
            verify_db,
            collection_name=collection_name,
            document_id=unrelated_document_id,
        )
    finally:
        authority_db.close()
        if verify_db is not None:
            verify_db.rollback()
            verify_db.close()
        if verify_engine is not None:
            verify_engine.dispose()
        if downstream_db is not None:
            downstream_db.rollback()
            downstream_db.close()
        if downstream_engine is not None:
            downstream_engine.dispose()
        _cleanup_downstream_collections(collection_ids)
        _cleanup(organization_ids)
