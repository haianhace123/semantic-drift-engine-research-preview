"""Tenant-facing operations must not expose deployment process details."""

from datetime import datetime, timezone
from types import SimpleNamespace

from src.api.worker_operations_responses import worker_operations_response
from src.api.routes import operations as operations_routes


NOW = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)


def _overview():
    instance = SimpleNamespace(
        worker_instance_id="private-node-01",
        status="degraded",
        started_at=NOW,
        last_heartbeat_at=NOW,
        last_cycle_started_at=NOW,
        last_cycle_completed_at=NOW,
        last_success_at=None,
        consecutive_failures=2,
        last_failure_at=NOW,
        last_error="SECRET_SENTINEL database-url/password=PRIVATE",
    )
    worker = SimpleNamespace(
        worker_kind="vector",
        status="degraded",
        expected=True,
        fresh_instance_count=1,
        degraded_instance_count=1,
        recent_stale_instance_count=0,
        instances=(instance,),
    )
    return SimpleNamespace(
        observed_at=NOW,
        heartbeat_stale_after_seconds=30,
        concern_window_seconds=300,
        workers=(worker,),
    )


def _assert_tenant_safe(response):
    payload = response.model_dump(mode="json")
    worker = payload["workers"][0]
    assert worker["worker_kind"] == "vector"
    assert worker["status"] == "degraded"
    assert worker["degraded_instance_count"] == 1
    assert worker["fresh_instance_count"] == 1
    assert worker["instances"] == []
    serialized = response.model_dump_json()
    assert "SECRET_SENTINEL" not in serialized
    assert "private-node-01" not in serialized
    assert "database-url" not in serialized


def test_admin_projection_excludes_deployment_instances_and_errors():
    _assert_tenant_safe(worker_operations_response(_overview()))


def test_admin_route_uses_tenant_safe_projection(monkeypatch):
    monkeypatch.setattr(
        operations_routes, "get_worker_operational_overview",
        lambda db: _overview(),
    )
    response = operations_routes.get_worker_operations(
        user=object(), db=object(),
    )
    _assert_tenant_safe(response)
