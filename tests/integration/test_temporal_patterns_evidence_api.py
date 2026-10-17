"""Temporal Pattern listing must distinguish lifecycle from qualified Risk v2 evidence.

Requires RUN_DB_INTEGRATION_TESTS=1 and the integration PostgreSQL database.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import os
import uuid

import pytest
from fastapi.testclient import TestClient

import src.main as main_module
from src.api import temporal_routes
from src.core.database import SessionLocal
from src.core.security import get_current_user
from src.main import app
from src.models.pattern import Pattern
from src.services.persisted_pattern_qualification_service import qualified_pattern_ids

from tests.integration.test_alert_execution_service import (
    _cleanup,
    _make_entity_and_page,
    _make_event,
    _make_organization,
    _make_pattern,
    _make_risk_assessment,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)

client = TestClient(app)
BASE = datetime(2026, 9, 10, tzinfo=timezone.utc)


def _database_override():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def test_temporal_patterns_preserves_paging_and_exposes_qualified_risk_evidence(monkeypatch):
    monkeypatch.setattr(
        main_module, "verify_token", lambda token: {"sub": str(uuid.uuid4())}
    )
    db = SessionLocal()
    own_id = None
    foreign_id = None
    try:
        own = _make_organization(db)
        own_id = own.id
        foreign = _make_organization(db)
        foreign_id = foreign.id
        entity, page = _make_entity_and_page(
            db, organization_id=own_id, suffix="pr3-own"
        )
        valid_event = _make_event(
            db, organization_id=own_id, page=page,
            detected_at=BASE, suffix="pr3-valid",
        )
        pending_event = _make_event(
            db, organization_id=own_id, page=page,
            detected_at=BASE + timedelta(minutes=1), suffix="pr3-pending",
        )
        pending_event.analysis_status = "pending"
        for event in (valid_event, pending_event):
            assessment = _make_risk_assessment(
                db, organization_id=own_id,
                assessed_at=BASE + timedelta(days=1),
                created_at=BASE + timedelta(days=2), score=0.88,
            )
            assessment.semantic_drift_event_id = event.id

        def create_pattern(pattern_type, event_ids, score, *, archived=False):
            row = _make_pattern(
                db, organization_id=own_id, entity_id=entity.id,
                detected_at=BASE + timedelta(days=3),
                created_at=BASE + timedelta(days=4),
            )
            row.pattern_type = pattern_type
            row.event_ids = [str(event_id) for event_id in event_ids]
            row.event_count = len(event_ids)
            row.significance_score = score
            row.is_archived = archived
            return row

        valid = create_pattern("risk_escalation", [valid_event.id], 0.95)
        invalid = create_pattern("risk_escalation", [pending_event.id], 0.85)
        other = create_pattern("repeated_drift", [], 0.70)
        archived = create_pattern("risk_escalation", [valid_event.id], 0.40, archived=True)
        foreign_pattern = Pattern(
            organization_id=foreign_id,
            name="Foreign risk pattern",
            pattern_type="risk_escalation",
            event_ids=[str(valid_event.id)],
            significance_score=1.0,
            is_active=True,
            is_archived=False,
        )
        db.add(foreign_pattern)
        db.commit()

        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            id=uuid.uuid4(), organization_id=own_id,
            email="pr3-pattern-read@example.com",
        )
        app.dependency_overrides[temporal_routes.get_db] = _database_override
        headers = {"Authorization": "Bearer pr3-pattern-read-test-token"}
        observed_batches = []

        def recording_qualification(db_session, *, organization_id, patterns):
            observed_batches.append((organization_id, tuple(item.id for item in patterns)))
            return qualified_pattern_ids(
                db_session, organization_id=organization_id, patterns=patterns
            )

        monkeypatch.setattr(temporal_routes, "qualified_pattern_ids", recording_qualification)

        first = client.get("/api/temporal/patterns?limit=2", headers=headers)
        assert first.status_code == 200, first.text
        first_data = first.json()
        assert (first_data["total"], first_data["offset"], first_data["limit"]) == (3, 0, 2)
        assert [row["id"] for row in first_data["data"]] == [str(valid.id), str(invalid.id)]
        assert [row["risk_evidence_qualified"] for row in first_data["data"]] == [True, False]
        assert all(row["is_active"] is True for row in first_data["data"])
        assert all(row["is_archived"] is False for row in first_data["data"])
        assert observed_batches[-1] == (own_id, (valid.id, invalid.id))

        second = client.get("/api/temporal/patterns?limit=2&offset=2", headers=headers)
        assert second.status_code == 200, second.text
        second_data = second.json()
        assert second_data["total"] == 3
        assert second_data["offset"] == 2
        assert [(row["id"], row["risk_evidence_qualified"]) for row in second_data["data"]] == [
            (str(other.id), None)
        ]
        assert observed_batches[-1] == (own_id, (other.id,))

        history = client.get(
            "/api/temporal/patterns?active_only=false&pattern_type=risk_escalation",
            headers=headers,
        )
        assert history.status_code == 200, history.text
        history_data = history.json()
        assert history_data["total"] == 3
        statuses = {row["id"]: row["risk_evidence_qualified"] for row in history_data["data"]}
        assert statuses == {str(valid.id): True, str(invalid.id): False, str(archived.id): True}
        assert next(row for row in history_data["data"] if row["id"] == str(archived.id))["is_archived"] is True
        assert str(foreign_pattern.id) not in statuses
        assert db.query(Pattern).filter(Pattern.organization_id == own_id).count() == 4
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(temporal_routes.get_db, None)
        try:
            _cleanup(db, foreign_id)
            _cleanup(db, own_id)
        finally:
            db.close()
