from __future__ import annotations

from datetime import timedelta
import os
import uuid

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
from src.models.lineage_invalidation_policy_activation import (
    LineageInvalidationPolicyActivationRecord,
)
from src.models.lineage_observation import LineageObservationRecord
from src.models.organization import Organization
from src.services.lineage_execution_target_binding_service import (
    persist_lineage_execution_target_binding,
)
from src.services.lineage_invalidation_control_plane_projection_service import (
    load_lineage_invalidation_control_plane_view,
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
        name=f"lineage-control-plane-projection-{suffix}",
        slug=f"lineage-control-plane-projection-{suffix}",
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


def _policy(revision: int = 7) -> LineageInvalidationPolicyIdentity:
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
        policy=_policy(),
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
    binding_revision: int = 1,
) -> LineageExecutionTargetBinding:
    return LineageExecutionTargetBinding(
        identity=LineageExecutionTargetIdentity(
            source_external_id="source-a",
            asset_external_id="index-a",
        ),
        binding_revision=binding_revision,
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
):
    return persist_lineage_invalidation_decision(
        db,
        organization_id=organization_id,
        decision=_decision(),
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


def _authorize(
    db,
    *,
    organization_id: uuid.UUID,
    decision_record_id: uuid.UUID,
) -> LineageInvalidationExecutionIntentRecord:
    return authorize_lineage_invalidation_execution(
        db,
        organization_id=organization_id,
        decision_record_id=decision_record_id,
    ).record


def _claim(
    db,
    *,
    organization_id: uuid.UUID,
    intent_id: uuid.UUID,
) -> LineageInvalidationExecutionAttemptRecord:
    return claim_lineage_invalidation_execution(
        db,
        organization_id=organization_id,
        intent_id=intent_id,
        lease_seconds=300,
    ).record


def _cleanup(organization_ids: list[uuid.UUID]) -> None:
    if not organization_ids:
        return

    db = SessionLocal()

    try:
        db.rollback()
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


def test_decision_without_execution_projects_durable_decision_only() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        persisted = _persist_decision(
            db,
            organization_id=organization.id,
        )
        db.commit()

        view = load_lineage_invalidation_control_plane_view(
            db,
            organization_id=organization.id,
            decision_record_id=persisted.record.id,
        )

        assert view is not None
        assert view.decision.record_id == persisted.record.id
        assert view.decision.decision == _decision()
        assert view.eligibility.eligible is False
        assert view.eligibility.reason_code == "policy_not_active"
        assert view.execution.intent is None
        assert view.execution.latest_attempt is None
        assert view.execution.result is None
    finally:
        db.close()
        _cleanup(organization_ids)


def test_authorized_execution_projects_intent_without_attempt() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        persisted = _persist_decision(
            db,
            organization_id=organization.id,
        )
        _establish_execution_authority(
            db,
            organization_id=organization.id,
        )
        db.commit()

        intent = _authorize(
            db,
            organization_id=organization.id,
            decision_record_id=persisted.record.id,
        )
        db.commit()

        view = load_lineage_invalidation_control_plane_view(
            db,
            organization_id=organization.id,
            decision_record_id=persisted.record.id,
        )

        assert view is not None
        assert view.eligibility.eligible is True
        assert view.eligibility.reason_code == "eligible"
        assert view.execution.intent is not None
        assert view.execution.intent.record_id == intent.id
        assert view.execution.intent.authorized_at == intent.authorized_at
        assert view.execution.latest_attempt is None
        assert view.execution.result is None
    finally:
        db.close()
        _cleanup(organization_ids)


def test_claimed_execution_projects_latest_attempt_without_result() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        persisted = _persist_decision(
            db,
            organization_id=organization.id,
        )
        _establish_execution_authority(
            db,
            organization_id=organization.id,
        )
        db.commit()

        intent = _authorize(
            db,
            organization_id=organization.id,
            decision_record_id=persisted.record.id,
        )
        db.commit()

        attempt = _claim(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
        )
        db.commit()

        view = load_lineage_invalidation_control_plane_view(
            db,
            organization_id=organization.id,
            decision_record_id=persisted.record.id,
        )

        assert view is not None
        assert view.execution.intent is not None
        assert view.execution.latest_attempt is not None
        assert view.execution.latest_attempt.record_id == attempt.id
        assert view.execution.latest_attempt.attempt_number == 1
        assert view.execution.latest_attempt.binding_revision == 1
        assert view.execution.latest_attempt.claimed_at == attempt.claimed_at
        assert (
            view.execution.latest_attempt.lease_expires_at
            == attempt.lease_expires_at
        )
        assert view.execution.result is None
    finally:
        db.close()
        _cleanup(organization_ids)


def test_terminal_execution_projects_succeeded_result() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        persisted = _persist_decision(
            db,
            organization_id=organization.id,
        )
        _establish_execution_authority(
            db,
            organization_id=organization.id,
        )
        db.commit()

        intent = _authorize(
            db,
            organization_id=organization.id,
            decision_record_id=persisted.record.id,
        )
        db.commit()

        attempt = _claim(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
        )
        db.commit()

        result = persist_lineage_invalidation_execution_result(
            db,
            organization_id=organization.id,
            attempt_id=attempt.id,
            outcome="succeeded",
        ).record
        db.commit()

        view = load_lineage_invalidation_control_plane_view(
            db,
            organization_id=organization.id,
            decision_record_id=persisted.record.id,
        )

        assert view is not None
        assert view.execution.latest_attempt is not None
        assert view.execution.latest_attempt.record_id == attempt.id
        assert view.execution.result is not None
        assert view.execution.result.record_id == result.id
        assert view.execution.result.attempt_id == attempt.id
        assert view.execution.result.outcome == "succeeded"
        assert view.execution.result.recorded_at == result.recorded_at
    finally:
        db.close()
        _cleanup(organization_ids)


def test_projection_selects_latest_attempt() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        persisted = _persist_decision(
            db,
            organization_id=organization.id,
        )
        _establish_execution_authority(
            db,
            organization_id=organization.id,
        )
        db.commit()

        intent = _authorize(
            db,
            organization_id=organization.id,
            decision_record_id=persisted.record.id,
        )
        db.commit()

        first_attempt = _claim(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
        )
        db.commit()

        db.execute(
            LineageInvalidationExecutionAttemptRecord.__table__.update()
            .where(
                LineageInvalidationExecutionAttemptRecord.id
                == first_attempt.id
            )
            .values(
                claimed_at=func.clock_timestamp() - timedelta(seconds=600),
                lease_expires_at=(
                    func.clock_timestamp() - timedelta(seconds=300)
                ),
            )
        )
        db.commit()

        second_attempt = _claim(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
        )
        db.commit()

        view = load_lineage_invalidation_control_plane_view(
            db,
            organization_id=organization.id,
            decision_record_id=persisted.record.id,
        )

        assert view is not None
        assert view.execution.latest_attempt is not None
        assert view.execution.latest_attempt.record_id == second_attempt.id
        assert view.execution.latest_attempt.attempt_number == 2
        assert view.execution.result is None
    finally:
        db.close()
        _cleanup(organization_ids)


def test_current_eligibility_can_change_after_terminal_execution() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        persisted = _persist_decision(
            db,
            organization_id=organization.id,
        )
        _establish_execution_authority(
            db,
            organization_id=organization.id,
        )
        db.commit()

        intent = _authorize(
            db,
            organization_id=organization.id,
            decision_record_id=persisted.record.id,
        )
        db.commit()

        attempt = _claim(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
        )
        db.commit()

        result = persist_lineage_invalidation_execution_result(
            db,
            organization_id=organization.id,
            attempt_id=attempt.id,
            outcome="succeeded",
        ).record
        db.commit()

        before = load_lineage_invalidation_control_plane_view(
            db,
            organization_id=organization.id,
            decision_record_id=persisted.record.id,
        )

        assert before is not None
        assert before.eligibility.eligible is True
        assert before.eligibility.reason_code == "eligible"
        assert before.execution.result is not None
        assert before.execution.result.record_id == result.id
        assert before.execution.result.outcome == "succeeded"

        persist_lineage_observation(
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
                dependencies=(),
            ),
        )
        db.commit()

        after = load_lineage_invalidation_control_plane_view(
            db,
            organization_id=organization.id,
            decision_record_id=persisted.record.id,
        )

        assert after is not None
        assert after.eligibility.eligible is False
        assert after.eligibility.reason_code == "target_not_current"

        assert after.execution.result is not None
        assert after.execution.result.record_id == result.id
        assert after.execution.result.outcome == "succeeded"
    finally:
        db.close()
        _cleanup(organization_ids)


