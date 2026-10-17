from __future__ import annotations

import uuid

import pytest

from src.crawler import registry


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://policies.google.com/privacy", "policies.google.com"),
        ("https://slack.com/docs", "slack.com"),
        ("https://openai.com/pricing", "openai.com"),
        ("https://docs.example.org/public/policy", "docs.example.org"),
    ],
)
def test_normalize_hostname_accepts_public_https_document(url: str, expected: str) -> None:
    assert registry.normalize_hostname(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "", "   ", "policies.google.com/privacy",
        "ftp://openai.com/policies/terms-of-use",
        "http://openai.com/policies/terms-of-use",
        "https://user:secret@slack.com/tos",
        "https://slack.com:8080/docs",
        "https://slack.com/docs?access=secret",
        "https://slack.com/docs#section",
        "https://slack.com:invalid/docs",
    ],
)
def test_normalize_hostname_rejects_invalid_url(url: str) -> None:
    with pytest.raises(registry.UnsupportedCrawlerSourceError):
        registry.normalize_hostname(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://slack.com/terms-of-service",
        "https://slack.com/docs",
        "https://policies.google.com/privacy",
        "https://google.com/sla",
        "https://openai.com/policies/terms-of-use",
        "https://openai.com/pricing",
        "https://docs.example.org/privacy",
    ],
)
def test_scheduled_sources_use_one_document_bound_crawler(monkeypatch, url: str) -> None:
    organization_id = uuid.uuid4()
    monitored_page_id = uuid.uuid4()
    claim_token = uuid.uuid4()
    captured: dict[str, object] = {}

    class FakePublicHTMLCrawler:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def crawl(self):
            captured["called"] = True
            return object()

    monkeypatch.setattr(registry, "PublicHTMLCrawler", FakePublicHTMLCrawler)
    result = registry.crawl_monitored_page(
        url=url,
        organization_id=organization_id,
        monitored_page_id=monitored_page_id,
        claim_token=claim_token,
    )
    assert result is not None
    assert captured == {
        "url": url,
        "organization_id": organization_id,
        "monitored_page_id": monitored_page_id,
        "claim_token": claim_token,
        "called": True,
    }


def test_bad_stored_url_never_builds_a_crawler(monkeypatch):
    monkeypatch.setattr(
        registry, "PublicHTMLCrawler",
        lambda **kwargs: pytest.fail("unsafe stored URL must fail before construction"),
    )
    with pytest.raises(registry.UnsupportedCrawlerSourceError):
        registry.build_crawler_runner(
            url="http://localhost/admin",
            organization_id=uuid.uuid4(),
            monitored_page_id=uuid.uuid4(),
        )
