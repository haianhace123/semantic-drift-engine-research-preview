"""Contract tests for gap admission and provider-free reconnaissance."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from scripts import semantic_analysis_recovery_worker as worker
from src.services import semantic_analysis_recovery_service as recovery

NOW = datetime(2026, 10, 10, tzinfo=timezone.utc)


def _pending(*, marker="snapshot_gap", attempts=0, age=180):
    return SimpleNamespace(
        analysis_status="pending", analysis_attempts=attempts,
        semantic_delta_json={"analysis_recovery": {"source": marker}},
        analysis_error=None, analyzed_at=NOW - timedelta(seconds=age),
        detected_at=NOW - timedelta(hours=1),
    )


def test_pending_only_admitted_for_registered_snapshot_gap():
    assert recovery._eligible(_pending(), now=NOW, max_attempts=3, base_retry_seconds=60)
    assert not recovery._eligible(_pending(marker="other"), now=NOW,
                                  max_attempts=3, base_retry_seconds=60)
    assert not recovery._eligible(_pending(attempts=1, age=59), now=NOW,
                                  max_attempts=3, base_retry_seconds=60)
    assert recovery._eligible(_pending(attempts=1, age=60), now=NOW,
                              max_attempts=3, base_retry_seconds=60)
    assert not recovery._eligible(_pending(attempts=3), now=NOW,
                                  max_attempts=3, base_retry_seconds=60)


def test_reconciliation_scans_are_bounded():
    for limit, scan in ((0, 10), (11, 10), (1, 10001)):
        with pytest.raises(ValueError, match="bounds"):
            recovery.missing_snapshot_pair_candidates(limit=limit, scan_limit=scan)


def test_worker_opt_in_dry_run_never_registers(monkeypatch):
    monkeypatch.setattr(worker, "missing_snapshot_pair_candidates", lambda **kw: [object()])
    monkeypatch.setattr(worker, "reconcile_missing_snapshot_pairs", lambda **kw: pytest.fail("mutated during dry run"))
    monkeypatch.setattr(worker, "recovery_candidates", lambda **kw: [])
    args = worker.build_parser().parse_args(["--reconcile-missing"])
    assert worker.run_cycle(args) == {"gap_dry_run": 1}


def test_worker_reconcile_registers_before_recovering(monkeypatch):
    event = uuid4()
    steps = []
    monkeypatch.setattr(worker, "reconcile_missing_snapshot_pairs", lambda **kw: (steps.append("register") or [event]))
    monkeypatch.setattr(worker, "recovery_candidates", lambda **kw: (steps.append("candidates") or [event]))
    monkeypatch.setattr(worker, "recover_event", lambda *a, **kw: (steps.append("recover") or SimpleNamespace(outcome="completed",event_id=str(event), attempts=1)))
    args = worker.build_parser().parse_args(["--execute", "--reconcile-missing"])
    assert worker.run_cycle(args) == {"gap_registered": 1, "completed": 1}
    assert steps == ["register", "candidates", "recover"]


def test_worker_rejects_unbounded_gap_scan():
    with pytest.raises(SystemExit, match="Invalid recovery"):
        worker.main(["--gap-scan-limit", "10001"])


def test_explicit_event_recovery_cannot_silently_register_other_gaps():
    with pytest.raises(SystemExit, match="cannot be combined"):
        worker.main(["--reconcile-missing", "--event-id", str(uuid4())])
