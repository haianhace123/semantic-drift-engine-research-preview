from __future__ import annotations

import os
import threading
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import delete, func

from src.core.database import SessionLocal
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
    LineageInvalidationExecutionVerificationConflictError,
    LineageInvalidationExecutionVerificationObservationInvalidError,
    LineageInvalidationExecutionVerificationObservationNotFoundError,
    LineageInvalidationExecutionVerificationResultNotFoundError,
    LineageInvalidationExecutionVerificationResultNotSuccessfulError,
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
from src.services.lineage_invalidation_execution_verification_projection_service import (
    load_lineage_invalidation_execution_verification_view,
)
from src.services.lineage_target_integrity_service import (
    assess_lineage_target_integrity,
)
from src.services.lineage_invalidation_projection_service import (
    load_lineage_invalidation_decision_by_record_id,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


def _make_organization(db) -> Organization:
    suffix = uuid.uuid4().hex[:12]
    organization = Organization(
        id=uuid.uuid4(),
        name=f"lineage-execution-verification-{suffix}",
        slug=f"lineage-execution-verification-{suffix}",
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


def _policy() -> LineageInvalidationPolicyIdentity:
    return LineageInvalidationPolicyIdentity(
        policy_external_id="rag-invalidation",
        revision=7,
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
    asset_external_id: str = "index-a",
    revision_external_id: str = "index-a-r7",
    sequence: int = 7,
    source_external_id: str = "source-a",
) -> LineageObservation:
    return LineageObservation(
        observation_kind="materialization",
        source_external_id=source_external_id,
        snapshot_external_id=f"snapshot-{sequence}",
        asset_external_id=asset_external_id,
        asset_kind="index",
        revision_external_id=revision_external_id,
        sequence=sequence,
        dependencies=(
            LineageRevisionReference(
                asset_external_id="chunk-a",
                revision_external_id="chunk-a-r3",
                dependency_type="indexed_from",
            ),
        ),
    )


def _consumption(
    *,
    sequence: int = 8,
) -> LineageObservation:
    return LineageObservation(
        observation_kind="consumption",
        source_external_id="source-a",
        snapshot_external_id=f"snapshot-consumption-{sequence}",
        asset_external_id="index-a",
        asset_kind="index",
        revision_external_id="index-a-r8",
        sequence=sequence,
        dependencies=(
            LineageRevisionReference(
                asset_external_id="chunk-a",
                revision_external_id="chunk-a-r4",
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


def _execution_target_binding() -> LineageExecutionTargetBinding:
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


def _establish_execution_result(
    db,
    *,
    organization_id: uuid.UUID,
    outcome: str = "succeeded",
) -> LineageInvalidationExecutionResultRecord:
    persisted_decision = persist_lineage_invalidation_decision(
        db,
        organization_id=organization_id,
        decision=_decision(),
    )

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
        binding=_execution_target_binding(),
    )

    intent = authorize_lineage_invalidation_execution(
        db,
        organization_id=organization_id,
        decision_record_id=persisted_decision.record.id,
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
        outcome=outcome,  # type: ignore[arg-type]
    )

    db.commit()

    return result.record


def _persist_verification_observation(
    db,
    *,
    organization_id: uuid.UUID,
    asset_external_id: str = "index-a",
    revision_external_id: str = "index-a-r8",
    sequence: int = 8,
    source_external_id: str = "source-a",
) -> LineageObservationRecord:
    persisted = persist_lineage_observation(
        db,
        organization_id=organization_id,
        observation=_materialization(
            asset_external_id=asset_external_id,
            revision_external_id=revision_external_id,
            sequence=sequence,
            source_external_id=source_external_id,
        ),
    )
    db.commit()
    return persisted.record


def _verification_count(
    db,
    *,
    organization_id: uuid.UUID,
    execution_result_id: uuid.UUID,
) -> int:
    return (
        db.query(
            func.count(
                LineageInvalidationExecutionVerificationRecord.id
            )
        )
        .filter(
            LineageInvalidationExecutionVerificationRecord.organization_id
            == organization_id,
            LineageInvalidationExecutionVerificationRecord.execution_result_id
            == execution_result_id,
        )
        .scalar()
    )


def _cleanup(
    organization_ids: list[uuid.UUID],
) -> None:
    if not organization_ids:
        return

    db = SessionLocal()

    try:
        db.rollback()

        db.execute(
            delete(
                LineageInvalidationExecutionVerificationRecord
            ).where(
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
            delete(Organization).where(
                Organization.id.in_(organization_ids)
            )
        )
        db.commit()
    finally:
        db.close()


def _load_decision_for_execution_result(
    db,
    *,
    organization_id: uuid.UUID,
    execution_result_id: uuid.UUID,
):
    result = (
        db.query(LineageInvalidationExecutionResultRecord)
        .filter(
            LineageInvalidationExecutionResultRecord.organization_id
            == organization_id,
            LineageInvalidationExecutionResultRecord.id
            == execution_result_id,
        )
        .one()
    )

    attempt = (
        db.query(LineageInvalidationExecutionAttemptRecord)
        .filter(
            LineageInvalidationExecutionAttemptRecord.organization_id
            == organization_id,
            LineageInvalidationExecutionAttemptRecord.id
            == result.attempt_id,
        )
        .one()
    )

    intent = (
        db.query(LineageInvalidationExecutionIntentRecord)
        .filter(
            LineageInvalidationExecutionIntentRecord.organization_id
            == organization_id,
            LineageInvalidationExecutionIntentRecord.id
            == attempt.intent_id,
        )
        .one()
    )

    decision = load_lineage_invalidation_decision_by_record_id(
        db,
        organization_id=organization_id,
        record_id=intent.decision_id,
    )

    assert decision is not None
    return decision


def test_successful_result_can_bind_materialization_evidence() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        execution_result = _establish_execution_result(
            db,
            organization_id=organization.id,
        )
        observation = _persist_verification_observation(
            db,
            organization_id=organization.id,
        )

        persisted = persist_lineage_invalidation_execution_verification(
            db,
            organization_id=organization.id,
            execution_result_id=execution_result.id,
            observation_id=observation.id,
        )

        assert persisted.created is True
        assert persisted.record.organization_id == organization.id
        assert persisted.record.execution_result_id == execution_result.id
        assert persisted.record.observation_id == observation.id
        assert _verification_count(
            db,
            organization_id=organization.id,
            execution_result_id=execution_result.id,
        ) == 1
    finally:
        db.close()
        _cleanup(organization_ids)


def test_same_verification_binding_is_idempotent() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        execution_result = _establish_execution_result(
            db,
            organization_id=organization.id,
        )
        observation = _persist_verification_observation(
            db,
            organization_id=organization.id,
        )

        first = persist_lineage_invalidation_execution_verification(
            db,
            organization_id=organization.id,
            execution_result_id=execution_result.id,
            observation_id=observation.id,
        )
        db.commit()

        second = persist_lineage_invalidation_execution_verification(
            db,
            organization_id=organization.id,
            execution_result_id=execution_result.id,
            observation_id=observation.id,
        )

        assert first.record.id == second.record.id
        assert second.created is False
        assert _verification_count(
            db,
            organization_id=organization.id,
            execution_result_id=execution_result.id,
        ) == 1
    finally:
        db.close()
        _cleanup(organization_ids)


def test_conflicting_verification_binding_is_rejected() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        execution_result = _establish_execution_result(
            db,
            organization_id=organization.id,
        )
        first_observation = _persist_verification_observation(
            db,
            organization_id=organization.id,
            sequence=8,
        )
        persist_lineage_invalidation_execution_verification(
            db,
            organization_id=organization.id,
            execution_result_id=execution_result.id,
            observation_id=first_observation.id,
        )
        db.commit()
        second_observation = _persist_verification_observation(
            db,
            organization_id=organization.id,
            revision_external_id="index-a-r9",
            sequence=9,
        )

        with pytest.raises(
            LineageInvalidationExecutionVerificationConflictError
        ) as exc_info:
            persist_lineage_invalidation_execution_verification(
                db,
                organization_id=organization.id,
                execution_result_id=execution_result.id,
                observation_id=second_observation.id,
            )

        assert (
            exc_info.value.existing_observation_id
            == first_observation.id
        )
        assert (
            exc_info.value.requested_observation_id
            == second_observation.id
        )
        assert _verification_count(
            db,
            organization_id=organization.id,
            execution_result_id=execution_result.id,
        ) == 1
    finally:
        db.close()
        _cleanup(organization_ids)


def test_failed_result_cannot_bind_verification_evidence() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        execution_result = _establish_execution_result(
            db,
            organization_id=organization.id,
            outcome="failed",
        )
        observation = _persist_verification_observation(
            db,
            organization_id=organization.id,
        )

        with pytest.raises(
            LineageInvalidationExecutionVerificationResultNotSuccessfulError
        ):
            persist_lineage_invalidation_execution_verification(
                db,
                organization_id=organization.id,
                execution_result_id=execution_result.id,
                observation_id=observation.id,
            )

        assert _verification_count(
            db,
            organization_id=organization.id,
            execution_result_id=execution_result.id,
        ) == 0
    finally:
        db.close()
        _cleanup(organization_ids)


def test_consumption_observation_cannot_verify_execution() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        execution_result = _establish_execution_result(
            db,
            organization_id=organization.id,
        )

        observation = persist_lineage_observation(
            db,
            organization_id=organization.id,
            observation=_consumption(),
        )
        db.commit()

        with pytest.raises(
            LineageInvalidationExecutionVerificationObservationInvalidError
        ):
            persist_lineage_invalidation_execution_verification(
                db,
                organization_id=organization.id,
                execution_result_id=execution_result.id,
                observation_id=observation.record.id,
            )
    finally:
        db.close()
        _cleanup(organization_ids)


@pytest.mark.parametrize(
    (
        "source_external_id",
        "asset_external_id",
    ),
    (
        ("source-b", "index-a"),
        ("source-a", "index-b"),
    ),
)
def test_wrong_target_observation_cannot_verify_execution(
    source_external_id: str,
    asset_external_id: str,
) -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        execution_result = _establish_execution_result(
            db,
            organization_id=organization.id,
        )
        observation = _persist_verification_observation(
            db,
            organization_id=organization.id,
            source_external_id=source_external_id,
            asset_external_id=asset_external_id,
        )

        with pytest.raises(
            LineageInvalidationExecutionVerificationObservationInvalidError
        ):
            persist_lineage_invalidation_execution_verification(
                db,
                organization_id=organization.id,
                execution_result_id=execution_result.id,
                observation_id=observation.id,
            )
    finally:
        db.close()
        _cleanup(organization_ids)


def test_result_lookup_is_tenant_scoped() -> None:
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

        execution_result = _establish_execution_result(
            db,
            organization_id=owner.id,
        )
        observation = _persist_verification_observation(
            db,
            organization_id=other.id,
        )

        with pytest.raises(
            LineageInvalidationExecutionVerificationResultNotFoundError
        ):
            persist_lineage_invalidation_execution_verification(
                db,
                organization_id=other.id,
                execution_result_id=execution_result.id,
                observation_id=observation.id,
            )
    finally:
        db.close()
        _cleanup(organization_ids)


def test_observation_lookup_is_tenant_scoped() -> None:
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

        execution_result = _establish_execution_result(
            db,
            organization_id=owner.id,
        )
        observation = _persist_verification_observation(
            db,
            organization_id=other.id,
        )

        with pytest.raises(
            LineageInvalidationExecutionVerificationObservationNotFoundError
        ):
            persist_lineage_invalidation_execution_verification(
                db,
                organization_id=owner.id,
                execution_result_id=execution_result.id,
                observation_id=observation.id,
            )
    finally:
        db.close()
        _cleanup(organization_ids)


def test_caller_rollback_removes_created_verification() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        execution_result = _establish_execution_result(
            db,
            organization_id=organization.id,
        )
        observation = _persist_verification_observation(
            db,
            organization_id=organization.id,
        )

        persisted = persist_lineage_invalidation_execution_verification(
            db,
            organization_id=organization.id,
            execution_result_id=execution_result.id,
            observation_id=observation.id,
        )

        assert persisted.created is True
        assert _verification_count(
            db,
            organization_id=organization.id,
            execution_result_id=execution_result.id,
        ) == 1

        db.rollback()

        assert _verification_count(
            db,
            organization_id=organization.id,
            execution_result_id=execution_result.id,
        ) == 0
    finally:
        db.close()
        _cleanup(organization_ids)


def test_verification_service_does_not_commit_caller_transaction() -> None:
    db = SessionLocal()
    observer = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        execution_result = _establish_execution_result(
            db,
            organization_id=organization.id,
        )
        observation = _persist_verification_observation(
            db,
            organization_id=organization.id,
        )

        persisted = persist_lineage_invalidation_execution_verification(
            db,
            organization_id=organization.id,
            execution_result_id=execution_result.id,
            observation_id=observation.id,
        )

        assert persisted.created is True
        assert _verification_count(
            observer,
            organization_id=organization.id,
            execution_result_id=execution_result.id,
        ) == 0

        db.commit()
        observer.rollback()

        assert _verification_count(
            observer,
            organization_id=organization.id,
            execution_result_id=execution_result.id,
        ) == 1
    finally:
        observer.close()
        db.close()
        _cleanup(organization_ids)


def test_concurrent_same_verification_binding_is_idempotent() -> None:
    setup_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(setup_db)
        organization_ids.append(organization.id)

        execution_result = _establish_execution_result(
            setup_db,
            organization_id=organization.id,
        )
        observation = _persist_verification_observation(
            setup_db,
            organization_id=organization.id,
        )

        organization_id = organization.id
        execution_result_id = execution_result.id
        observation_id = observation.id

        start = threading.Barrier(2)
        lock = threading.Lock()
        records: list[tuple[uuid.UUID, bool]] = []
        errors: list[BaseException] = []

        def persist_binding() -> None:
            db = SessionLocal()

            try:
                start.wait(timeout=10)

                persisted = (
                    persist_lineage_invalidation_execution_verification(
                        db,
                        organization_id=organization_id,
                        execution_result_id=execution_result_id,
                        observation_id=observation_id,
                    )
                )
                db.commit()

                with lock:
                    records.append(
                        (
                            persisted.record.id,
                            persisted.created,
                        )
                    )
            except BaseException as exc:
                db.rollback()

                with lock:
                    errors.append(exc)
            finally:
                db.close()

        first_thread = threading.Thread(target=persist_binding)
        second_thread = threading.Thread(target=persist_binding)

        first_thread.start()
        second_thread.start()

        first_thread.join(timeout=10)
        second_thread.join(timeout=10)

        assert not first_thread.is_alive()
        assert not second_thread.is_alive()
        assert errors == []
        assert len(records) == 2
        assert len({record_id for record_id, _ in records}) == 1
        assert sorted(created for _, created in records) == [False, True]

        verify_db = SessionLocal()

        try:
            assert _verification_count(
                verify_db,
                organization_id=organization_id,
                execution_result_id=execution_result_id,
            ) == 1
        finally:
            verify_db.close()
    finally:
        setup_db.close()
        _cleanup(organization_ids)


def test_successful_execution_without_bound_evidence_is_awaiting_evidence(
) -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        execution_result = _establish_execution_result(
            db,
            organization_id=organization.id,
        )

        decision = _load_decision_for_execution_result(
            db,
            organization_id=organization.id,
            execution_result_id=execution_result.id,
        )

        view = load_lineage_invalidation_execution_verification_view(
            db,
            organization_id=organization.id,
            decision=decision,
            execution_result_id=execution_result.id,
        )

        assert view.state == "awaiting_evidence"
        assert view.execution_result_id == execution_result.id
        assert view.verification_record_id is None
        assert view.observation_id is None
        assert view.stale_dependency == decision.decision.causal_path[-2]
    finally:
        db.close()
        _cleanup(organization_ids)


def test_bound_evidence_with_stale_direct_dependency_is_not_verified(
) -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        execution_result = _establish_execution_result(
            db,
            organization_id=organization.id,
        )

        observation = persist_lineage_observation(
            db,
            organization_id=organization.id,
            observation=LineageObservation(
                observation_kind="materialization",
                source_external_id="source-a",
                snapshot_external_id="snapshot-8",
                asset_external_id="index-a",
                asset_kind="index",
                revision_external_id="index-a-r8",
                sequence=8,
                dependencies=(
                    LineageRevisionReference(
                        asset_external_id="chunk-a",
                        revision_external_id="chunk-a-r3",
                    ),
                ),
            ),
        )
        db.commit()

        persist_lineage_invalidation_execution_verification(
            db,
            organization_id=organization.id,
            execution_result_id=execution_result.id,
            observation_id=observation.record.id,
        )
        db.commit()

        decision = _load_decision_for_execution_result(
            db,
            organization_id=organization.id,
            execution_result_id=execution_result.id,
        )

        view = load_lineage_invalidation_execution_verification_view(
            db,
            organization_id=organization.id,
            decision=decision,
            execution_result_id=execution_result.id,
        )

        assert view.state == "stale_dependency_present"
        assert view.verification_record_id is not None
        assert view.observation_id == observation.record.id
        assert view.stale_dependency == decision.decision.causal_path[-2]
    finally:
        db.close()
        _cleanup(organization_ids)


def test_bound_evidence_without_stale_direct_dependency_is_verified(
) -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        execution_result = _establish_execution_result(
            db,
            organization_id=organization.id,
        )

        observation = persist_lineage_observation(
            db,
            organization_id=organization.id,
            observation=LineageObservation(
                observation_kind="materialization",
                source_external_id="source-a",
                snapshot_external_id="snapshot-8",
                asset_external_id="index-a",
                asset_kind="index",
                revision_external_id="index-a-r8",
                sequence=8,
                dependencies=(
                    LineageRevisionReference(
                        asset_external_id="chunk-a",
                        revision_external_id="chunk-a-r4",
                    ),
                ),
            ),
        )
        db.commit()

        persist_lineage_invalidation_execution_verification(
            db,
            organization_id=organization.id,
            execution_result_id=execution_result.id,
            observation_id=observation.record.id,
        )
        db.commit()

        decision = _load_decision_for_execution_result(
            db,
            organization_id=organization.id,
            execution_result_id=execution_result.id,
        )

        view = load_lineage_invalidation_execution_verification_view(
            db,
            organization_id=organization.id,
            decision=decision,
            execution_result_id=execution_result.id,
        )

        assert view.state == "verified"
        assert view.verification_record_id is not None
        assert view.observation_id == observation.record.id
        assert view.stale_dependency == decision.decision.causal_path[-2]
    finally:
        db.close()
        _cleanup(organization_ids)


def test_pre_execution_observation_cannot_be_bound() -> None:
    db = SessionLocal()
    organizations: list[uuid.UUID] = []
    try:
        org = _make_organization(db)
        organizations.append(org.id)
        result = _establish_execution_result(db, organization_id=org.id)
        observation = _persist_verification_observation(
            db, organization_id=org.id,
        )
        # Simulate an observation ingested before the result. DB timestamps
        # are trusted ingestion metadata, not producer-controlled timestamps.
        observation.created_at = result.recorded_at - timedelta(minutes=1)
        db.commit()

        with pytest.raises(
            LineageInvalidationExecutionVerificationObservationInvalidError
        ):
            persist_lineage_invalidation_execution_verification(
                db, organization_id=org.id,
                execution_result_id=result.id, observation_id=observation.id,
            )
        assert _verification_count(
            db, organization_id=org.id, execution_result_id=result.id,
        ) == 0
    finally:
        db.close()
        _cleanup(organizations)


def test_same_revision_and_superseded_materialization_are_rejected() -> None:
    db = SessionLocal()
    organizations: list[uuid.UUID] = []
    try:
        org = _make_organization(db)
        organizations.append(org.id)
        result = _establish_execution_result(db, organization_id=org.id)
        same_revision = _persist_verification_observation(
            db, organization_id=org.id, sequence=8,
            revision_external_id="index-a-r7",
        )
        with pytest.raises(
            LineageInvalidationExecutionVerificationObservationInvalidError
        ):
            persist_lineage_invalidation_execution_verification(
                db, organization_id=org.id,
                execution_result_id=result.id, observation_id=same_revision.id,
            )
        older = _persist_verification_observation(
            db, organization_id=org.id, sequence=9,
            revision_external_id="index-a-r9",
        )
        _persist_verification_observation(
            db, organization_id=org.id, sequence=10,
            revision_external_id="index-a-r10",
        )
        with pytest.raises(
            LineageInvalidationExecutionVerificationObservationInvalidError
        ):
            persist_lineage_invalidation_execution_verification(
                db, organization_id=org.id,
                execution_result_id=result.id, observation_id=older.id,
            )
        assert _verification_count(
            db, organization_id=org.id, execution_result_id=result.id,
        ) == 0
    finally:
        db.close()
        _cleanup(organizations)


def test_superseded_bound_evidence_no_longer_verifies_target_integrity() -> None:
    db = SessionLocal()
    organizations: list[uuid.UUID] = []
    try:
        org = _make_organization(db)
        organizations.append(org.id)
        result = _establish_execution_result(db, organization_id=org.id)
        repaired = persist_lineage_observation(
            db, organization_id=org.id,
            observation=LineageObservation(
                observation_kind="materialization",
                source_external_id="source-a", snapshot_external_id="repair-8",
                asset_external_id="index-a", asset_kind="index",
                revision_external_id="index-a-r8", sequence=8,
                dependencies=(),
            ),
        ).record
        db.commit()
        persist_lineage_invalidation_execution_verification(
            db, organization_id=org.id,
            execution_result_id=result.id, observation_id=repaired.id,
        )
        db.commit()
        decision = _load_decision_for_execution_result(
            db, organization_id=org.id, execution_result_id=result.id,
        )
        assert load_lineage_invalidation_execution_verification_view(
            db, organization_id=org.id, decision=decision,
            execution_result_id=result.id,
        ).state == "verified"
        assert assess_lineage_target_integrity(
            db, organization_id=org.id, target=decision.decision.target,
        ).status == "verified"

        # A later current materialization reinstates the stale dependency.
        _persist_verification_observation(
            db, organization_id=org.id, sequence=9,
            revision_external_id="index-a-r9",
        )
        view = load_lineage_invalidation_execution_verification_view(
            db, organization_id=org.id, decision=decision,
            execution_result_id=result.id,
        )
        assert view.state == "awaiting_evidence"
        assert view.observation_id == repaired.id
        assert assess_lineage_target_integrity(
            db, organization_id=org.id, target=decision.decision.target,
        ).status == "affected"
    finally:
        db.close()
        _cleanup(organizations)


def test_previously_bound_pre_execution_evidence_fails_closed_on_read() -> None:
    db = SessionLocal()
    organizations: list[uuid.UUID] = []
    try:
        org = _make_organization(db)
        organizations.append(org.id)
        result = _establish_execution_result(db, organization_id=org.id)
        repaired = persist_lineage_observation(
            db, organization_id=org.id,
            observation=LineageObservation(
                observation_kind="materialization",
                source_external_id="source-a", snapshot_external_id="repair-8",
                asset_external_id="index-a", asset_kind="index",
                revision_external_id="index-a-r8", sequence=8,
                dependencies=(),
            ),
        ).record
        repaired.created_at = result.recorded_at - timedelta(minutes=1)
        db.commit()
        # Historical bindings remain immutable, but may be unqualified.
        db.add(LineageInvalidationExecutionVerificationRecord(
            organization_id=org.id, execution_result_id=result.id,
            observation_id=repaired.id,
        ))
        db.commit()
        decision = _load_decision_for_execution_result(
            db, organization_id=org.id, execution_result_id=result.id,
        )
        view = load_lineage_invalidation_execution_verification_view(
            db, organization_id=org.id, decision=decision,
            execution_result_id=result.id,
        )
        assert view.state == "awaiting_evidence"
        assert view.observation_id == repaired.id
        assert assess_lineage_target_integrity(
            db, organization_id=org.id, target=decision.decision.target,
        ).status == "affected"
    finally:
        db.close()
        _cleanup(organizations)
