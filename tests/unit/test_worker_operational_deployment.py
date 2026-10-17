"""Deployment expectation must not hide recent worker failures."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.models.worker_operational_state import WORKER_KIND_EMAIL
from src.services.worker_operational_read_service import get_worker_operational_overview


NOW = datetime(2026, 10, 6, 10, 0, tzinfo=timezone.utc)


def _overview(*, seconds_since_heartbeat=None, failures=0):
    states = []
    if seconds_since_heartbeat is not None:
        states.append(SimpleNamespace(
            worker_kind=WORKER_KIND_EMAIL,
            worker_instance_id="email-1",
            started_at=NOW - timedelta(minutes=10),
            last_heartbeat_at=NOW - timedelta(seconds=seconds_since_heartbeat),
            last_cycle_started_at=None,
            last_cycle_completed_at=None,
            last_success_at=None,
            consecutive_failures=failures,
            last_failure_at=None,
            last_error="cycle failed" if failures else None,
        ))

    query = Mock()
    query.filter.return_value = query
    query.order_by.return_value = query
    query.all.return_value = [
        item for item in states
        if item.last_heartbeat_at >= NOW - timedelta(minutes=5)
    ]
    db = Mock()
    db.query.return_value = query
    overview = get_worker_operational_overview(db, now=NOW)
    return next(w for w in overview.workers if w.worker_kind == WORKER_KIND_EMAIL)


def test_disabled_email_with_no_history(monkeypatch):
    monkeypatch.setenv("SDE_EMAIL_WORKER_EXPECTED", "false")
    email = _overview()
    assert email.status == "disabled"
    assert email.expected is False


def test_required_email_without_heartbeat_is_unavailable(monkeypatch):
    monkeypatch.setenv("SDE_EMAIL_WORKER_EXPECTED", "true")
    email = _overview()
    assert email.status == "unavailable"
    assert email.expected is True


def test_unexpected_active_email_still_visible(monkeypatch):
    monkeypatch.setenv("SDE_EMAIL_WORKER_EXPECTED", "false")
    email = _overview(seconds_since_heartbeat=5)
    assert email.expected is False
    assert email.status == "healthy"
    assert email.fresh_instance_count == 1


def test_unexpected_email_failure_still_degraded(monkeypatch):
    monkeypatch.setenv("SDE_EMAIL_WORKER_EXPECTED", "false")
    assert _overview(seconds_since_heartbeat=5, failures=2).status == "degraded"


def test_recent_stale_email_not_suppressed(monkeypatch):
    monkeypatch.setenv("SDE_EMAIL_WORKER_EXPECTED", "false")
    email = _overview(seconds_since_heartbeat=45)
    assert email.status == "unavailable"
    assert email.recent_stale_instance_count == 1


def test_old_stale_email_is_disabled_after_concern_window(monkeypatch):
    monkeypatch.setenv("SDE_EMAIL_WORKER_EXPECTED", "false")
    email = _overview(seconds_since_heartbeat=301)
    assert email.status == "disabled"
    assert email.instances == ()


@pytest.mark.parametrize("value", ["", "yes", "1", "off", "enabled"])
def test_bad_email_expectation_fails_closed(monkeypatch, value):
    monkeypatch.setenv("SDE_EMAIL_WORKER_EXPECTED", value)
    with pytest.raises(ValueError, match="SDE_EMAIL_WORKER_EXPECTED"):
        _overview()


def test_boolean_expected_parser_normalizes_case(monkeypatch):
    monkeypatch.setenv("SDE_EMAIL_WORKER_EXPECTED", " TRUE ")
    assert _overview().expected is True


def test_disabled_worker_response_contains_expected_flag(monkeypatch):
    from src.api.worker_operations_responses import worker_operations_response
    from src.services.worker_operational_read_service import WorkerOperationalOverview

    monkeypatch.setenv("SDE_EMAIL_WORKER_EXPECTED", "false")
    email = _overview()
    payload = worker_operations_response(WorkerOperationalOverview(
        observed_at=NOW,
        heartbeat_stale_after_seconds=30,
        concern_window_seconds=300,
        workers=(email,),
    )).model_dump(mode="json")
    assert payload["workers"][0]["status"] == "disabled"
    assert payload["workers"][0]["expected"] is False
