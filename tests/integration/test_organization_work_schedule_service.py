from __future__ import annotations

from datetime import datetime, timezone
import os
import uuid

import pytest
from sqlalchemy import delete

from src.core.database import SessionLocal
from src.models.organization import Organization
from src.models.organization_work_schedule import (
    WORK_KIND_ALERT_RECONCILIATION,
    WORK_KIND_INTELLIGENCE,
    OrganizationWorkSchedule,
)
from src.services.organization_work_schedule_service import (
    clear_organization_work_schedule,
    schedule_organization_work_retry,
)


UTC = timezone.utc


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


def _make_organization(db) -> Organization:
    suffix = uuid.uuid4().hex[:12]

    organization = Organization(
        id=uuid.uuid4(),
        name=f"work-schedule-{suffix}",
        slug=f"work-schedule-{suffix}",
        billing_email=f"{suffix}@example.com",
    )

    db.add(organization)
    db.flush()

    return organization


def _load_schedule(
    db,
    *,
    organization_id,
    work_kind,
):
    return (
        db.query(OrganizationWorkSchedule)
        .filter(
            OrganizationWorkSchedule.organization_id
            == organization_id,
            OrganizationWorkSchedule.work_kind
            == work_kind,
        )
        .one_or_none()
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
                OrganizationWorkSchedule
            ).where(
                OrganizationWorkSchedule.organization_id.in_(
                    organization_ids
                )
            )
        )

        db.execute(
            delete(
                Organization
            ).where(
                Organization.id.in_(
                    organization_ids
                )
            )
        )

        db.commit()

    finally:
        db.close()


def test_schedule_retry_upserts_next_eligibility():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        first_now = datetime(
            2036,
            9,
            14,
            3,
            0,
            tzinfo=UTC,
        )

        first_retry_after = schedule_organization_work_retry(
            db,
            organization_id=organization.id,
            work_kind=WORK_KIND_INTELLIGENCE,
            retry_seconds=60,
            now=first_now,
        )

        db.commit()

        first = _load_schedule(
            db,
            organization_id=organization.id,
            work_kind=WORK_KIND_INTELLIGENCE,
        )

        assert first is not None
        assert first.retry_after == first_retry_after
        assert first.retry_after == datetime(
            2036,
            9,
            14,
            3,
            1,
            tzinfo=UTC,
        )

        second_now = datetime(
            2036,
            9,
            14,
            4,
            0,
            tzinfo=UTC,
        )

        second_retry_after = schedule_organization_work_retry(
            db,
            organization_id=organization.id,
            work_kind=WORK_KIND_INTELLIGENCE,
            retry_seconds=30,
            now=second_now,
        )

        db.commit()

        second = _load_schedule(
            db,
            organization_id=organization.id,
            work_kind=WORK_KIND_INTELLIGENCE,
        )

        assert second is not None
        assert second.retry_after == second_retry_after
        assert second.retry_after == datetime(
            2036,
            9,
            14,
            4,
            0,
            30,
            tzinfo=UTC,
        )

        count = (
            db.query(OrganizationWorkSchedule)
            .filter(
                OrganizationWorkSchedule.organization_id
                == organization.id,
                OrganizationWorkSchedule.work_kind
                == WORK_KIND_INTELLIGENCE,
            )
            .count()
        )

        assert count == 1

    finally:
        db.close()
        _cleanup(organization_ids)


def test_work_kinds_are_scheduled_and_cleared_independently():
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)
        db.commit()

        now = datetime(
            2036,
            9,
            14,
            3,
            0,
            tzinfo=UTC,
        )

        schedule_organization_work_retry(
            db,
            organization_id=organization.id,
            work_kind=WORK_KIND_INTELLIGENCE,
            retry_seconds=60,
            now=now,
        )

        schedule_organization_work_retry(
            db,
            organization_id=organization.id,
            work_kind=WORK_KIND_ALERT_RECONCILIATION,
            retry_seconds=120,
            now=now,
        )

        db.commit()

        intelligence = _load_schedule(
            db,
            organization_id=organization.id,
            work_kind=WORK_KIND_INTELLIGENCE,
        )
        reconciliation = _load_schedule(
            db,
            organization_id=organization.id,
            work_kind=WORK_KIND_ALERT_RECONCILIATION,
        )

        assert intelligence is not None
        assert reconciliation is not None
        assert (
            intelligence.retry_after
            != reconciliation.retry_after
        )

        clear_organization_work_schedule(
            db,
            organization_id=organization.id,
            work_kind=WORK_KIND_INTELLIGENCE,
        )

        db.commit()

        intelligence = _load_schedule(
            db,
            organization_id=organization.id,
            work_kind=WORK_KIND_INTELLIGENCE,
        )
        reconciliation = _load_schedule(
            db,
            organization_id=organization.id,
            work_kind=WORK_KIND_ALERT_RECONCILIATION,
        )

        assert intelligence is None
        assert reconciliation is not None

    finally:
        db.close()
        _cleanup(organization_ids)


def test_schedule_retry_rolls_back_with_caller_transaction():
    setup_db = SessionLocal()
    execution_db = SessionLocal()
    verify_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(setup_db)
        organization_ids.append(organization.id)
        setup_db.commit()

        schedule_organization_work_retry(
            execution_db,
            organization_id=organization.id,
            work_kind=WORK_KIND_INTELLIGENCE,
            retry_seconds=60,
            now=datetime(
                2036,
                9,
                14,
                3,
                0,
                tzinfo=UTC,
            ),
        )

        execution_db.rollback()

        persisted = _load_schedule(
            verify_db,
            organization_id=organization.id,
            work_kind=WORK_KIND_INTELLIGENCE,
        )

        assert persisted is None

    finally:
        setup_db.close()
        execution_db.close()
        verify_db.close()
        _cleanup(organization_ids)


def test_clear_schedule_rolls_back_with_caller_transaction():
    setup_db = SessionLocal()
    execution_db = SessionLocal()
    verify_db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(setup_db)
        organization_ids.append(organization.id)
        setup_db.commit()

        schedule_organization_work_retry(
            setup_db,
            organization_id=organization.id,
            work_kind=WORK_KIND_INTELLIGENCE,
            retry_seconds=60,
            now=datetime(
                2036,
                9,
                14,
                3,
                0,
                tzinfo=UTC,
            ),
        )

        setup_db.commit()

        clear_organization_work_schedule(
            execution_db,
            organization_id=organization.id,
            work_kind=WORK_KIND_INTELLIGENCE,
        )

        execution_db.rollback()

        persisted = _load_schedule(
            verify_db,
            organization_id=organization.id,
            work_kind=WORK_KIND_INTELLIGENCE,
        )

        assert persisted is not None

    finally:
        setup_db.close()
        execution_db.close()
        verify_db.close()
        _cleanup(organization_ids)
