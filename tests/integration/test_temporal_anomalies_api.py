from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
from src.models.anomaly import Anomaly
from src.models.entity import Entity
from src.models.monitored_page import MonitoredPage
from src.models.organization import Organization
from src.models.page_snapshot import PageSnapshot
from src.models.semantic_drift_event import SemanticDriftEvent


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
    20,
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
        email="temporal-anomalies-api@example.com",
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
            f"Temporal Anomalies {prefix} "
            f"{token[:8]}"
        ),
        slug=(
            f"temporal-anomalies-"
            f"{prefix.lower()}-"
            f"{token}"
        ),
        billing_email=(
            f"temporal-anomalies-"
            f"{token[:16]}"
            "@example.com"
        ),
    )

    db.add(
        organization
    )
    db.flush()

    return organization


def _create_anomaly(
    db,
    *,
    organization: Organization,
    suffix: str,
    anomaly_type: str,
    significance: float,
) -> Anomaly:
    entity = Entity(
        id=uuid.uuid4(),
        organization_id=organization.id,
        name=(
            f"Temporal Anomaly "
            f"{suffix}"
        ),
        domain=(
            f"{suffix.lower()}-"
            f"{uuid.uuid4().hex[:12]}"
            ".example.com"
        ),
        category="technology",
    )

    db.add(
        entity
    )
    db.flush()

    page = MonitoredPage(
        id=uuid.uuid4(),
        organization_id=organization.id,
        entity_id=entity.id,
        url=(
            f"https://{entity.domain}/docs"
        ),
        page_type="docs",
        crawl_frequency="daily",
        is_active=True,
    )

    db.add(
        page
    )
    db.flush()

    old_snapshot = PageSnapshot(
        id=uuid.uuid4(),
        organization_id=organization.id,
        monitored_page_id=page.id,
        content_hash=(
            f"old-{suffix.lower()}-"
            f"{uuid.uuid4().hex}"
        ),
        raw_content=(
            f"old {suffix}"
        ),
        content_size=len(
            f"old {suffix}"
        ),
        crawled_at=(
            BASE
            - timedelta(
                minutes=1
            )
        ),
    )

    new_snapshot = PageSnapshot(
        id=uuid.uuid4(),
        organization_id=organization.id,
        monitored_page_id=page.id,
        content_hash=(
            f"new-{suffix.lower()}-"
            f"{uuid.uuid4().hex}"
        ),
        raw_content=(
            f"new {suffix}"
        ),
        content_size=len(
            f"new {suffix}"
        ),
        crawled_at=BASE,
    )

    db.add_all(
        [
            old_snapshot,
            new_snapshot,
        ]
    )
    db.flush()

    event = SemanticDriftEvent(
        id=uuid.uuid4(),
        organization_id=organization.id,
        monitored_page_id=page.id,
        old_snapshot_id=old_snapshot.id,
        new_snapshot_id=new_snapshot.id,
        semantic_delta_json={
            "test": suffix,
        },
        impact_score=50,
        detected_at=BASE,
    )

    db.add(
        event
    )
    db.flush()

    anomaly = Anomaly(
        id=uuid.uuid4(),
        organization_id=organization.id,
        event_id=event.id,
        anomaly_type=anomaly_type,
        description=(
            f"{suffix} anomaly"
        ),
        expected_value=0.25,
        actual_value=0.75,
        deviation_score=2.5,
        significance=significance,
        baseline_days=28,
        window_days=7,
        detection_method="integration-test",
        detected_at=BASE,
        is_confirmed=False,
        is_dismissed=False,
        is_archived=False,
    )

    db.add(
        anomaly
    )
    db.flush()

    return anomaly


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
                Anomaly
            ).where(
                Anomaly.organization_id.in_(
                    organization_ids
                )
            )
        )

        db.execute(
            delete(
                SemanticDriftEvent
            ).where(
                SemanticDriftEvent.organization_id.in_(
                    organization_ids
                )
            )
        )

        db.execute(
            delete(
                PageSnapshot
            ).where(
                PageSnapshot.organization_id.in_(
                    organization_ids
                )
            )
        )

        db.execute(
            delete(
                MonitoredPage
            ).where(
                MonitoredPage.organization_id.in_(
                    organization_ids
                )
            )
        )

        db.execute(
            delete(
                Entity
            ).where(
                Entity.organization_id.in_(
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


def test_temporal_anomalies_returns_anomaly_and_isolates_tenant(
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
        other_organization = (
            _create_organization(
                setup_db,
                prefix="Other",
            )
        )

        organization_ids.extend(
            [
                own_organization.id,
                other_organization.id,
            ]
        )

        own_anomaly = (
            _create_anomaly(
                setup_db,
                organization=own_organization,
                suffix="Own",
                anomaly_type="magnitude",
                significance=0.91,
            )
        )

        other_anomaly = (
            _create_anomaly(
                setup_db,
                organization=other_organization,
                suffix="Other",
                anomaly_type="frequency",
                significance=0.99,
            )
        )

        own_anomaly_id = (
            own_anomaly.id
        )
        other_anomaly_id = (
            other_anomaly.id
        )

        setup_db.commit()
    finally:
        setup_db.close()

    app.dependency_overrides[
        temporal_routes.get_db
    ] = _database_override

    app.dependency_overrides[
        get_current_user
    ] = lambda: _fake_user(
        organization_ids[0]
    )

    try:
        response = client.get(
            "/api/temporal/anomalies",
            params={
                "limit": 200,
                "offset": 0,
                "include_dismissed": "false",
            },
            headers={
                "Authorization":
                    "Bearer test-token",
            },
        )

        assert response.status_code == 200

        payload = response.json()

        anomaly_ids = {
            item["id"]
            for item in payload["data"]
        }

        assert str(
            own_anomaly_id
        ) in anomaly_ids

        assert str(
            other_anomaly_id
        ) not in anomaly_ids

        own_payload = next(
            item
            for item in payload["data"]
            if item["id"]
            == str(
                own_anomaly_id
            )
        )

        assert (
            own_payload[
                "anomaly_type"
            ]
            == "magnitude"
        )
        assert (
            own_payload[
                "description"
            ]
            == "Own anomaly"
        )
        assert (
            own_payload[
                "expected_value"
            ]
            == pytest.approx(
                0.25
            )
        )
        assert (
            own_payload[
                "actual_value"
            ]
            == pytest.approx(
                0.75
            )
        )
        assert (
            own_payload[
                "deviation_score"
            ]
            == pytest.approx(
                2.5
            )
        )
        assert (
            own_payload[
                "significance"
            ]
            == pytest.approx(
                0.91
            )
        )
        assert (
            own_payload[
                "detection_method"
            ]
            == "integration-test"
        )
        assert (
            own_payload[
                "is_confirmed"
            ]
            is False
        )
        assert (
            own_payload[
                "is_dismissed"
            ]
            is False
        )
    finally:
        app.dependency_overrides.clear()

        _cleanup(
            organization_ids
        )