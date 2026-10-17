from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from unittest.mock import Mock

from src.crawler import scheduler
from src.crawler.scheduler import CrawlCycleStatus
from src.services.crawl_scheduling_service import CrawlClaim
from src.services.crawl_scheduling_service import CrawlClaimAuthorityError


def _claim() -> CrawlClaim:
    now = datetime.now(
        timezone.utc
    )

    return CrawlClaim(
        monitored_page_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        url="https://policies.google.com/privacy",
        crawl_frequency="daily",
        claim_token=uuid.uuid4(),
        claimed_at=now,
        claim_expires_at=now,
    )


def test_idle_cycle_does_not_execute_or_finalize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed = False
    finalized = False

    def fake_claim(
        **kwargs,
    ):
        return None

    def fake_crawl(**kwargs):
        nonlocal executed
        executed = True

    def fake_finalize(claim):
        nonlocal finalized
        finalized = True

    monkeypatch.setattr(
        scheduler,
        "_claim_next_page",
        fake_claim,
    )

    monkeypatch.setattr(
        scheduler,
        "crawl_monitored_page",
        fake_crawl,
    )

    monkeypatch.setattr(
        scheduler,
        "_finalize_success",
        fake_finalize,
    )

    result = scheduler.run_crawl_cycle()

    assert result.status == CrawlCycleStatus.IDLE
    assert result.claim is None
    assert executed is False
    assert finalized is False


def test_successful_cycle_uses_exact_claim_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim()
    captured: dict[str, object] = {}
    finalized: list[CrawlClaim] = []

    monkeypatch.setattr(
        scheduler,
        "_claim_next_page",
        lambda **kwargs: claim,
    )

    def fake_crawl(
        *,
        url,
        organization_id,
        monitored_page_id,
        claim_token,
    ):
        captured["url"] = url
        captured["organization_id"] = organization_id
        captured["monitored_page_id"] = monitored_page_id
        captured["claim_token"] = claim_token
        return object()

    monkeypatch.setattr(
        scheduler,
        "crawl_monitored_page",
        fake_crawl,
    )

    monkeypatch.setattr(
        scheduler,
        "_finalize_success",
        finalized.append,
    )

    monkeypatch.setattr(
        scheduler,
        "_finalize_failure",
        lambda claim: pytest.fail(
            "Successful crawl must not finalize failure."
        ),
    )

    result = scheduler.run_crawl_cycle()

    assert result.status == CrawlCycleStatus.SUCCEEDED
    assert result.claim == claim

    assert captured == {
        "url": claim.url,
        "organization_id": claim.organization_id,
        "monitored_page_id": claim.monitored_page_id,
        "claim_token": claim.claim_token,
    }

    assert finalized == [
        claim
    ]


def test_none_snapshot_finalizes_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim()
    finalized: list[CrawlClaim] = []

    monkeypatch.setattr(
        scheduler,
        "_claim_next_page",
        lambda **kwargs: claim,
    )

    monkeypatch.setattr(
        scheduler,
        "crawl_monitored_page",
        lambda **kwargs: None,
    )

    monkeypatch.setattr(
        scheduler,
        "_finalize_failure",
        finalized.append,
    )

    monkeypatch.setattr(
        scheduler,
        "_finalize_success",
        lambda claim: pytest.fail(
            "Failed crawl must not finalize success."
        ),
    )

    result = scheduler.run_crawl_cycle()

    assert result.status == CrawlCycleStatus.FAILED
    assert result.claim == claim
    assert finalized == [
        claim
    ]


def test_superseded_claim_does_not_crash_failure_finalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim()
    monkeypatch.setattr(scheduler, "_claim_next_page", lambda **kwargs: claim)
    monkeypatch.setattr(scheduler, "crawl_monitored_page", lambda **kwargs: None)

    def lost_claim(*args, **kwargs):
        raise CrawlClaimAuthorityError("claim expired")

    # The production helper catches a claim-authority error without
    # propagating; simulate its called persistence authority instead.
    monkeypatch.setattr(scheduler, "finalize_crawl_failure", lost_claim)
    result = scheduler.run_crawl_cycle()
    assert result.status == CrawlCycleStatus.FAILED
    assert result.claim == claim