def test_projection_is_tenant_scoped() -> None:
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
        db.commit()

        view = load_lineage_invalidation_control_plane_view(
            db,
            organization_id=other.id,
            decision_record_id=persisted.record.id,
        )

        assert view is None
    finally:
        db.close()
        _cleanup(organization_ids)


def test_unknown_decision_returns_none() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        view = load_lineage_invalidation_control_plane_view(
            db,
            organization_id=organization.id,
            decision_record_id=uuid.uuid4(),
        )

        assert view is None
    finally:
        db.close()
        _cleanup(organization_ids)


def test_projection_does_not_create_execution_records() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        persisted = _persist_decision(
            db,
            organization_id=organization.id,
        )
        db.commit()

        before_intents = (
            db.query(func.count(LineageInvalidationExecutionIntentRecord.id))
            .filter(
                LineageInvalidationExecutionIntentRecord.organization_id
                == organization.id
            )
            .scalar()
        )
        before_attempts = (
            db.query(func.count(LineageInvalidationExecutionAttemptRecord.id))
            .filter(
                LineageInvalidationExecutionAttemptRecord.organization_id
                == organization.id
            )
            .scalar()
        )
        before_results = (
            db.query(func.count(LineageInvalidationExecutionResultRecord.id))
            .filter(
                LineageInvalidationExecutionResultRecord.organization_id
                == organization.id
            )
            .scalar()
        )

        view = load_lineage_invalidation_control_plane_view(
            db,
            organization_id=organization.id,
            decision_record_id=persisted.record.id,
        )

        assert view is not None

        after_intents = (
            db.query(func.count(LineageInvalidationExecutionIntentRecord.id))
            .filter(
                LineageInvalidationExecutionIntentRecord.organization_id
                == organization.id
            )
            .scalar()
        )
        after_attempts = (
            db.query(func.count(LineageInvalidationExecutionAttemptRecord.id))
            .filter(
                LineageInvalidationExecutionAttemptRecord.organization_id
                == organization.id
            )
            .scalar()
        )
        after_results = (
            db.query(func.count(LineageInvalidationExecutionResultRecord.id))
            .filter(
                LineageInvalidationExecutionResultRecord.organization_id
                == organization.id
            )
            .scalar()
        )

        assert after_intents == before_intents == 0
        assert after_attempts == before_attempts == 0
        assert after_results == before_results == 0
    finally:
        db.close()
        _cleanup(organization_ids)
