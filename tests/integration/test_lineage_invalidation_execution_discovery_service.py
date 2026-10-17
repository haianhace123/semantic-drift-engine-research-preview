from __future__ import annotations

from datetime import timedelta
import os
import uuid

import pytest
from sqlalchemy import delete, func, select

from src.core.database import SessionLocal
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
from src.models.organization import Organization
from src.services.lineage_invalidation_execution_discovery_service import (
    discover_lineage_invalidation_execution_candidate,
    discover_lineage_invalidation_execution_candidates,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


def _make_organization(db) -> Organization:
    suffix = uuid.uuid4().hex[:12]
    organization = Organization(
        id=uuid.uuid4(),
        name=f"lineage-execution-discovery-{suffix}",
        slug=f"lineage-execution-discovery-{suffix}",
        billing_email=f"{suffix}@example.com",
    )
    db.add(organization)
    db.flush()
    return organization


def _make_intent(
    db,
    *,
    organization_id: uuid.UUID,
    authorized_at,
) -> LineageInvalidationExecutionIntentRecord:
    decision = LineageInvalidationDecisionRecord(
        id=uuid.uuid4(),
        organization_id=organization_id,
        root_source_external_id="source-a",
        root_asset_external_id=f"root-{uuid.uuid4().hex}",
        root_revision_external_id="root-r1",
        target_source_external_id="source-a",
        target_asset_external_id=f"target-{uuid.uuid4().hex}",
        target_revision_external_id="target-r1",
        policy_external_id="rag-invalidation",
        policy_revision=1,
        disposition="invalidate",
        reason_code="causal_dependency_changed",
    )
    db.add(decision)
    db.flush()

    intent = LineageInvalidationExecutionIntentRecord(
        organization_id=organization_id,
        decision_id=decision.id,
        authorized_at=authorized_at,
    )
    db.add(intent)
    db.flush()
    return intent


def _make_attempt(
    db,
    *,
    organization_id: uuid.UUID,
    intent_id: uuid.UUID,
    attempt_number: int,
    claimed_at,
    lease_expires_at,
) -> LineageInvalidationExecutionAttemptRecord:
    attempt = LineageInvalidationExecutionAttemptRecord(
        organization_id=organization_id,
        intent_id=intent_id,
        attempt_number=attempt_number,
        binding_revision=1,
        claimed_at=claimed_at,
        lease_expires_at=lease_expires_at,
    )
    db.add(attempt)
    db.flush()
    return attempt


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
            delete(Organization).where(
                Organization.id.in_(organization_ids)
            )
        )
        db.commit()
    finally:
        db.close()


def test_no_authorized_intent_returns_none() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        candidate = discover_lineage_invalidation_execution_candidate(
            db,
            organization_id=organization.id,
        )

        assert candidate is None
    finally:
        db.close()
        _cleanup(organization_ids)


def test_never_attempted_intent_is_discovered() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        db_now = db.execute(
            select(func.clock_timestamp())
        ).scalar_one()

        intent = _make_intent(
            db,
            organization_id=organization.id,
            authorized_at=db_now,
        )
        db.commit()

        candidate = discover_lineage_invalidation_execution_candidate(
            db,
            organization_id=organization.id,
        )

        assert candidate is not None
        assert candidate.organization_id == organization.id
        assert candidate.intent_id == intent.id
    finally:
        db.close()
        _cleanup(organization_ids)


def test_unexpired_latest_attempt_is_not_discovered() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        db_now = db.execute(
            select(func.clock_timestamp())
        ).scalar_one()

        intent = _make_intent(
            db,
            organization_id=organization.id,
            authorized_at=db_now - timedelta(seconds=10),
        )
        _make_attempt(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
            attempt_number=1,
            claimed_at=db_now,
            lease_expires_at=db_now + timedelta(seconds=300),
        )
        db.commit()

        candidate = discover_lineage_invalidation_execution_candidate(
            db,
            organization_id=organization.id,
        )

        assert candidate is None
    finally:
        db.close()
        _cleanup(organization_ids)


def test_expired_latest_attempt_without_result_is_discovered() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        db_now = db.execute(
            select(func.clock_timestamp())
        ).scalar_one()

        intent = _make_intent(
            db,
            organization_id=organization.id,
            authorized_at=db_now - timedelta(seconds=600),
        )
        _make_attempt(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
            attempt_number=1,
            claimed_at=db_now - timedelta(seconds=600),
            lease_expires_at=db_now - timedelta(seconds=300),
        )
        db.commit()

        candidate = discover_lineage_invalidation_execution_candidate(
            db,
            organization_id=organization.id,
        )

        assert candidate is not None
        assert candidate.organization_id == organization.id
        assert candidate.intent_id == intent.id
    finally:
        db.close()
        _cleanup(organization_ids)


@pytest.mark.parametrize(
    "outcome",
    (
        "succeeded",
        "failed",
    ),
)
def test_terminal_latest_attempt_is_not_discovered(
    outcome: str,
) -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        db_now = db.execute(
            select(func.clock_timestamp())
        ).scalar_one()

        intent = _make_intent(
            db,
            organization_id=organization.id,
            authorized_at=db_now - timedelta(seconds=600),
        )
        attempt = _make_attempt(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
            attempt_number=1,
            claimed_at=db_now - timedelta(seconds=600),
            lease_expires_at=db_now - timedelta(seconds=300),
        )
        db.add(
            LineageInvalidationExecutionResultRecord(
                organization_id=organization.id,
                attempt_id=attempt.id,
                outcome=outcome,
                recorded_at=db_now - timedelta(seconds=200),
            )
        )
        db.commit()

        candidate = discover_lineage_invalidation_execution_candidate(
            db,
            organization_id=organization.id,
        )

        assert candidate is None
    finally:
        db.close()
        _cleanup(organization_ids)


