from __future__ import annotations

from datetime import datetime, timezone
import os
from types import SimpleNamespace
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

import src.main as main_module

from src.api import temporal_routes
from src.core.database import SessionLocal
from src.core.security import get_current_user
from src.main import app
from src.models.alert import (
    ALERT_SOURCE_PATTERN,
    ALERT_STATUS_PENDING,
    Alert,
)
from src.models.organization import Organization


pytestmark = pytest.mark.skipif(
    os.getenv(
        "RUN_DB_INTEGRATION_TESTS"
    )
    != "1",
    reason=(
        "RUN_DB_INTEGRATION_TESTS is not enabled"
    ),
)


client = TestClient(
    app
)


BASE = datetime(
    2026,
    9,
    10,
    8,
    0,
    0,
    tzinfo=timezone.utc,
)


def _fake_user(
    organization_id: uuid.UUID,
):
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization_id,
        email="temporal-alerts-api@example.com",
    )


def _database_override():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()


def _allow_auth_middleware(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        main_module,
        "verify_token",
        lambda token: {
            "sub": str(
                uuid.uuid4()
            ),
        },
    )


def _create_organization(
    db,
    *,
    prefix: str,
) -> Organization:
    token = uuid.uuid4().hex

    organization = Organization(
        id=uuid.uuid4(),
        name=(
            f"Temporal Alerts {prefix} "
            f"{token[:8]}"
        ),
        slug=(
            f"temporal-alerts-"
            f"{prefix.lower()}-"
            f"{token}"
        ),
        billing_email=(
            f"temporal-alerts-"
            f"{token[:16]}"
            "@example.com"
        ),
    )

    db.add(
        organization
    )
    db.flush()

    return organization


def _create_pattern_alert(
    db,
    *,
    organization_id: uuid.UUID,
    title: str,
    occurred_at: datetime,
) -> Alert:
    source_id = uuid.uuid4()

    alert = Alert(
        id=uuid.uuid4(),
        organization_id=organization_id,
        event_id=None,
        source_type=ALERT_SOURCE_PATTERN,
        source_id=source_id,
        detection_key=(
            uuid.uuid4().hex
            + uuid.uuid4().hex
        ),
        alert_type="pattern",
        title=title,
        message=(
            "Repeated semantic drift pattern "
            "detected."
        ),
        severity="high",
        recommendation=(
            "Review the repeated drift sequence."
        ),
        affected_entities=[
            "TemporalEntity",
        ],
        occurred_at=occurred_at,
        status=ALERT_STATUS_PENDING,
        sent_to=[],
        created_at=occurred_at,
    )

    db.add(
        alert
    )
    db.flush()

    return alert


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
                Alert
            ).where(
                Alert.organization_id.in_(
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


def test_temporal_alerts_returns_eventless_pattern_alert_and_isolates_tenant(
    monkeypatch,
):
    _allow_auth_middleware(
        monkeypatch
    )

    setup_db = SessionLocal()

    organization_ids: list[
        uuid.UUID
    ] = []

    try:
        own_organization = (
            _create_organization(
                setup_db,
                prefix="Own",
            )
        )
        organization_ids.append(
            own_organization.id
        )

        foreign_organization = (
            _create_organization(
                setup_db,
                prefix="Foreign",
            )
        )
        organization_ids.append(
            foreign_organization.id
        )

        own_alert = (
            _create_pattern_alert(
                setup_db,
                organization_id=(
                    own_organization.id
                ),
                title=(
                    "Own eventless "
                    "Pattern Alert"
                ),
                occurred_at=BASE,
            )
        )

        foreign_alert = (
            _create_pattern_alert(
                setup_db,
                organization_id=(
                    foreign_organization.id
                ),
                title=(
                    "Foreign Pattern Alert"
                ),
                occurred_at=BASE,
            )
        )

        own_alert_id = own_alert.id
        own_source_id = own_alert.source_id

        foreign_alert_id = (
            foreign_alert.id
        )

        setup_db.commit()

        app.dependency_overrides[
            get_current_user
        ] = lambda: _fake_user(
            own_organization.id
        )

        app.dependency_overrides[
            temporal_routes.get_db
        ] = _database_override

        response = client.get(
            "/api/temporal/alerts",
            headers={
                "Authorization": (
                    "Bearer "
                    "temporal-alerts-test-token"
                ),
            },
        )

        assert response.status_code == 200

        payload = response.json()

        assert payload["total"] == 1
        assert payload["offset"] == 0
        assert payload["limit"] == 50

        assert len(
            payload["data"]
        ) == 1

        item = payload["data"][0]

        assert (
            item["id"]
            == str(
                own_alert_id
            )
        )

        assert (
            item["event_id"]
            is None
        )

        assert (
            item["entity_id"]
            is None
        )

        assert (
            item["entity_name"]
            is None
        )

        assert (
            item["domain"]
            is None
        )

        assert (
            item["url"]
            is None
        )

        assert (
            item["page_type"]
            is None
        )

        assert (
            item["alert_type"]
            == "pattern"
        )

        assert (
            item["source_type"]
            == ALERT_SOURCE_PATTERN
        )

        assert (
            item["source_id"]
            == str(
                own_source_id
            )
        )

        assert (
            item["occurred_at"]
            == BASE.isoformat()
        )

        assert (
            item["title"]
            == (
                "Own eventless "
                "Pattern Alert"
            )
        )

        returned_ids = {
            row["id"]
            for row in payload["data"]
        }

        assert (
            str(
                foreign_alert_id
            )
            not in returned_ids
        )

    finally:
        app.dependency_overrides.pop(
            get_current_user,
            None,
        )

        app.dependency_overrides.pop(
            temporal_routes.get_db,
            None,
        )

        setup_db.close()

        _cleanup(
            organization_ids
        )