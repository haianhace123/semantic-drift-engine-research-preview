"""A live source probe must match the production crawler without mutating state."""

from __future__ import annotations

import json

import pytest

from scripts import probe_public_html_source as probe
from src.crawler.http_transport import CrawlerDestinationError, CrawlerNetworkError


URL = "https://docs.example.org/policy"


def test_dry_run_does_not_fetch(monkeypatch, capsys):
    monkeypatch.setattr(probe, "fetch_crawler_html", lambda *a, **k: pytest.fail("network call"))
    assert probe.main(["--url", URL]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "dry_run"
    assert report["network_requested"] is False
    assert report["provider_calls"] is False
    assert report["database_writes"] is False


@pytest.mark.parametrize("url", [
    "http://docs.example.org/policy",
    "https://127.0.0.1/a",
    "https://docs.example.org/a?token=secret",
    "https://user:pass@docs.example.org/",
    "https://docs.example.org:8443/policy",
    "https://docs.example.org/policy#section",
])
def test_bad_urls_rejected_without_network(monkeypatch, url):
    monkeypatch.setattr(probe, "fetch_crawler_html", lambda *a, **k: pytest.fail("network call"))
    assert probe.main(["--url", url, "--execute"]) == 2


def test_provider_domain_is_eligible_for_non_network_probe(monkeypatch, capsys):
    monkeypatch.setattr(probe, "fetch_crawler_html", lambda *a, **k: pytest.fail("network call"))
    assert probe.main(["--url", "https://policies.google.com/privacy"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["hostname"] == "policies.google.com"
    assert report["network_requested"] is False


def test_production_rejects_network_call(monkeypatch, capsys):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr(probe, "fetch_crawler_html", lambda *a, **k: pytest.fail("network call"))
    assert probe.main(["--url", URL, "--execute"]) == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "non_development_environment"


def test_execute_matches_crawler_transport_and_extraction(monkeypatch):
    from src.crawler.public_html_crawler import MAX_HTML_BYTES
    html = "<html><body><main><p>" + "Policy content. " * 12 + "</p></main></body></html>"
    captures = []
    def fake_fetch(url, **kwargs):
        captures.append((url, kwargs))
        return html
    monkeypatch.setattr(probe, "fetch_crawler_html", fake_fetch)
    report = probe.execute_probe(url=URL, samples=1, interval_seconds=5, environment="test")
    assert report["outcome"] == "eligible"
    assert report["content_stable_between_samples"] is None
    assert len(report["samples"][0]["normalized_sha256"]) == 64
    assert captures[0][1]["allowed_hosts"] == {"docs.example.org"}
    assert captures[0][1]["max_response_bytes"] == MAX_HTML_BYTES
    assert captures[0][1]["timeout_seconds"] == 30.0
    assert "Policy content." not in json.dumps(report)


def test_two_samples_detect_changed_normalized_text(monkeypatch):
    docs = iter([
        "<main>" + "Existing rights. " * 12 + "</main>",
        "<main>" + "Changed rights. " * 12 + "</main>",
    ])
    monkeypatch.setattr(probe, "fetch_crawler_html", lambda *a, **k: next(docs))
    delays = []
    monkeypatch.setattr(probe.time, "sleep", delays.append)
    report = probe.execute_probe(url=URL, samples=2, interval_seconds=5, environment="development")
    assert report["content_stable_between_samples"] is False
    assert delays == [5]


def test_identical_extractions_have_identical_hashes(monkeypatch):
    monkeypatch.setattr(probe, "fetch_crawler_html", lambda *a, **k: "<main>" + "Stable policy. " * 12 + "</main>")
    monkeypatch.setattr(probe.time, "sleep", lambda _: None)
    report = probe.execute_probe(url=URL, samples=2, interval_seconds=2, environment="test")
    assert report["content_stable_between_samples"] is True


@pytest.mark.parametrize("bad_html", [
    pytest.param("<main>tiny</main>", id="too-short"),
    pytest.param(
        "<main>" + ("x" * (probe.MAX_EXTRACTED_CHARACTERS + 1)) + "</main>",
        id="above-max-extracted-characters",
    ),
])
def test_extraction_limits_match_crawler(monkeypatch, bad_html):
    monkeypatch.setattr(probe, "fetch_crawler_html", lambda *a, **k: bad_html)
    with pytest.raises(probe.PilotProbeError, match="content_rejected"):
        probe.capture_public_sample(URL)


@pytest.mark.parametrize("error, expected", [
    (CrawlerDestinationError("private upstream"), "blocked_destination"),
    (CrawlerNetworkError("upstream secret"), "fetch_failed"),
])
def test_upstream_errors_are_sanitized(monkeypatch, error, expected, capsys):
    monkeypatch.setattr(probe, "fetch_crawler_html", lambda *a, **k: (_ for _ in ()).throw(error))
    assert probe.main(["--url", URL, "--execute"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["reason"] == expected
    assert "upstream" not in json.dumps(report)


def test_no_short_interval_or_wrong_environment(monkeypatch):
    monkeypatch.setattr(probe, "fetch_crawler_html", lambda *a, **k: pytest.fail("network call"))
    with pytest.raises(probe.PilotProbeError, match="sample_interval_too_short"):
        probe.execute_probe(url=URL, samples=2, interval_seconds=0, environment="test")
    with pytest.raises(probe.PilotProbeError, match="non_development_environment"):
        probe.execute_probe(url=URL, samples=1, interval_seconds=5, environment="production")


def test_unexpected_failure_is_sanitized(monkeypatch, capsys):
    def fail(*args, **kwargs):
        raise RuntimeError("sensitive response content")
    monkeypatch.setattr(probe, "fetch_crawler_html", fail)
    assert probe.main(["--url", URL, "--execute"]) == 3
    output = capsys.readouterr().out
    assert json.loads(output)["reason"] == "probe_internal_error"
    assert "sensitive" not in output