def test_discovery_uses_latest_attempt() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        db_now = db.execute(
            select(func.clock_timestamp())
        ).scalar_one()

        intent = _make_intent(
            db,
            organization_id=organization.id,
            authorized_at=db_now - timedelta(seconds=900),
        )
        _make_attempt(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
            attempt_number=1,
            claimed_at=db_now - timedelta(seconds=900),
            lease_expires_at=db_now - timedelta(seconds=800),
        )
        _make_attempt(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
            attempt_number=2,
            claimed_at=db_now,
            lease_expires_at=db_now + timedelta(seconds=300),
        )
        db.commit()

        candidate = discover_lineage_invalidation_execution_candidate(
            db,
            organization_id=organization.id,
        )

        assert candidate is None
    finally:
        db.close()
        _cleanup(organization_ids)


def test_oldest_authorized_claimable_intent_is_discovered() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        db_now = db.execute(
            select(func.clock_timestamp())
        ).scalar_one()

        oldest = _make_intent(
            db,
            organization_id=organization.id,
            authorized_at=db_now - timedelta(seconds=20),
        )
        _make_intent(
            db,
            organization_id=organization.id,
            authorized_at=db_now - timedelta(seconds=10),
        )
        db.commit()

        candidate = discover_lineage_invalidation_execution_candidate(
            db,
            organization_id=organization.id,
        )

        assert candidate is not None
        assert candidate.intent_id == oldest.id
    finally:
        db.close()
        _cleanup(organization_ids)


def test_organization_filter_isolates_discovery() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        first = _make_organization(db)
        second = _make_organization(db)
        organization_ids.extend(
            [
                first.id,
                second.id,
            ]
        )

        db_now = db.execute(
            select(func.clock_timestamp())
        ).scalar_one()

        _make_intent(
            db,
            organization_id=first.id,
            authorized_at=db_now - timedelta(seconds=20),
        )
        second_intent = _make_intent(
            db,
            organization_id=second.id,
            authorized_at=db_now - timedelta(seconds=10),
        )
        db.commit()

        candidate = discover_lineage_invalidation_execution_candidate(
            db,
            organization_id=second.id,
        )

        assert candidate is not None
        assert candidate.organization_id == second.id
        assert candidate.intent_id == second_intent.id
    finally:
        db.close()
        _cleanup(organization_ids)


def test_discovery_batch_returns_candidates_in_authorization_order() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        db_now = db.execute(
            select(func.clock_timestamp())
        ).scalar_one()

        oldest = _make_intent(
            db,
            organization_id=organization.id,
            authorized_at=db_now - timedelta(seconds=30),
        )
        middle = _make_intent(
            db,
            organization_id=organization.id,
            authorized_at=db_now - timedelta(seconds=20),
        )
        newest = _make_intent(
            db,
            organization_id=organization.id,
            authorized_at=db_now - timedelta(seconds=10),
        )
        db.commit()

        candidates = discover_lineage_invalidation_execution_candidates(
            db,
            organization_id=organization.id,
            limit=3,
        )

        assert tuple(
            candidate.intent_id for candidate in candidates
        ) == (
            oldest.id,
            middle.id,
            newest.id,
        )
    finally:
        db.close()
        _cleanup(organization_ids)


def test_discovery_batch_respects_limit() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        db_now = db.execute(
            select(func.clock_timestamp())
        ).scalar_one()

        oldest = _make_intent(
            db,
            organization_id=organization.id,
            authorized_at=db_now - timedelta(seconds=30),
        )
        middle = _make_intent(
            db,
            organization_id=organization.id,
            authorized_at=db_now - timedelta(seconds=20),
        )
        _make_intent(
            db,
            organization_id=organization.id,
            authorized_at=db_now - timedelta(seconds=10),
        )
        db.commit()

        candidates = discover_lineage_invalidation_execution_candidates(
            db,
            organization_id=organization.id,
            limit=2,
        )

        assert tuple(
            candidate.intent_id for candidate in candidates
        ) == (
            oldest.id,
            middle.id,
        )
    finally:
        db.close()
        _cleanup(organization_ids)


def test_expired_attempt_within_retry_cooldown_is_not_discovered() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        db_now = db.execute(
            select(func.clock_timestamp())
        ).scalar_one()

        intent = _make_intent(
            db,
            organization_id=organization.id,
            authorized_at=db_now - timedelta(seconds=600),
        )

        _make_attempt(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
            attempt_number=1,
            claimed_at=db_now - timedelta(seconds=330),
            lease_expires_at=db_now - timedelta(seconds=30),
        )
        db.commit()

        candidate = discover_lineage_invalidation_execution_candidate(
            db,
            organization_id=organization.id,
        )

        assert candidate is None

    finally:
        db.close()
        _cleanup(organization_ids)


def test_expired_attempt_after_retry_cooldown_is_discovered() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        db_now = db.execute(
            select(func.clock_timestamp())
        ).scalar_one()

        intent = _make_intent(
            db,
            organization_id=organization.id,
            authorized_at=db_now - timedelta(seconds=600),
        )

        _make_attempt(
            db,
            organization_id=organization.id,
            intent_id=intent.id,
            attempt_number=1,
            claimed_at=db_now - timedelta(seconds=390),
            lease_expires_at=db_now - timedelta(seconds=90),
        )
        db.commit()

        candidate = discover_lineage_invalidation_execution_candidate(
            db,
            organization_id=organization.id,
        )

        assert candidate is not None
        assert candidate.intent_id == intent.id

    finally:
        db.close()
        _cleanup(organization_ids)