def test_superseded_claim_cannot_report_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim()
    monkeypatch.setattr(scheduler, "_claim_next_page", lambda **kwargs: claim)
    monkeypatch.setattr(scheduler, "crawl_monitored_page", lambda **kwargs: object())

    def lost_claim(*args, **kwargs):
        raise CrawlClaimAuthorityError("claim superseded")

    monkeypatch.setattr(scheduler, "_finalize_success", lost_claim)
    result = scheduler.run_crawl_cycle()
    assert result.status == CrawlCycleStatus.FAILED
    assert result.claim == claim


def test_crawler_exception_finalizes_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim()
    finalized: list[CrawlClaim] = []

    monkeypatch.setattr(
        scheduler,
        "_claim_next_page",
        lambda **kwargs: claim,
    )

    def fail_crawl(**kwargs):
        raise RuntimeError(
            "crawler failed"
        )

    monkeypatch.setattr(
        scheduler,
        "crawl_monitored_page",
        fail_crawl,
    )

    monkeypatch.setattr(
        scheduler,
        "_finalize_failure",
        lambda claim, **kwargs: finalized.append(claim),
    )

    result = scheduler.run_crawl_cycle()

    assert result.status == CrawlCycleStatus.FAILED
    assert result.claim == claim
    assert finalized == [
        claim
    ]


def test_failure_finalization_error_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim()

    monkeypatch.setattr(
        scheduler,
        "_claim_next_page",
        lambda **kwargs: claim,
    )

    monkeypatch.setattr(
        scheduler,
        "crawl_monitored_page",
        lambda **kwargs: None,
    )

    def fail_finalize(
        claim,
    ):
        raise RuntimeError(
            "finalization failed"
        )

    monkeypatch.setattr(
        scheduler,
        "_finalize_failure",
        fail_finalize,
    )

    with pytest.raises(
        RuntimeError,
        match="finalization failed",
    ):
        scheduler.run_crawl_cycle()


def test_success_finalization_error_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim()

    monkeypatch.setattr(
        scheduler,
        "_claim_next_page",
        lambda **kwargs: claim,
    )

    monkeypatch.setattr(
        scheduler,
        "crawl_monitored_page",
        lambda **kwargs: object(),
    )

    def fail_finalize(
        claim,
    ):
        raise RuntimeError(
            "finalization failed"
        )

    monkeypatch.setattr(
        scheduler,
        "_finalize_success",
        fail_finalize,
    )

    with pytest.raises(
        RuntimeError,
        match="finalization failed",
    ):
        scheduler.run_crawl_cycle()


def test_cycle_forwards_organization_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    organization_id = uuid.uuid4()
    captured: dict[str, object] = {}

    def fake_claim(
        **kwargs,
    ):
        captured.update(
            kwargs
        )

        return None

    monkeypatch.setattr(
        scheduler,
        "_claim_next_page",
        fake_claim,
    )

    result = scheduler.run_crawl_cycle(
        organization_id=organization_id,
    )

    assert result.status == CrawlCycleStatus.IDLE
    assert captured == {
        "organization_id": organization_id,
    }


def test_scheduler_rejects_non_positive_poll_seconds() -> None:
    with pytest.raises(
        scheduler.CrawlSchedulerConfigurationError
    ):
        scheduler.CrawlScheduler(
            poll_seconds=0,
        )


def test_scheduler_waits_when_cycle_is_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = scheduler.CrawlScheduler(
        poll_seconds=5,
    )

    calls = 0

    def fake_cycle():
        nonlocal calls

        calls += 1

        if calls == 1:
            return scheduler.CrawlCycleResult(
                status=scheduler.CrawlCycleStatus.IDLE,
            )

        worker.stop()

        return scheduler.CrawlCycleResult(
            status=scheduler.CrawlCycleStatus.IDLE,
        )

    waits: list[float] = []

    def fake_wait(
        timeout: float,
    ) -> bool:
        waits.append(
            timeout
        )

        return False

    monkeypatch.setattr(
        scheduler,
        "run_crawl_cycle",
        fake_cycle,
    )

    monkeypatch.setattr(
        worker.stop_event,
        "wait",
        fake_wait,
    )

    worker.run()

    assert calls == 2
    assert waits == [
        5,
    ]


