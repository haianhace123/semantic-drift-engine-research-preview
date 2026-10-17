from __future__ import annotations

import os
import threading
import uuid

import pytest
from sqlalchemy import delete

from src.core.database import SessionLocal
from src.domain.lineage_invalidation import (
    LineageInvalidationDecision,
    LineageInvalidationPolicyIdentity,
)
from src.domain.lineage_revision import (
    LineageRevisionIdentity,
)
from src.models.lineage_invalidation_decision import (
    LineageInvalidationDecisionPathRevisionRecord,
    LineageInvalidationDecisionRecord,
)
from src.models.organization import Organization
from src.services.lineage_invalidation_persistence_service import (
    LineageInvalidationDecisionConflictError,
    persist_lineage_invalidation_decision,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


def _make_organization(db) -> Organization:
    suffix = uuid.uuid4().hex[:12]
    organization = Organization(
        id=uuid.uuid4(),
        name=f"lineage-invalidation-{suffix}",
        slug=f"lineage-invalidation-{suffix}",
        billing_email=f"{suffix}@example.com",
    )
    db.add(organization)
    db.flush()
    return organization


def _revision(
    asset_external_id: str,
) -> LineageRevisionIdentity:
    return LineageRevisionIdentity(
        source_external_id="policy-source",
        asset_external_id=asset_external_id,
        revision_external_id=f"{asset_external_id}@v1",
    )


def _decision(
    *,
    disposition: str = "invalidate",
    reason_code: str = "causal_dependency_changed",
    policy_revision: int = 7,
    intermediate_asset_external_id: str = "index-a",
) -> LineageInvalidationDecision:
    root = _revision("chunk-a")
    intermediate = _revision(
        intermediate_asset_external_id
    )
    target = _revision("agent-a")

    return LineageInvalidationDecision(
        root=root,
        target=target,
        policy=LineageInvalidationPolicyIdentity(
            policy_external_id="default-lineage-policy",
            revision=policy_revision,
        ),
        disposition=disposition,
        reason_code=reason_code,
        causal_path=(
            root,
            intermediate,
            target,
        ),
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
            delete(LineageInvalidationDecisionRecord).where(
                LineageInvalidationDecisionRecord.organization_id.in_(
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


def test_persists_exact_ordered_causal_path() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        result = persist_lineage_invalidation_decision(
            db,
            organization_id=organization.id,
            decision=_decision(),
        )

        assert result.created is True
        record_id = result.record.id
        db.commit()

        db.expire_all()

        persisted = (
            db.query(LineageInvalidationDecisionRecord)
            .filter(
                LineageInvalidationDecisionRecord.id
                == record_id
            )
            .one()
        )

        assert persisted.organization_id == organization.id
        assert persisted.disposition == "invalidate"
        assert persisted.policy_external_id == (
            "default-lineage-policy"
        )
        assert persisted.policy_revision == 7

        assert [
            (
                revision.ordinal,
                revision.asset_external_id,
                revision.revision_external_id,
            )
            for revision in persisted.causal_path
        ] == [
            (0, "chunk-a", "chunk-a@v1"),
            (1, "index-a", "index-a@v1"),
            (2, "agent-a", "agent-a@v1"),
        ]
    finally:
        db.close()
        _cleanup(organization_ids)


def test_identical_replay_is_idempotent() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        decision = _decision()

        first = persist_lineage_invalidation_decision(
            db,
            organization_id=organization.id,
            decision=decision,
        )
        second = persist_lineage_invalidation_decision(
            db,
            organization_id=organization.id,
            decision=decision,
        )

        assert first.created is True
        assert second.created is False
        assert second.record.id == first.record.id

        assert (
            db.query(LineageInvalidationDecisionRecord)
            .filter(
                LineageInvalidationDecisionRecord.organization_id
                == organization.id
            )
            .count()
            == 1
        )

        db.commit()
    finally:
        db.close()
        _cleanup(organization_ids)


@pytest.mark.parametrize(
    "conflicting_decision",
    (
        _decision(
            disposition="rematerialize",
        ),
        _decision(
            reason_code="policy_override",
        ),
        _decision(
            intermediate_asset_external_id="index-b",
        ),
    ),
)
def test_conflicting_payload_replay_fails_closed(
    conflicting_decision: LineageInvalidationDecision,
) -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        persist_lineage_invalidation_decision(
            db,
            organization_id=organization.id,
            decision=_decision(),
        )

        with pytest.raises(
            LineageInvalidationDecisionConflictError
        ):
            persist_lineage_invalidation_decision(
                db,
                organization_id=organization.id,
                decision=conflicting_decision,
            )

        db.rollback()
    finally:
        db.close()
        _cleanup(organization_ids)


def test_same_decision_identity_is_tenant_isolated() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        first_organization = _make_organization(db)
        second_organization = _make_organization(db)
        organization_ids.extend(
            [
                first_organization.id,
                second_organization.id,
            ]
        )
        db.commit()

        decision = _decision()

        first = persist_lineage_invalidation_decision(
            db,
            organization_id=first_organization.id,
            decision=decision,
        )
        second = persist_lineage_invalidation_decision(
            db,
            organization_id=second_organization.id,
            decision=decision,
        )

        assert first.created is True
        assert second.created is True
        assert first.record.id != second.record.id

        db.commit()
    finally:
        db.close()
        _cleanup(organization_ids)


def test_policy_revision_is_part_of_decision_identity() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        first = persist_lineage_invalidation_decision(
            db,
            organization_id=organization.id,
            decision=_decision(
                policy_revision=7,
            ),
        )
        second = persist_lineage_invalidation_decision(
            db,
            organization_id=organization.id,
            decision=_decision(
                policy_revision=8,
            ),
        )

        assert first.created is True
        assert second.created is True
        assert first.record.id != second.record.id

        db.commit()
    finally:
        db.close()
        _cleanup(organization_ids)


def test_persistence_rolls_back_with_caller_transaction() -> None:
    setup_db = SessionLocal()
    execution_db = SessionLocal()
    verify_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(setup_db)
        organization_ids.append(organization.id)
        setup_db.commit()

        result = persist_lineage_invalidation_decision(
            execution_db,
            organization_id=organization.id,
            decision=_decision(),
        )

        record_id = result.record.id
        execution_db.rollback()

        persisted = (
            verify_db.query(LineageInvalidationDecisionRecord)
            .filter(
                LineageInvalidationDecisionRecord.id
                == record_id
            )
            .one_or_none()
        )

        assert persisted is None
    finally:
        setup_db.close()
        execution_db.close()
        verify_db.close()
        _cleanup(organization_ids)


def test_decision_and_causal_path_rollback_atomically() -> None:
    setup_db = SessionLocal()
    execution_db = SessionLocal()
    verify_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(setup_db)
        organization_ids.append(organization.id)
        setup_db.commit()

        result = persist_lineage_invalidation_decision(
            execution_db,
            organization_id=organization.id,
            decision=_decision(),
        )

        record_id = result.record.id
        execution_db.rollback()

        assert (
            verify_db.query(LineageInvalidationDecisionRecord)
            .filter(
                LineageInvalidationDecisionRecord.id
                == record_id
            )
            .one_or_none()
            is None
        )
        assert (
            verify_db.query(
                LineageInvalidationDecisionPathRevisionRecord
            )
            .filter(
                LineageInvalidationDecisionPathRevisionRecord.decision_id
                == record_id
            )
            .count()
            == 0
        )
    finally:
        setup_db.close()
        execution_db.close()
        verify_db.close()
        _cleanup(organization_ids)


def test_concurrent_identical_replay_creates_one_decision() -> None:
    setup_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(setup_db)
        organization_ids.append(organization.id)
        setup_db.commit()

        organization_id = organization.id
        decision = _decision()

        barrier = threading.Barrier(2)
        results: list[tuple[bool, uuid.UUID]] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def persist() -> None:
            db = SessionLocal()

            try:
                barrier.wait()
                result = persist_lineage_invalidation_decision(
                    db,
                    organization_id=organization_id,
                    decision=decision,
                )
                db.commit()

                with lock:
                    results.append(
                        (
                            result.created,
                            result.record.id,
                        )
                    )
            except BaseException as exc:
                db.rollback()

                with lock:
                    errors.append(exc)
            finally:
                db.close()

        threads = [
            threading.Thread(target=persist),
            threading.Thread(target=persist),
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join(timeout=10)

        assert all(
            not thread.is_alive()
            for thread in threads
        )
        assert errors == []
        assert len(results) == 2
        assert sorted(
            created
            for created, _ in results
        ) == [
            False,
            True,
        ]
        assert len(
            {
                record_id
                for _, record_id in results
            }
        ) == 1

        verify_db = SessionLocal()

        try:
            assert (
                verify_db.query(
                    LineageInvalidationDecisionRecord
                )
                .filter(
                    LineageInvalidationDecisionRecord.organization_id
                    == organization_id
                )
                .count()
                == 1
            )
        finally:
            verify_db.close()
    finally:
        setup_db.close()
        _cleanup(organization_ids)


def test_concurrent_conflicting_replay_fails_closed() -> None:
    setup_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(setup_db)
        organization_ids.append(organization.id)
        setup_db.commit()

        organization_id = organization.id
        barrier = threading.Barrier(2)
        successes: list[tuple[bool, uuid.UUID, str]] = []
        conflicts: list[
            LineageInvalidationDecisionConflictError
        ] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def persist(
            disposition: str,
        ) -> None:
            db = SessionLocal()

            try:
                barrier.wait()
                result = persist_lineage_invalidation_decision(
                    db,
                    organization_id=organization_id,
                    decision=_decision(
                        disposition=disposition,
                    ),
                )
                db.commit()

                with lock:
                    successes.append(
                        (
                            result.created,
                            result.record.id,
                            disposition,
                        )
                    )
            except LineageInvalidationDecisionConflictError as exc:
                db.rollback()

                with lock:
                    conflicts.append(exc)
            except BaseException as exc:
                db.rollback()

                with lock:
                    errors.append(exc)
            finally:
                db.close()

        threads = [
            threading.Thread(
                target=persist,
                args=("invalidate",),
            ),
            threading.Thread(
                target=persist,
                args=("rematerialize",),
            ),
        ]

        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join(timeout=10)

        assert all(
            not thread.is_alive()
            for thread in threads
        )
        assert errors == []
        assert len(successes) == 1
        assert successes[0][0] is True
        assert len(conflicts) == 1

        verify_db = SessionLocal()

        try:
            rows = (
                verify_db.query(
                    LineageInvalidationDecisionRecord
                )
                .filter(
                    LineageInvalidationDecisionRecord.organization_id
                    == organization_id
                )
                .all()
            )

            assert len(rows) == 1
            assert rows[0].disposition in {
                "invalidate",
                "rematerialize",
            }
            assert (
                rows[0].disposition
                == successes[0][2]
            )
        finally:
            verify_db.close()
    finally:
        setup_db.close()
        _cleanup(organization_ids)


def test_conflicting_replay_preserves_outer_transaction() -> None:
    setup_db = SessionLocal()
    execution_db = SessionLocal()
    verify_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(setup_db)
        organization_ids.append(organization.id)
        setup_db.commit()

        persist_lineage_invalidation_decision(
            setup_db,
            organization_id=organization.id,
            decision=_decision(),
        )
        setup_db.commit()

        independent = persist_lineage_invalidation_decision(
            execution_db,
            organization_id=organization.id,
            decision=LineageInvalidationDecision(
                root=_revision("chunk-independent"),
                target=_revision("agent-independent"),
                policy=LineageInvalidationPolicyIdentity(
                    policy_external_id="default-lineage-policy",
                    revision=7,
                ),
                disposition="no_action",
                reason_code="policy_exempt",
                causal_path=(
                    _revision("chunk-independent"),
                    _revision("agent-independent"),
                ),
            ),
        )

        with pytest.raises(
            LineageInvalidationDecisionConflictError
        ):
            persist_lineage_invalidation_decision(
                execution_db,
                organization_id=organization.id,
                decision=_decision(
                    disposition="rematerialize",
                ),
            )

        independent_id = independent.record.id
        execution_db.commit()

        persisted = (
            verify_db.query(LineageInvalidationDecisionRecord)
            .filter(
                LineageInvalidationDecisionRecord.id
                == independent_id
            )
            .one_or_none()
        )

        assert persisted is not None
        assert persisted.disposition == "no_action"
    finally:
        setup_db.close()
        execution_db.close()
        verify_db.close()
        _cleanup(organization_ids)