from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import OperationalError

from src.core.database import SessionLocal
from src.models.organization import Organization
from src.services.organization_execution_lock_service import (
    acquire_organization_execution_lock,
)


def _make_organization(db):
    suffix = uuid.uuid4().hex[:10]
    organization = Organization(
        id=uuid.uuid4(),
        name=f"execution-lock-{suffix}",
        slug=f"execution-lock-{suffix}",
        billing_email=f"{suffix}@example.com",
    )
    db.add(organization)
    db.flush()
    return organization


def _cleanup_organizations(
    organization_ids,
):
    db = SessionLocal()

    try:
        db.rollback()
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


def test_same_organization_execution_is_serialized():
    setup_db = SessionLocal()
    organization_id = None

    try:
        organization = _make_organization(
            setup_db
        )
        organization_id = organization.id
        setup_db.commit()
    finally:
        setup_db.close()

    first_db = SessionLocal()
    second_db = SessionLocal()

    try:
        acquire_organization_execution_lock(
            first_db,
            organization_id=organization_id,
        )

        second_db.execute(
            text(
                "SET LOCAL lock_timeout = '100ms'"
            )
        )

        with pytest.raises(
            OperationalError
        ):
            acquire_organization_execution_lock(
                second_db,
                organization_id=organization_id,
            )

        second_db.rollback()

        first_db.commit()

        acquire_organization_execution_lock(
            second_db,
            organization_id=organization_id,
        )
        second_db.rollback()
    finally:
        first_db.rollback()
        second_db.rollback()
        first_db.close()
        second_db.close()

        if organization_id is not None:
            _cleanup_organizations(
                [organization_id]
            )


def test_different_organizations_execute_independently():
    setup_db = SessionLocal()
    organization_ids = []

    try:
        first_organization = (
            _make_organization(
                setup_db
            )
        )
        second_organization = (
            _make_organization(
                setup_db
            )
        )
        organization_ids = [
            first_organization.id,
            second_organization.id,
        ]
        setup_db.commit()
    finally:
        setup_db.close()

    first_db = SessionLocal()
    second_db = SessionLocal()

    try:
        acquire_organization_execution_lock(
            first_db,
            organization_id=(
                organization_ids[0]
            ),
        )

        second_db.execute(
            text(
                "SET LOCAL lock_timeout = '100ms'"
            )
        )

        acquire_organization_execution_lock(
            second_db,
            organization_id=(
                organization_ids[1]
            ),
        )

        second_db.rollback()
        first_db.rollback()
    finally:
        first_db.rollback()
        second_db.rollback()
        first_db.close()
        second_db.close()

        if organization_ids:
            _cleanup_organizations(
                organization_ids
            )