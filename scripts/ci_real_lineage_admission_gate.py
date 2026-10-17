"""Strict persisted repair/event-to-HTTP acceptance on disposable databases."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit

from scripts.ci_langchain_pgvector_gate import validate_acceptance_report


ACCEPTANCE_MODULES = (
    "tests/integration/test_lineage_physical_recovery_acceptance.py",
    "tests/integration/test_temporal_event_http_admission_acceptance.py",
)
EXPECTED_CASES = 3  # two repair paths and one event-to-HTTP enforcement path
MATERIALITY_MODULES = ("tests/integration/test_temporal_lineage_invalidation_flow.py",)
EXPECTED_MATERIALITY_CASES = 2


def require_disposable_lineage_databases() -> None:
    """Refuse to run integration code against the application database."""
    database_url = urlsplit(os.environ.get("DATABASE_URL", ""))
    redis_url = urlsplit(os.environ.get("REDIS_URL", ""))
    api_url = urlsplit(os.environ.get("SDE_LINEAGE_ACCEPTANCE_API_URL", ""))
    if not (
        os.environ.get("RUN_DB_INTEGRATION_TESTS") == "1"
        and os.environ.get("ENVIRONMENT") == "test"
        and database_url.scheme in {"postgresql", "postgresql+psycopg2"}
        and database_url.hostname == "db"
        and database_url.port == 5432
        and database_url.path == "/sde_integration"
        and database_url.username == "sde_integration"
        and database_url.password == "sde_local_test_only"
        and not database_url.query
        and not database_url.fragment
        and redis_url.scheme == "redis"
        and redis_url.hostname == "redis"
        and redis_url.port == 6379
        and redis_url.path == "/0"
        and api_url.scheme == "http"
        and api_url.hostname == "api"
        and api_url.port == 8000
        and api_url.path in {"", "/"}
        and not api_url.username
        and not api_url.password
        and not api_url.query
        and not api_url.fragment
    ):
        raise ValueError(
            "Refusing write-capable acceptance outside the disposable "
            "sde_integration PostgreSQL, Redis and internal HTTP environment."
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--materiality-only",
        action="store_true",
        help="Run both materiality integration cases before the HTTP API starts.",
    )
    args = parser.parse_args(argv)
    modules = MATERIALITY_MODULES if args.materiality_only else ACCEPTANCE_MODULES
    expected = EXPECTED_MATERIALITY_CASES if args.materiality_only else EXPECTED_CASES

    try:
        require_disposable_lineage_databases()
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="sde-lineage-admission-") as temp:
        report = Path(temp) / "report.xml"
        result = subprocess.run(
            [
                sys.executable, "-m", "pytest", "-q", "--tb=short",
                *modules, f"--junitxml={report}",
            ],
            check=False,
        )
        if result.returncode:
            print("Persisted lineage/admission acceptance failed.", file=sys.stderr)
            return 1
        try:
            count = validate_acceptance_report(report, expected_count=expected)
        except (OSError, ValueError, ET.ParseError) as exc:
            print(f"Persisted lineage acceptance gate failed: {exc}", file=sys.stderr)
            return 1
        if args.materiality_only:
            print(f"PASS: Isolated materiality: {count} passed, 0 skipped.")
        else:
            print(f"Persisted lineage/event-to-HTTP admission: {count} passed, 0 skipped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
