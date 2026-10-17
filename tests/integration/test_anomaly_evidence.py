from __future__ import annotations

from datetime import (
    datetime,
    timedelta,
    timezone,
)
import uuid

import pytest

from src.core.database import SessionLocal
from src.models.entity import Entity
from src.models.monitored_page import MonitoredPage
from src.models.organization import Organization
from src.models.page_snapshot import PageSnapshot
from src.models.semantic_drift_event import SemanticDriftEvent
from src.services.anomaly_evidence_service import (
    load_anomaly_evidence,
)


pytestmark = pytest.mark.skipif(
    not __import__("os").getenv(
        "RUN_DB_INTEGRATION_TESTS"
    ),
    reason=(
        "RUN_DB_INTEGRATION_TESTS is not enabled"
    ),
)


BASE = datetime(
    2026,
    9,
    1,
    12,
    0,
    tzinfo=timezone.utc,
)


def _slug(
    prefix: str,
) -> str:
    return (
        f"{prefix}-"
        f"{uuid.uuid4().hex}"
    )


def _create_organization(
    db,
    *,
    prefix: str,
):
    organization = Organization(
        id=uuid.uuid4(),
        name=f"Anomaly Evidence {prefix}",
        slug=_slug(
            prefix
        ),
        billing_email=(
            f"{prefix}-"
            f"{uuid.uuid4().hex[:12]}"
            "@example.com"
        ),
    )

    db.add(
        organization
    )
    db.flush()

    return organization


def _create_entity_and_page(
    db,
    *,
    organization,
    suffix: str,
):
    entity = Entity(
        organization_id=organization.id,
        name=f"Entity {suffix}",
        domain=(
            f"{suffix.lower()}-"
            f"{uuid.uuid4().hex}.example.com"
        ),
        category="technology",
    )

    db.add(
        entity
    )
    db.flush()

    page = MonitoredPage(
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

    return entity, page


def _create_event(
    db,
    *,
    organization,
    page,
    detected_at: datetime,
    suffix: str,
):
    old_snapshot = PageSnapshot(
        organization_id=organization.id,
        monitored_page_id=page.id,
        content_hash=(
            f"old-{suffix}-"
            f"{uuid.uuid4().hex}"
        ),
        raw_content=f"old {suffix}",
        content_size=len(
            f"old {suffix}"
        ),
        crawled_at=(
            detected_at
            - timedelta(
                minutes=1
            )
        ),
    )

    new_snapshot = PageSnapshot(
        organization_id=organization.id,
        monitored_page_id=page.id,
        content_hash=(
            f"new-{suffix}-"
            f"{uuid.uuid4().hex}"
        ),
        raw_content=f"new {suffix}",
        content_size=len(
            f"new {suffix}"
        ),
        crawled_at=detected_at,
    )

    db.add_all(
        [
            old_snapshot,
            new_snapshot,
        ]
    )
    db.flush()

    event = SemanticDriftEvent(
        organization_id=organization.id,
        monitored_page_id=page.id,
        old_snapshot_id=old_snapshot.id,
        new_snapshot_id=new_snapshot.id,
        semantic_delta_json={
            "test": suffix,
        },
        impact_score=50,
        detected_at=detected_at,
    )

    db.add(
        event
    )
    db.flush()

    return event


def test_anomaly_evidence_partitions_temporal_boundaries():
    db = SessionLocal()

    try:
        organization = _create_organization(
            db,
            prefix="anomaly-boundary",
        )

        entity, page = (
            _create_entity_and_page(
                db,
                organization=organization,
                suffix="Boundary",
            )
        )

        as_of = BASE

        current_start = (
            as_of
            - timedelta(
                days=7
            )
        )

        baseline_start = (
            current_start
            - timedelta(
                days=28
            )
        )

        _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=(
                baseline_start
                - timedelta(
                    seconds=1
                )
            ),
            suffix="before-baseline",
        )

        baseline_edge = _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=baseline_start,
            suffix="baseline-edge",
        )

        historical = _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=(
                current_start
                - timedelta(
                    seconds=1
                )
            ),
            suffix="historical",
        )

        current_edge = _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=current_start,
            suffix="current-edge",
        )

        current = _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=(
                as_of
                - timedelta(
                    hours=1
                )
            ),
            suffix="current",
        )

        as_of_edge = _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=as_of,
            suffix="as-of-edge",
        )

        _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=(
                as_of
                + timedelta(
                    seconds=1
                )
            ),
            suffix="future",
        )

        result = load_anomaly_evidence(
            db,
            organization_id=organization.id,
            as_of=as_of,
            baseline_days=28,
            window_days=7,
        )

        assert result.organization_id == (
            organization.id
        )

        assert result.baseline_start == (
            baseline_start
        )

        assert result.current_start == (
            current_start
        )

        assert len(
            result.pages
        ) == 1

        page_evidence = result.pages[0]

        assert (
            page_evidence.monitored_page_id
            == page.id
        )

        assert (
            page_evidence.entity_id
            == entity.id
        )

        assert [
            item.event_id
            for item
            in page_evidence.historical_events
        ] == [
            baseline_edge.id,
            historical.id,
        ]

        assert [
            item.event_id
            for item
            in page_evidence.current_events
        ] == [
            current_edge.id,
            current.id,
            as_of_edge.id,
        ]

        assert (
            result.historical_event_count
            == 2
        )

        assert (
            result.current_event_count
            == 3
        )

    finally:
        db.rollback()
        db.close()


