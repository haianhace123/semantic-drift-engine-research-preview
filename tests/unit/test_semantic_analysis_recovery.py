"""Pure recovery contracts; no live provider or PostgreSQL required."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from src.diff_engine.deepseek_diff import DeepSeekPermanentError, DeepSeekTransientError
from src.services import semantic_analysis_recovery_service as recovery
from scripts import semantic_analysis_recovery_worker as worker

NOW = datetime(2026, 10, 9, 10, 0, tzinfo=timezone.utc)


def _failed(*, attempts=1, error="provider timeout", retryable=True, last=None):
    return SimpleNamespace(
        id=uuid.uuid4(), analysis_status="failed", analysis_attempts=attempts,
        analysis_error=error, analyzed_at=last,
        detected_at=NOW - timedelta(minutes=10),
        semantic_delta_json={"analysis_failure": {"retryable": retryable}},
    )


def test_exponential_backoff_and_attempt_exhaustion():
    event = _failed(last=NOW - timedelta(seconds=59))
    assert not recovery._eligible(event, now=NOW, max_attempts=3, base_retry_seconds=60)
    event.analyzed_at = NOW - timedelta(seconds=60)
    assert recovery._eligible(event, now=NOW, max_attempts=3, base_retry_seconds=60)
    event.analysis_attempts = 2
    assert not recovery._eligible(event, now=NOW, max_attempts=3, base_retry_seconds=60)
    event.analyzed_at = NOW - timedelta(seconds=120)
    assert recovery._eligible(event, now=NOW, max_attempts=3, base_retry_seconds=60)
    event.analysis_attempts = 3
    assert not recovery._eligible(event, now=NOW, max_attempts=3, base_retry_seconds=60)


def test_permanent_failure_and_legacy_oversize_are_not_retried():
    event = _failed(last=NOW - timedelta(days=1), retryable=False)
    assert not recovery._retryable(event)
    assert not recovery._eligible(event, now=NOW, max_attempts=3, base_retry_seconds=0)
    event.semantic_delta_json = {"semantic_analysis": {"analysis_status": "failed"}}
    event.analysis_error = "No partial analysis was performed."
    assert not recovery._retryable(event)
    event.analysis_error = "DeepSeek semantic analysis request failed."
    assert recovery._retryable(event)


def test_failed_status_is_required():
    event = _failed(last=NOW - timedelta(days=1))
    event.analysis_status = "completed"
    assert not recovery._eligible(event, now=NOW, max_attempts=3, base_retry_seconds=0)


def test_advisory_lock_key_is_stable_and_event_specific():
    a = uuid.UUID("11111111-1111-4111-8111-111111111111")
    b = uuid.UUID("22222222-2222-4222-8222-222222222222")
    assert recovery._lock_key(a) == recovery._lock_key(a)
    assert recovery._lock_key(a) != recovery._lock_key(b)
    assert -(2**63) <= recovery._lock_key(a) < 2**63


def test_snapshot_hash_is_verified(tmp_path: Path):
    text = "A clear privacy policy."
    path = tmp_path / "snapshot.txt"
    path.write_text(text, encoding="utf-8")
    import hashlib
    snapshot = SimpleNamespace(
        raw_content_path=str(path),
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
    )
    assert recovery._read_verified(snapshot) == text
    path.write_text("Tampered", encoding="utf-8")
    with pytest.raises(DeepSeekPermanentError, match="hash mismatch"):
        recovery._read_verified(snapshot)


def test_failure_update_retains_nonanalysis_identity_and_removes_completion():
    event = _failed()
    event.semantic_delta_json = {
        "semantic_analysis": {"analysis_status": "success", "changes": []},
        "old_hash": "old", "new_hash": "new",
    }
    event.confidence = 0.9
    event.impact_score = 70
    event.changes_count = 4
    event.summary_text = "Before"
    recovery._record_failure(event, DeepSeekPermanentError("input invalid"))
    assert event.semantic_delta_json["old_hash"] == "old"
    assert event.semantic_delta_json["analysis_failure"]["retryable"] is False
    assert event.analysis_status == "failed"
    assert event.confidence == 0 and event.impact_score == 0


def test_worker_defaults_to_provider_free_dry_run(monkeypatch):
    event_id = uuid.uuid4()
    monkeypatch.setattr(worker, "recovery_candidates", lambda **kwargs: [event_id])
    monkeypatch.setattr(worker, "recover_event", lambda *args, **kwargs: pytest.fail("provider path called"))
    args = worker.build_parser().parse_args(["--limit", "1"])
    assert worker.run_cycle(args) == {"dry_run": 1}


def test_worker_does_not_allow_unapproved_loop():
    with pytest.raises(SystemExit, match="requires --execute"):
        worker.main(["--loop"])


def test_transient_and_permanent_error_types_are_distinct():
    assert issubclass(DeepSeekTransientError, Exception)
    assert not issubclass(DeepSeekTransientError, DeepSeekPermanentError)


def test_permanent_and_transient_failure_metadata_distinction():
    for error, retryable in (
        (DeepSeekPermanentError("bad request"), False),
        (DeepSeekTransientError("timeout"), True),
    ):
        event = _failed()
        event.confidence = 0.4
        event.impact_score = 50
        event.changes_count = 2
        event.summary_text = "Old"
        recovery._record_failure(event, error)
        assert event.semantic_delta_json["analysis_failure"]["retryable"] is retryable
        assert event.analysis_status == "failed"
        assert event.analysis_error == str(error)


def test_requalification_requires_explicit_scoping_and_no_loop():
    for argv in (
        ["--requalify", "--execute"],
        ["--requalify", "--execute", "--event-id", str(uuid.uuid4())],
        ["--requalify", "--execute", "--loop", "--event-id", str(uuid.uuid4()),
         "--organization-id", str(uuid.uuid4())],
    ):
        with pytest.raises(SystemExit, match="--requalify requires"):
            worker.main(argv)
    with pytest.raises(ValueError, match="specific organization"):
        recovery.recover_event(uuid.uuid4(), requalify=True)
