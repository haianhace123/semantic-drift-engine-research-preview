from __future__ import annotations

import json
import os
import uuid

import pytest
from sqlalchemy import delete, text

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
from src.services.lineage_execution_target_binding_service import (
    persist_lineage_execution_target_binding,
)
from src.services.lineage_invalidation_execution_claim_service import (
    claim_lineage_invalidation_execution,
)
from src.services.lineage_invalidation_execution_intent_service import (
    authorize_lineage_invalidation_execution,
)
from src.services.lineage_invalidation_executor_service import (
    LineageInvalidationExecutorAttemptNotFoundError,
    LineageInvalidationExecutorCausalMismatchError,
    LineageInvalidationExecutorUnsupportedTargetError,
    resolve_lineage_invalidation_execution_plan,
)
from src.services.lineage_pgvector_invalidation_service import (
    invalidate_lineage_pgvector_causal_chunk,
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


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


def _make_organization(db) -> Organization:
    suffix = uuid.uuid4().hex[:12]
    organization = Organization(
        id=uuid.uuid4(),
        name=f"lineage-invalidation-executor-{suffix}",
        slug=f"lineage-invalidation-executor-{suffix}",
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
    binding_revision: int,
    collection_name: str,
    document_id: str,
    provider: str = "pgvector",
    target_kind: str = "langchain_pgvector_causal_chunk",
) -> LineageExecutionTargetBinding:
    return LineageExecutionTargetBinding(
        identity=LineageExecutionTargetIdentity(
            source_external_id="source-a",
            asset_external_id="index-a",
        ),
        binding_revision=binding_revision,
        provider=provider,
        target_kind=target_kind,
        configuration=(
            {
                "connection_ref": "test-downstream",
                "collection_name": collection_name,
            }
            if target_kind == "langchain_pgvector_causal_chunk"
            else {
                "connection_ref": "test-downstream",
                "collection_name": collection_name,
                "document_id": document_id,
                "document_revision_external_id": "index-a-r7",
            }
        ),
    )


def _establish_claimable_intent(
    db,
    *,
    organization_id: uuid.UUID,
    collection_name: str,
    document_id: str,
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
            binding_revision=1,
            collection_name=collection_name,
            document_id=document_id,
        ),
    )

    return authorize_lineage_invalidation_execution(
        db,
        organization_id=organization_id,
        decision_record_id=persisted.record.id,
    ).record


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
                'executor-test',
                '{"chunk_revision_external_id":"chunk-a-r3",'
                '"source_external_id":"source-a"}'::jsonb
            )
            """
        ),
        {
            "document_id": document_id,
            "collection_id": collection_id,
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


def _cleanup(
    organization_ids: list[uuid.UUID],
    collection_ids: list[uuid.UUID],
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


def test_executor_invalidates_exact_pinned_pgvector_target() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []
    collection_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        collection_id = uuid.uuid4()
        collection_ids.append(collection_id)
        collection_name = f"sde-executor-{uuid.uuid4().hex}"
        document_id = "chunk-a"

        _insert_collection(
            db,
            collection_id=collection_id,
            collection_name=collection_name,
            organization_id=organization.id,
        )
        _insert_document(
            db,
            collection_id=collection_id,
            document_id=document_id,
        )

        intent = _establish_claimable_intent(
            db,
            organization_id=organization.id,
            collection_name=collection_name,
            document_id=document_id,
        )
        db.commit()

        claim = claim_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
        )
        attempt_id = claim.record.id
        db.commit()

        plan = resolve_lineage_invalidation_execution_plan(
            db,
            organization_id=organization.id,
            attempt_id=attempt_id,
        )
        result = invalidate_lineage_pgvector_causal_chunk(
            db,
            configuration=plan.target_configuration,
        )

        assert plan.attempt_id == attempt_id
        assert plan.binding_revision == 1
        assert plan.provider == "pgvector"
        assert plan.target_kind == "langchain_pgvector_causal_chunk"
        assert plan.connection_ref == "test-downstream"
        assert dict(plan.target_configuration) == {
            "collection_name": collection_name,
            "document_id": document_id,
            "document_revision_external_id": "chunk-a-r3",
            "source_external_id": "source-a",
            "organization_id": str(organization.id),
            "index_external_id": "index-a",
        }
        assert result.deleted is True
        assert not _document_exists(
            db,
            collection_name=collection_name,
            document_id=document_id,
        )
    finally:
        db.rollback()
        db.close()
        _cleanup(organization_ids, collection_ids)


def test_executor_uses_pinned_binding_after_new_revision_is_published() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []
    collection_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        old_collection_id = uuid.uuid4()
        new_collection_id = uuid.uuid4()
        collection_ids.extend(
            [
                old_collection_id,
                new_collection_id,
            ]
        )

        old_collection_name = f"sde-executor-old-{uuid.uuid4().hex}"
        new_collection_name = f"sde-executor-new-{uuid.uuid4().hex}"
        old_document_id = "chunk-a"
        new_document_id = "chunk-a-new-collection"

        _insert_collection(
            db,
            collection_id=old_collection_id,
            collection_name=old_collection_name,
            organization_id=organization.id,
        )
        _insert_document(
            db,
            collection_id=old_collection_id,
            document_id=old_document_id,
        )
        _insert_collection(
            db,
            collection_id=new_collection_id,
            collection_name=new_collection_name,
            organization_id=organization.id,
        )
        _insert_document(
            db,
            collection_id=new_collection_id,
            document_id=new_document_id,
        )

        intent = _establish_claimable_intent(
            db,
            organization_id=organization.id,
            collection_name=old_collection_name,
            document_id=old_document_id,
        )
        db.commit()

        claim = claim_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
        )
        attempt_id = claim.record.id

        assert claim.record.binding_revision == 1

        db.commit()

        persist_lineage_execution_target_binding(
            db,
            organization_id=organization.id,
            binding=_binding(
                binding_revision=2,
                collection_name=new_collection_name,
                document_id=new_document_id,
            ),
        )
        db.commit()

        plan = resolve_lineage_invalidation_execution_plan(
            db,
            organization_id=organization.id,
            attempt_id=attempt_id,
        )
        result = invalidate_lineage_pgvector_causal_chunk(
            db,
            configuration=plan.target_configuration,
        )

        assert plan.binding_revision == 1
        assert result.deleted is True
        assert not _document_exists(
            db,
            collection_name=old_collection_name,
            document_id=old_document_id,
        )
        assert _document_exists(
            db,
            collection_name=new_collection_name,
            document_id=new_document_id,
        )
    finally:
        db.rollback()
        db.close()
        _cleanup(organization_ids, collection_ids)


def test_executor_repeated_execution_is_idempotent() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []
    collection_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        collection_id = uuid.uuid4()
        collection_ids.append(collection_id)
        collection_name = f"sde-executor-{uuid.uuid4().hex}"
        document_id = "chunk-a"

        _insert_collection(
            db,
            collection_id=collection_id,
            collection_name=collection_name,
            organization_id=organization.id,
        )
        _insert_document(
            db,
            collection_id=collection_id,
            document_id=document_id,
        )

        intent = _establish_claimable_intent(
            db,
            organization_id=organization.id,
            collection_name=collection_name,
            document_id=document_id,
        )
        db.commit()

        claim = claim_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
        )
        attempt_id = claim.record.id
        db.commit()

        plan = resolve_lineage_invalidation_execution_plan(
            db,
            organization_id=organization.id,
            attempt_id=attempt_id,
        )
        first = invalidate_lineage_pgvector_causal_chunk(
            db,
            configuration=plan.target_configuration,
        )
        second = invalidate_lineage_pgvector_causal_chunk(
            db,
            configuration=plan.target_configuration,
        )

        assert first.deleted is True
        assert second.deleted is False
    finally:
        db.rollback()
        db.close()
        _cleanup(organization_ids, collection_ids)


def test_executor_is_tenant_scoped() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []
    collection_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        other_organization = _make_organization(db)
        organization_ids.extend(
            [
                organization.id,
                other_organization.id,
            ]
        )

        collection_id = uuid.uuid4()
        collection_ids.append(collection_id)
        collection_name = f"sde-executor-{uuid.uuid4().hex}"
        document_id = "chunk-a"

        _insert_collection(
            db,
            collection_id=collection_id,
            collection_name=collection_name,
            organization_id=organization.id,
        )
        _insert_document(
            db,
            collection_id=collection_id,
            document_id=document_id,
        )

        intent = _establish_claimable_intent(
            db,
            organization_id=organization.id,
            collection_name=collection_name,
            document_id=document_id,
        )
        db.commit()

        claim = claim_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
        )
        attempt_id = claim.record.id
        db.commit()

        with pytest.raises(
            LineageInvalidationExecutorAttemptNotFoundError
        ):
            resolve_lineage_invalidation_execution_plan(
                db,
                organization_id=other_organization.id,
                attempt_id=attempt_id,
            )

        assert _document_exists(
            db,
            collection_name=collection_name,
            document_id=document_id,
        )
    finally:
        db.rollback()
        db.close()
        _cleanup(organization_ids, collection_ids)


def test_executor_rejects_unsupported_pinned_target_before_delete() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []
    collection_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        collection_id = uuid.uuid4()
        collection_ids.append(collection_id)
        collection_name = f"sde-executor-{uuid.uuid4().hex}"
        document_id = "chunk-a"

        _insert_collection(
            db,
            collection_id=collection_id,
            collection_name=collection_name,
            organization_id=organization.id,
        )
        _insert_document(
            db,
            collection_id=collection_id,
            document_id=document_id,
        )

        persisted = persist_lineage_invalidation_decision(
            db,
            organization_id=organization.id,
            decision=_decision(),
        )
        activate_lineage_invalidation_policy(
            db,
            organization_id=organization.id,
            policy=_policy(7),
        )
        persist_lineage_observation(
            db,
            organization_id=organization.id,
            observation=_materialization(),
        )
        persist_lineage_execution_target_binding(
            db,
            organization_id=organization.id,
            binding=_binding(
                binding_revision=1,
                collection_name=collection_name,
                document_id=document_id,
                provider="unsupported-provider",
                target_kind="unsupported-kind",
            ),
        )
        intent = authorize_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            decision_record_id=persisted.record.id,
        ).record
        db.commit()

        claim = claim_lineage_invalidation_execution(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
        )
        attempt_id = claim.record.id
        db.commit()

        with pytest.raises(
            LineageInvalidationExecutorUnsupportedTargetError
        ):
            resolve_lineage_invalidation_execution_plan(
                db,
                organization_id=organization.id,
                attempt_id=attempt_id,
            )

        assert _document_exists(
            db,
            collection_name=collection_name,
            document_id=document_id,
        )
    finally:
        db.rollback()
        db.close()
        _cleanup(organization_ids, collection_ids)


def test_executor_rejects_historical_document_scoped_attempt() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []
    collection_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        collection_id = uuid.uuid4()
        collection_ids.append(collection_id)
        collection_name = f"sde-executor-legacy-{uuid.uuid4().hex}"
        document_id = "chunk-a"

        _insert_collection(
            db,
            collection_id=collection_id,
            collection_name=collection_name,
            organization_id=organization.id,
        )
        _insert_document(
            db,
            collection_id=collection_id,
            document_id=document_id,
        )

        intent = _establish_claimable_intent(
            db,
            organization_id=organization.id,
            collection_name=collection_name,
            document_id=document_id,
        )

        persist_lineage_execution_target_binding(
            db,
            organization_id=organization.id,
            binding=_binding(
                binding_revision=2,
                collection_name=collection_name,
                document_id=document_id,
                target_kind="langchain_pgvector_document",
            ),
        )
        db.commit()

        from sqlalchemy import func, select
        from datetime import timedelta

        now = db.execute(select(func.clock_timestamp())).scalar_one()

        legacy_attempt = LineageInvalidationExecutionAttemptRecord(
            organization_id=organization.id,
            intent_id=intent.id,
            attempt_number=1,
            binding_revision=2,
            claimed_at=now,
            lease_expires_at=now + timedelta(seconds=300),
        )
        db.add(legacy_attempt)
        db.commit()

        with pytest.raises(
            LineageInvalidationExecutorUnsupportedTargetError
        ):
            resolve_lineage_invalidation_execution_plan(
                db,
                organization_id=organization.id,
                attempt_id=legacy_attempt.id,
            )

        assert _document_exists(
            db,
            collection_name=collection_name,
            document_id=document_id,
        )
    finally:
        db.rollback()
        db.close()
        _cleanup(organization_ids, collection_ids)


def test_executor_caller_rollback_restores_invalidated_document() -> None:
    setup_db = SessionLocal()
    execution_db = SessionLocal()
    verify_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []
    collection_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(setup_db)
        organization_ids.append(organization.id)

        collection_id = uuid.uuid4()
        collection_ids.append(collection_id)
        collection_name = f"sde-executor-{uuid.uuid4().hex}"
        document_id = "chunk-a"

        _insert_collection(
            setup_db,
            collection_id=collection_id,
            collection_name=collection_name,
            organization_id=organization.id,
        )
        _insert_document(
            setup_db,
            collection_id=collection_id,
            document_id=document_id,
        )

        intent = _establish_claimable_intent(
            setup_db,
            organization_id=organization.id,
            collection_name=collection_name,
            document_id=document_id,
        )
        setup_db.commit()

        claim = claim_lineage_invalidation_execution(
            setup_db,
            organization_id=organization.id,
            intent_id=intent.id,
        )
        attempt_id = claim.record.id
        setup_db.commit()

        plan = resolve_lineage_invalidation_execution_plan(
            setup_db,
            organization_id=organization.id,
            attempt_id=attempt_id,
        )
        result = invalidate_lineage_pgvector_causal_chunk(
            execution_db,
            configuration=plan.target_configuration,
        )

        assert result.deleted is True

        execution_db.rollback()

        assert _document_exists(
            verify_db,
            collection_name=collection_name,
            document_id=document_id,
        )
    finally:
        execution_db.rollback()
        verify_db.close()
        execution_db.close()
        setup_db.close()
        _cleanup(organization_ids, collection_ids)


def test_historical_pinned_binding_outside_causal_path_cannot_resolve() -> None:
    from datetime import timedelta
    from sqlalchemy import func, select

    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []
    collection_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        intent = _establish_claimable_intent(
            db,
            organization_id=organization.id,
            collection_name=f"historical-{uuid.uuid4().hex}",
            document_id=f"document-{uuid.uuid4().hex}",
        )
        persist_lineage_execution_target_binding(
            db,
            organization_id=organization.id,
            binding=LineageExecutionTargetBinding(
                identity=LineageExecutionTargetIdentity(
                    source_external_id="source-a",
                    asset_external_id="index-a",
                ),
                binding_revision=2,
                provider="pgvector",
                target_kind="langchain_pgvector_document_revision",
                configuration={
                    "connection_ref": "test-downstream",
                    "collection_name": "rag-index",
                    "document_id": "unrelated-document",
                    "document_revision_external_id": "unrelated-r1",
                },
            ),
        )
        now = db.execute(select(func.clock_timestamp())).scalar_one()
        historical_attempt = LineageInvalidationExecutionAttemptRecord(
            organization_id=organization.id,
            intent_id=intent.id,
            attempt_number=1,
            binding_revision=2,
            claimed_at=now,
            lease_expires_at=now + timedelta(seconds=300),
        )
        db.add(historical_attempt)
        db.commit()

        with pytest.raises(LineageInvalidationExecutorUnsupportedTargetError):
            resolve_lineage_invalidation_execution_plan(
                db,
                organization_id=organization.id,
                attempt_id=historical_attempt.id,
            )
    finally:
        db.close()
        _cleanup(organization_ids, collection_ids)
