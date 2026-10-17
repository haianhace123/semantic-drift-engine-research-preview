"""Crawl outcomes are claim-fenced, source-safe and never inference of semantic analysis."""

from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

import pytest

from src.crawler import scheduler
from src.crawler.public_html_crawler import PublicHTMLCrawlFailure, PublicHTMLCrawler
from src.crawler.http_transport import CrawlerDestinationError, CrawlerNetworkError
from src.services import crawl_scheduling_service as service


class FakeDB:
    def __init__(self):
        self.page = SimpleNamespace(
            crawl_frequency="hourly", last_crawled_at=None, next_crawl_at=None,
            last_crawl_attempt_at=None, last_crawl_status=None,
            last_crawl_failure_code=None, consecutive_crawl_failures=0,
            crawl_claim_token=uuid.uuid4(), crawl_claimed_at=object(),
            crawl_claim_expires_at=object(),
        )
        self.now = datetime.now(timezone.utc)
        self.flushed = 0

    def execute(self, statement):
        return SimpleNamespace(scalar_one=lambda: self.now)

    def flush(self):
        self.flushed += 1


def test_failure_then_success_resets_outcome_without_creating_semantic_evidence(monkeypatch):
    db = FakeDB()
    monkeypatch.setattr(service, "_lock_claimed_page_for_finalization", lambda *args, **kwargs: db.page)
    page_id = uuid.uuid4()
    token = uuid.uuid4()
    retry_at = service.finalize_crawl_failure(
        db, monitored_page_id=page_id, claim_token=token, failure_code="fetch_failed",
    )
    assert retry_at > db.now
    assert db.page.last_crawled_at is None
    assert db.page.last_crawl_status == "failed"
    assert db.page.last_crawl_failure_code == "fetch_failed"
    assert db.page.last_crawl_attempt_at == db.now
    assert db.page.consecutive_crawl_failures == 1
    assert db.page.crawl_claim_token is None
    service.finalize_crawl_failure(
        db, monitored_page_id=page_id, claim_token=token, failure_code="content_rejected",
    )
    assert db.page.consecutive_crawl_failures == 2
    assert db.page.last_crawl_failure_code == "content_rejected"
    service.finalize_crawl_success(db, monitored_page_id=page_id, claim_token=token)
    assert db.page.last_crawl_status == "succeeded"
    assert db.page.last_crawl_failure_code is None
    assert db.page.consecutive_crawl_failures == 0
    assert db.page.last_crawled_at == db.now


def test_failure_rejects_unknown_or_raw_provider_message_before_db_write():
    db = FakeDB()
    with pytest.raises(service.CrawlSchedulingConfigurationError):
        service.finalize_crawl_failure(
            db, monitored_page_id=uuid.uuid4(), claim_token=uuid.uuid4(),
            failure_code="provider token: sensitive-secret",
        )
    assert db.flushed == 0


@pytest.mark.parametrize("code", ["blocked_destination", "fetch_failed", "content_rejected"])
def test_scheduler_preserves_only_safe_public_crawl_classification(monkeypatch, code):
    claim = service.CrawlClaim(
        monitored_page_id=uuid.uuid4(), organization_id=uuid.uuid4(),
        url="https://docs.example.org/policy", crawl_frequency="daily",
        claim_token=uuid.uuid4(), claimed_at=datetime.now(timezone.utc),
        claim_expires_at=datetime.now(timezone.utc),
    )
    observed = []
    monkeypatch.setattr(scheduler, "_claim_next_page", lambda **kw: claim)
    def fail(**kwargs):
        raise PublicHTMLCrawlFailure(code)
    monkeypatch.setattr(scheduler, "crawl_monitored_page", fail)
    monkeypatch.setattr(scheduler, "_finalize_failure", lambda c, **kw: observed.append((c, kw)))
    result = scheduler.run_crawl_cycle()
    assert result.status == scheduler.CrawlCycleStatus.FAILED
    assert observed == [(claim, {"failure_code": code})]


@pytest.mark.parametrize("error,code", [
    (CrawlerDestinationError("blocked private redirect: no sensitive detail"), "blocked_destination"),
    (CrawlerNetworkError("upstream 503 with private response"), "fetch_failed"),
])
def test_public_crawler_maps_transport_exceptions_without_exposing_message(monkeypatch, error, code):
    from src.crawler import public_html_crawler as public_module
    class FakeSession:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def query(self, *args): return self
        def join(self, *args): return self
        def filter(self, *args): return self
        def one_or_none(self):
            return (
                SimpleNamespace(page_type="docs"),
                SimpleNamespace(name="Docs", domain="docs.example.org", category="Public web"),
            )
    monkeypatch.setattr(public_module, "SessionLocal", FakeSession)
    def broken_fetch(*args, **kwargs): raise error
    monkeypatch.setattr(public_module, "fetch_crawler_html", broken_fetch)
    crawler = PublicHTMLCrawler(
        url="https://docs.example.org/policy", organization_id=uuid.uuid4(),
        monitored_page_id=uuid.uuid4(), claim_token=uuid.uuid4(),
    )
    with pytest.raises(PublicHTMLCrawlFailure) as exc:
        crawler.crawl()
    assert exc.value.code == code
    assert str(exc.value) == code
    assert "private" not in str(exc.value)


def test_detector_exception_after_persisted_snapshot_does_not_retry_crawl(monkeypatch, tmp_path):
    from src.crawler import public_html_crawler as mod
    class FakeSession:
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def query(self, *args): return self
        def join(self, *args): return self
        def filter(self, *args): return self
        def one_or_none(self):
            return (
                SimpleNamespace(page_type="docs"),
                SimpleNamespace(name="Docs", domain="docs.example.org", category="Public web"),
            )
    monkeypatch.setattr(mod, "SessionLocal", FakeSession)
    monkeypatch.setattr(mod, "fetch_crawler_html", lambda *args, **kwargs: "<main>Public contract</main>")
    monkeypatch.setattr(mod.HTMLCleaner, "clean", lambda html: "Public contract content. " * 12)
    snapshot_file = SimpleNamespace(path=str(tmp_path / "persisted.txt"), size_bytes=300)
    monkeypatch.setattr(mod, "persist_snapshot_content", lambda **kwargs: snapshot_file)
    page_id = uuid.uuid4()
    snapshot = SimpleNamespace(monitored_page_id=page_id)
    monkeypatch.setattr(mod, "create_snapshot_record", lambda **kwargs: snapshot)
    def failed_detector(*args): raise RuntimeError("provider crashed after database commit")
    monkeypatch.setattr(mod, "check_and_create_drift", failed_detector)
    crawler = PublicHTMLCrawler(
        url="https://docs.example.org/policy", organization_id=uuid.uuid4(),
        monitored_page_id=page_id, claim_token=uuid.uuid4(),
    )
    assert crawler.crawl() is snapshot
