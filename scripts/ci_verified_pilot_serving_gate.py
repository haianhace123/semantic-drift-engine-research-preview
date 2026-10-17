"""Strict real-stack verified-pilot test gate; requires disposable Compose DBs."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from scripts.ci_langchain_pgvector_gate import validate_acceptance_report
from scripts.ci_real_lineage_admission_gate import require_disposable_lineage_databases

EXPECTED_CASES = 5
TEST = "tests/integration/test_verified_pilot_serving_real.py"


def main() -> int:
    try:
        require_disposable_lineage_databases()
        from tests.integration.test_lineage_invalidation_execution_lifecycle import DOWNSTREAM_DATABASE_URL
        url = urlsplit(DOWNSTREAM_DATABASE_URL)
        if not (url.scheme in {"postgresql", "postgresql+psycopg", "postgresql+psycopg2"}
                and url.hostname == "integration_downstream_db" and url.port == 5432
                and url.username == "downstream_test" and url.password == "downstream_test"
                and url.path == "/downstream_test" and not url.query and not url.fragment):
            raise ValueError("Non-disposable PGVector database")
    except (ValueError, RuntimeError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="sde-verified-real-") as tmp:
        report = Path(tmp) / "pytest.xml"
        result = subprocess.run([
            sys.executable, "-B", "-m", "pytest", "-q", "-rs", "--tb=short",
            "-p", "no:cacheprovider", TEST, f"--junitxml={report}",
        ], check=False)
        if result.returncode != 0:
            print("Real verified-pilot serving tests failed", file=sys.stderr)
            return 1
        try:
            count = validate_acceptance_report(report, expected_count=EXPECTED_CASES)
        except (ValueError, OSError) as exc:
            print(f"Invalid acceptance report: {exc}", file=sys.stderr)
            return 1
        print(f"PASS: real verified-pilot admission: {count} passed, 0 skipped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
