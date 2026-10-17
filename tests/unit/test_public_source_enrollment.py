from __future__ import annotations

import pytest

from src.services.public_source_enrollment_service import (
    SourceEnrollmentError, validate_public_source,
)


def _source(url: str, **kwargs):
    return validate_public_source(
        url=url, page_type=kwargs.get("page_type", "docs"),
        crawl_frequency=kwargs.get("crawl_frequency", "daily"),
        label=kwargs.get("label", "Customer docs"),
    )


def test_enroll_public_domain_normalizes_hostname():
    source = _source("https://DOCS.Example.Org/policies/terms")
    assert source.url == "https://docs.example.org/policies/terms"
    assert source.domain == "docs.example.org"
    assert source.page_type == "docs"


@pytest.mark.parametrize("url", [
    "http://docs.example.org/path",
    "https://127.0.0.1/test",
    "https://[::1]/",
    "https://localhost/",
    "https://service.internal/page",
    "https://user:pass@docs.example.org/path",
    "https://docs.example.org:8443/path",
    "https://docs.example.org/path?token=secret",
    "https://docs.example.org/path#fragment",
    "https://docs.example.org:invalid/path",
    "https://-invalid.example.org/path",
    "https://docs.example.org:65536/path",
    "https://docs.example.org/" + "a" * 2048,
])
def test_rejects_unsafe_public_source_urls(url):
    with pytest.raises(SourceEnrollmentError):
        _source(url)


@pytest.mark.parametrize("url,page_type,domain", [
    ("https://openai.com/policies/terms-of-use", "tos", "openai.com"),
    ("https://openai.com/docs", "docs", "openai.com"),
    ("https://policies.google.com/privacy", "privacy", "policies.google.com"),
    ("https://slack.com/docs", "docs", "slack.com"),
    ("https://google.com/pricing", "pricing", "google.com"),
])
def test_customer_document_identity_not_hostname_adapter(url, page_type, domain):
    source = _source(url, page_type=page_type)
    assert source.domain == domain
    assert source.page_type == page_type


@pytest.mark.parametrize("page_type,frequency", [
    ("unknown", "daily"), ("docs", "every-minute"),
])
def test_invalid_page_type_or_frequency(page_type, frequency):
    with pytest.raises(SourceEnrollmentError):
        _source("https://docs.example.org", page_type=page_type, crawl_frequency=frequency)