def test_scheduler_does_not_wait_after_completed_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = scheduler.CrawlScheduler(
        poll_seconds=5,
    )

    calls = 0

    def fake_cycle():
        nonlocal calls

        calls += 1

        if calls == 1:
            return scheduler.CrawlCycleResult(
                status=(
                    scheduler.CrawlCycleStatus.SUCCEEDED
                ),
            )

        worker.stop()

        return scheduler.CrawlCycleResult(
            status=scheduler.CrawlCycleStatus.IDLE,
        )

    waits: list[float] = []

    def fake_wait(
        timeout: float,
    ) -> bool:
        waits.append(
            timeout
        )

        return False

    monkeypatch.setattr(
        scheduler,
        "run_crawl_cycle",
        fake_cycle,
    )

    monkeypatch.setattr(
        worker.stop_event,
        "wait",
        fake_wait,
    )

    worker.run()

    assert calls == 2
    assert waits == []


def test_scheduler_stop_interrupts_idle_wait() -> None:
    worker = scheduler.CrawlScheduler(
        poll_seconds=60,
    )

    worker.stop()

    assert worker.stop_event.is_set()


def test_scheduler_propagates_unexpected_cycle_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = scheduler.CrawlScheduler(
        poll_seconds=5,
    )

    def fail_cycle():
        raise RuntimeError(
            "scheduler failure"
        )

    monkeypatch.setattr(
        scheduler,
        "run_crawl_cycle",
        fail_cycle,
    )

    with pytest.raises(
        RuntimeError,
        match="scheduler failure",
    ):
        worker.run()


def test_cycle_isolates_unsupported_source_after_failure_finalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claim = _claim()

    finalized: list[
        scheduler.CrawlClaim
    ] = []

    monkeypatch.setattr(
        scheduler,
        "_claim_next_page",
        lambda **kwargs: claim,
    )

    def fail_crawl(
        **kwargs,
    ):
        raise scheduler.UnsupportedCrawlerSourceError(
            "unsupported source"
        )

    monkeypatch.setattr(
        scheduler,
        "crawl_monitored_page",
        fail_crawl,
    )

    monkeypatch.setattr(
        scheduler,
        "_finalize_failure",
        lambda claim, **kwargs: finalized.append(claim),
    )

    result = scheduler.run_crawl_cycle()

    assert result.status == CrawlCycleStatus.FAILED
    assert result.claim == claim
    assert finalized == [
        claim,
    ]


def test_scheduler_logs_successful_cycle_without_idle_noise(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    worker = scheduler.CrawlScheduler(
        poll_seconds=5,
    )
    claim = _claim()
    calls = 0

    def fake_cycle():
        nonlocal calls

        calls += 1

        if calls == 1:
            return scheduler.CrawlCycleResult(
                status=scheduler.CrawlCycleStatus.SUCCEEDED,
                claim=claim,
            )

        worker.stop()

        return scheduler.CrawlCycleResult(
            status=scheduler.CrawlCycleStatus.IDLE,
        )

    monkeypatch.setattr(
        scheduler,
        "run_crawl_cycle",
        fake_cycle,
    )

    with caplog.at_level(
        "INFO",
        logger=scheduler.__name__,
    ):
        worker.run()

    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == scheduler.__name__
    ]

    assert len(messages) == 1
    assert "status=succeeded" in messages[0]
    assert f"organization_id={claim.organization_id}" in messages[0]
    assert f"monitored_page_id={claim.monitored_page_id}" in messages[0]
    assert "duration_ms=" in messages[0]