def test_anomaly_evidence_is_tenant_isolated():
    db = SessionLocal()

    try:
        first_org = _create_organization(
            db,
            prefix="anomaly-tenant-a",
        )

        second_org = _create_organization(
            db,
            prefix="anomaly-tenant-b",
        )

        first_entity, first_page = (
            _create_entity_and_page(
                db,
                organization=first_org,
                suffix="TenantA",
            )
        )

        _, second_page = (
            _create_entity_and_page(
                db,
                organization=second_org,
                suffix="TenantB",
            )
        )

        first_event = _create_event(
            db,
            organization=first_org,
            page=first_page,
            detected_at=(
                BASE
                - timedelta(
                    days=1
                )
            ),
            suffix="tenant-a",
        )

        _create_event(
            db,
            organization=second_org,
            page=second_page,
            detected_at=(
                BASE
                - timedelta(
                    days=1
                )
            ),
            suffix="tenant-b",
        )

        result = load_anomaly_evidence(
            db,
            organization_id=first_org.id,
            as_of=BASE,
        )

        assert len(
            result.pages
        ) == 1

        page_evidence = result.pages[0]

        assert (
            page_evidence.monitored_page_id
            == first_page.id
        )

        assert (
            page_evidence.entity_id
            == first_entity.id
        )

        assert [
            item.event_id
            for item
            in page_evidence.current_events
        ] == [
            first_event.id
        ]

        assert (
            result.historical_event_count
            == 0
        )

        assert (
            result.current_event_count
            == 1
        )

    finally:
        db.rollback()
        db.close()


def test_future_event_does_not_leak():
    db = SessionLocal()

    try:
        organization = _create_organization(
            db,
            prefix="anomaly-future",
        )

        _, page = (
            _create_entity_and_page(
                db,
                organization=organization,
                suffix="Future",
            )
        )

        visible_event = _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=(
                BASE
                - timedelta(
                    hours=1
                )
            ),
            suffix="visible",
        )

        _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=(
                BASE
                + timedelta(
                    seconds=1
                )
            ),
            suffix="future",
        )

        result = load_anomaly_evidence(
            db,
            organization_id=organization.id,
            as_of=BASE,
        )

        assert len(
            result.pages
        ) == 1

        assert [
            item.event_id
            for item
            in result.pages[0].current_events
        ] == [
            visible_event.id
        ]

    finally:
        db.rollback()
        db.close()


def test_anomaly_evidence_groups_pages_independently():
    db = SessionLocal()

    try:
        organization = _create_organization(
            db,
            prefix="anomaly-pages",
        )

        first_entity, first_page = (
            _create_entity_and_page(
                db,
                organization=organization,
                suffix="PageA",
            )
        )

        second_entity, second_page = (
            _create_entity_and_page(
                db,
                organization=organization,
                suffix="PageB",
            )
        )

        first_historical = _create_event(
            db,
            organization=organization,
            page=first_page,
            detected_at=(
                BASE
                - timedelta(
                    days=10
                )
            ),
            suffix="page-a-history",
        )

        first_current = _create_event(
            db,
            organization=organization,
            page=first_page,
            detected_at=(
                BASE
                - timedelta(
                    days=1
                )
            ),
            suffix="page-a-current",
        )

        second_current = _create_event(
            db,
            organization=organization,
            page=second_page,
            detected_at=(
                BASE
                - timedelta(
                    days=2
                )
            ),
            suffix="page-b-current",
        )

        result = load_anomaly_evidence(
            db,
            organization_id=organization.id,
            as_of=BASE,
        )

        assert len(
            result.pages
        ) == 2

        by_page = {
            item.monitored_page_id: item
            for item in result.pages
        }

        first = by_page[
            first_page.id
        ]

        second = by_page[
            second_page.id
        ]

        assert (
            first.entity_id
            == first_entity.id
        )

        assert [
            item.event_id
            for item
            in first.historical_events
        ] == [
            first_historical.id
        ]

        assert [
            item.event_id
            for item
            in first.current_events
        ] == [
            first_current.id
        ]

        assert (
            second.entity_id
            == second_entity.id
        )

        assert (
            second.historical_events
            == ()
        )

        assert [
            item.event_id
            for item
            in second.current_events
        ] == [
            second_current.id
        ]

    finally:
        db.rollback()
        db.close()


def test_anomaly_evidence_loader_is_read_only():
    db = SessionLocal()

    try:
        organization = _create_organization(
            db,
            prefix="anomaly-read-only",
        )

        _, page = (
            _create_entity_and_page(
                db,
                organization=organization,
                suffix="ReadOnly",
            )
        )

        _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=(
                BASE
                - timedelta(
                    days=1
                )
            ),
            suffix="read-only",
        )

        db.flush()

        assert not db.new
        assert not db.dirty
        assert not db.deleted

        load_anomaly_evidence(
            db,
            organization_id=organization.id,
            as_of=BASE,
        )

        assert not db.new
        assert not db.dirty
        assert not db.deleted

    finally:
        db.rollback()
        db.close()