"""Exact temporal-event resolution is tenant-bound and independent of pagination."""
from __future__ import annotations

import os
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from src.api.temporal_routes import get_temporal_event_by_id, get_temporal_events
from src.core.database import SessionLocal
from tests.integration.test_temporal_replay import (
    _make_organization,
    _make_entity,
    _make_page,
    _make_snapshot,
    _make_event,
    _cleanup,
    _utc,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="Requires disposable PostgreSQL integration database.",
)


def test_exact_event_detail_does_not_expose_foreign_tenant():
    db = SessionLocal()
    org_ids = []
    try:
        owner = _make_organization(db, suffix="event-detail-owner")
        other = _make_organization(db, suffix="event-detail-foreign")
        org_ids.extend((owner.id, other.id))
        entity = _make_entity(db, organization_id=owner.id, suffix="event-detail")
        page = _make_page(
            db, organization_id=owner.id, entity_id=entity.id, suffix="event-detail"
        )
        before = _make_snapshot(
            db, organization_id=owner.id, monitored_page_id=page.id,
            suffix="event-detail-before", crawled_at=_utc(1), created_at=_utc(1)
        )
        after = _make_snapshot(
            db, organization_id=owner.id, monitored_page_id=page.id,
            suffix="event-detail-after", crawled_at=_utc(2), created_at=_utc(2)
        )
        event = _make_event(
            db, organization_id=owner.id, monitored_page_id=page.id,
            old_snapshot_id=before.id, new_snapshot_id=after.id, detected_at=_utc(3)
        )
        third = _make_snapshot(
            db, organization_id=owner.id, monitored_page_id=page.id,
            suffix="event-detail-third", crawled_at=_utc(4), created_at=_utc(4)
        )
        latest_event = _make_event(
            db, organization_id=owner.id, monitored_page_id=page.id,
            old_snapshot_id=after.id, new_snapshot_id=third.id, detected_at=_utc(5)
        )
        db.flush()

        newest_window = get_temporal_events(
            db=db, current_user=SimpleNamespace(organization_id=owner.id),
            start=None, end=None, entity_ids=None, limit=1, offset=0, order="desc",
        )
        assert newest_window["total"] == 2
        assert newest_window["data"][0]["id"] == str(latest_event.id)
        oldest_window = get_temporal_events(
            db=db, current_user=SimpleNamespace(organization_id=owner.id),
            start=None, end=None, entity_ids=None, limit=1, offset=0, order="asc",
        )
        assert oldest_window["data"][0]["id"] == str(event.id)

        response = get_temporal_event_by_id(
            event.id, db=db, current_user=SimpleNamespace(organization_id=owner.id)
        )
        assert response["id"] == str(event.id)
        assert response["organization_id"] == str(owner.id)
        assert response["monitored_page_id"] == str(page.id)
        assert response["new_snapshot_id"] == str(after.id)
        assert response["lineage"]["status"] == "unavailable"

        for unauthorized_id in (other.id, uuid.uuid4()):
            with pytest.raises(HTTPException) as denied:
                get_temporal_event_by_id(
                    event.id, db=db,
                    current_user=SimpleNamespace(organization_id=unauthorized_id),
                )
            assert denied.value.status_code == 404

        with pytest.raises(HTTPException) as absent:
            get_temporal_event_by_id(
                uuid.uuid4(), db=db,
                current_user=SimpleNamespace(organization_id=owner.id)
            )
        assert absent.value.status_code == 404
    finally:
        db.rollback()
        if org_ids:
            _cleanup(db, organization_ids=org_ids)
        db.close()