def test_scheduler_logs_failed_cycle_as_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    worker = scheduler.CrawlScheduler(
        poll_seconds=5,
    )
    claim = _claim()

    def fake_cycle():
        worker.stop()

        return scheduler.CrawlCycleResult(
            status=scheduler.CrawlCycleStatus.FAILED,
            claim=claim,
        )

    monkeypatch.setattr(
        scheduler,
        "run_crawl_cycle",
        fake_cycle,
    )

    with caplog.at_level(
        "WARNING",
        logger=scheduler.__name__,
    ):
        worker.run()

    records = [
        record
        for record in caplog.records
        if record.name == scheduler.__name__
    ]

    assert len(records) == 1
    assert records[0].levelname == "WARNING"
    assert "status=failed" in records[0].getMessage()
    assert "duration_ms=" in records[0].getMessage()


def test_scheduler_reports_successful_operational_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reporter = Mock()
    worker = scheduler.CrawlScheduler(
        poll_seconds=5,
        operational_reporter=reporter,
    )

    def fake_cycle():
        worker.stop()

        return scheduler.CrawlCycleResult(
            status=scheduler.CrawlCycleStatus.SUCCEEDED,
        )

    monkeypatch.setattr(
        scheduler,
        "run_crawl_cycle",
        fake_cycle,
    )

    worker.run()

    reporter.cycle_started.assert_called_once_with()
    reporter.cycle_succeeded.assert_called_once_with()
    reporter.cycle_failed.assert_not_called()


def test_scheduler_reports_failed_crawl_without_process_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reporter = Mock()
    worker = scheduler.CrawlScheduler(
        poll_seconds=5,
        operational_reporter=reporter,
    )

    def fake_cycle():
        worker.stop()

        return scheduler.CrawlCycleResult(
            status=CrawlCycleStatus.FAILED,
        )

    monkeypatch.setattr(
        scheduler,
        "run_crawl_cycle",
        fake_cycle,
    )

    worker.run()

    reporter.cycle_started.assert_called_once_with()
    reporter.cycle_failed.assert_called_once_with(
        "crawler_page_crawl_failed"
    )
    reporter.cycle_succeeded.assert_not_called()


def test_scheduler_reports_idle_cycle_as_operational_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reporter = Mock()
    worker = scheduler.CrawlScheduler(
        poll_seconds=5,
        operational_reporter=reporter,
    )

    def fake_cycle():
        worker.stop()
        return scheduler.CrawlCycleResult(
            status=CrawlCycleStatus.IDLE,
        )

    monkeypatch.setattr(
        scheduler,
        "run_crawl_cycle",
        fake_cycle,
    )

    worker.run()

    reporter.cycle_started.assert_called_once_with()
    reporter.cycle_succeeded.assert_called_once_with()
    reporter.cycle_failed.assert_not_called()


def test_scheduler_continues_with_next_page_after_failed_crawl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reporter = Mock()
    worker = scheduler.CrawlScheduler(
        poll_seconds=5,
        operational_reporter=reporter,
    )
    statuses = [CrawlCycleStatus.FAILED, CrawlCycleStatus.SUCCEEDED]

    def fake_cycle():
        status = statuses.pop(0)
        if not statuses:
            worker.stop()
        return scheduler.CrawlCycleResult(status=status)

    monkeypatch.setattr(scheduler, "run_crawl_cycle", fake_cycle)

    worker.run()

    assert statuses == []
    assert reporter.cycle_started.call_count == 2
    reporter.cycle_failed.assert_called_once_with(
        "crawler_page_crawl_failed"
    )
    reporter.cycle_succeeded.assert_called_once_with()


def test_scheduler_reports_unexpected_operational_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reporter = Mock()
    worker = scheduler.CrawlScheduler(
        poll_seconds=5,
        operational_reporter=reporter,
    )

    error = RuntimeError("scheduler failure")

    monkeypatch.setattr(
        scheduler,
        "run_crawl_cycle",
        Mock(side_effect=error),
    )

    with pytest.raises(
        RuntimeError,
        match="scheduler failure",
    ):
        worker.run()

    reporter.cycle_started.assert_called_once_with()
    reporter.cycle_failed.assert_called_once_with(error)
    reporter.cycle_succeeded.assert_not_called()