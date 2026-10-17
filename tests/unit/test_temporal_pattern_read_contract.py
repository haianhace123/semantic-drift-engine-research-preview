"""Read-only temporal Pattern contract stays distinct from stored lifecycle."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
import uuid

from src.api import temporal_routes


def _pattern(org, kind, *, active=True, archived=False):
    return SimpleNamespace(
        id=uuid.uuid4(), organization_id=org,
        name="Persisted pattern", description="Historical detection",
        pattern_type=kind, event_ids=[], entities=[], categories=[],
        event_count=0, entity_count=0, significance_score=0.5,
        time_span_days=0, first_event_at=None, last_event_at=None,
        summary="Historical detection",
        detected_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
        is_active=active, is_archived=archived,
    )


def test_pattern_read_model_adds_evidence_qualification_without_filtering(monkeypatch):
    org = uuid.uuid4()
    qualified = _pattern(org, "risk_escalation")
    unqualified = _pattern(org, "risk_escalation")
    other = _pattern(org, "repeated_drift")
    patterns = [qualified, unqualified, other]

    db = MagicMock()
    query = db.query.return_value
    query.filter.return_value = query
    query.order_by.return_value = query
    query.offset.return_value = query
    query.limit.return_value = query
    query.count.return_value = 3
    query.all.return_value = patterns
    calls = []

    def qualification(db_arg, *, organization_id, patterns):
        calls.append((db_arg, organization_id, tuple(patterns)))
        return frozenset({qualified.id})

    monkeypatch.setattr(temporal_routes, "qualified_pattern_ids", qualification)
    result = temporal_routes.get_patterns(
        db=db,
        current_user=SimpleNamespace(organization_id=org),
        limit=2,
        offset=1,
        pattern_type=None,
        active_only=True,
    )
    assert result["total"] == 3
    assert result["limit"] == 2
    assert result["offset"] == 1
    assert [item["risk_evidence_qualified"] for item in result["data"]] == [True, False, None]
    assert all(item["is_active"] for item in result["data"])
    assert calls == [(db, org, tuple(patterns))]


def test_pattern_evidence_remains_separate_from_archived_lifecycle(monkeypatch):
    org = uuid.uuid4()
    historical = _pattern(org, "risk_escalation", active=False, archived=True)
    db = MagicMock()
    query = db.query.return_value
    query.filter.return_value = query
    query.order_by.return_value = query
    query.offset.return_value = query
    query.limit.return_value = query
    query.count.return_value = 1
    query.all.return_value = [historical]
    monkeypatch.setattr(
        temporal_routes, "qualified_pattern_ids",
        lambda db_arg, *, organization_id, patterns: frozenset({historical.id}),
    )
    result = temporal_routes.get_patterns(
        db=db, current_user=SimpleNamespace(organization_id=org),
        limit=50, offset=0, pattern_type=None, active_only=False,
    )
    item = result["data"][0]
    assert item["risk_evidence_qualified"] is True
    assert item["is_active"] is False
    assert item["is_archived"] is True
