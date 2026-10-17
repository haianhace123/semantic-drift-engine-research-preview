"""Read-only pilot probe for a public HTML source through the production crawler boundary.

Dry-run by default. Explicit execution contacts the public URL but does not
create monitored pages, snapshots, semantic events or provider requests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from typing import Any
from urllib.parse import urlsplit

from src.crawler.http_transport import (
    CrawlerDestinationError,
    CrawlerNetworkError,
    fetch_crawler_html,
)
from src.crawler.public_html_crawler import (
    MAX_EXTRACTED_CHARACTERS,
    MAX_HTML_BYTES,
)
from src.parser.html_cleaner import HTMLCleaner
from src.services.public_source_enrollment_service import (
    SourceEnrollmentError,
    validate_public_source,
)

MIN_EXTRACTED_CHARACTERS = 100
MIN_SAMPLE_INTERVAL_SECONDS = 2


class PilotProbeError(ValueError):
    """Expected operator-facing validation or admission failure."""


def validate_probe_url(url: str) -> str:
    """Use the exact admission rules of scheduled public HTML monitoring."""
    try:
        normalized = validate_public_source(
            url=url, page_type="docs", crawl_frequency="daily", label=None,
        )
    except SourceEnrollmentError as exc:
        raise PilotProbeError("source_not_eligible") from exc
    return normalized.url


def capture_public_sample(url: str) -> dict[str, Any]:
    """Reuse production fetch, normalization and size limits; do not persist text."""
    hostname = urlsplit(url).hostname
    if hostname is None:
        raise PilotProbeError("source_not_eligible")
    try:
        html = fetch_crawler_html(
            url,
            allowed_hosts={hostname},
            headers={
                "Accept": "text/html, application/xhtml+xml",
                "Accept-Encoding": "identity",
            },
            timeout_seconds=30.0,
            max_response_bytes=MAX_HTML_BYTES,
        )
    except CrawlerDestinationError as exc:
        raise PilotProbeError("blocked_destination") from exc
    except CrawlerNetworkError as exc:
        raise PilotProbeError("fetch_failed") from exc

    content = HTMLCleaner.clean(html)
    if not MIN_EXTRACTED_CHARACTERS <= len(content) <= MAX_EXTRACTED_CHARACTERS:
        raise PilotProbeError("content_rejected")
    return {
        "normalized_characters": len(content),
        "normalized_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


def plan_probe(*, url: str, samples: int) -> dict[str, Any]:
    if samples not in (1, 2):
        raise PilotProbeError("invalid_sample_count")
    normalized_url = validate_probe_url(url)
    return {
        "mode": "dry_run",
        "hostname": urlsplit(normalized_url).hostname,
        "samples_requested": samples,
        "production_max_html_bytes": MAX_HTML_BYTES,
        "production_min_characters": MIN_EXTRACTED_CHARACTERS,
        "production_max_characters": MAX_EXTRACTED_CHARACTERS,
        "network_requested": False,
        "database_writes": False,
        "provider_calls": False,
    }


def execute_probe(
    *, url: str, samples: int, interval_seconds: int, environment: str,
) -> dict[str, Any]:
    plan = plan_probe(url=url, samples=samples)
    normalized_url = validate_probe_url(url)
    if environment.lower() not in {"development", "test"}:
        raise PilotProbeError("non_development_environment")
    if samples == 2 and interval_seconds < MIN_SAMPLE_INTERVAL_SECONDS:
        raise PilotProbeError("sample_interval_too_short")
    measurements: list[dict[str, Any]] = []
    for idx in range(samples):
        if idx:
            time.sleep(interval_seconds)
        sample = capture_public_sample(normalized_url)
        measurements.append(sample)

    return {
        "mode": "executed",
        "hostname": plan["hostname"],
        "outcome": "eligible",
        "samples": measurements,
        "content_stable_between_samples": (
            measurements[0]["normalized_sha256"] == measurements[1]["normalized_sha256"]
            if len(measurements) == 2 else None
        ),
        "database_writes": False,
        "provider_calls": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True, help="Authorized public HTTPS document URL")
    parser.add_argument("--execute", action="store_true", help="Opt in to outbound requests")
    parser.add_argument("--samples", type=int, choices=(1, 2), default=1)
    parser.add_argument("--interval-seconds", type=int, default=5)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.execute:
            report = execute_probe(
                url=args.url, samples=args.samples,
                interval_seconds=args.interval_seconds,
                environment=os.environ.get("ENVIRONMENT", "development"),
            )
        else:
            report = plan_probe(url=args.url, samples=args.samples)
        print(json.dumps(report, sort_keys=True))
        return 0
    except PilotProbeError as exc:
        # Deliberately no upstream HTML, HTTP headers, exception details or
        # user document content in operational output.
        print(json.dumps({"outcome": "not_eligible", "reason": str(exc)}, sort_keys=True))
        return 2
    except Exception:
        # Unexpected failures must not disclose response data or transport internals.
        print(json.dumps({"outcome": "not_eligible", "reason": "probe_internal_error"}, sort_keys=True))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
