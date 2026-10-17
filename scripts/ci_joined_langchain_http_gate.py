"""Strict joined real LangChain/PGVector -> event/worker -> HTTP admission gate.

Runs ONLY inside the disposable integration Compose project. Never catches
missing SDK/dependencies as a pass or accepts skipped tests.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlsplit

from scripts.ci_langchain_pgvector_gate import validate_acceptance_report
from scripts.ci_real_lineage_admission_gate import require_disposable_lineage_databases

JOINED_MODULE = "tests/integration/test_joined_langchain_http_admission.py"
EXPECTED_CASES = 1


def require_joined_disposable_environment() -> None:
    require_disposable_lineage_databases()
    downstream_url = urlsplit(os.environ.get("SDE_JOINED_DOWNSTREAM_DATABASE_URL", ""))
    if not (
        downstream_url.scheme == "postgresql"
        and downstream_url.hostname == "integration_downstream_db"
        and downstream_url.port == 5432
        and downstream_url.username == "downstream_test"
        and downstream_url.password == "downstream_test"
        and downstream_url.path == "/downstream_test"
        and not downstream_url.query
        and not downstream_url.fragment
    ):
        raise ValueError("Joined acceptance requires the disposable downstream_test database")


def main() -> int:
    try:
        require_joined_disposable_environment()
    except ValueError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="sde-joined-sdk-http-") as temp:
        report = Path(temp) / "report.xml"
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--tb=short", JOINED_MODULE,
             f"--junitxml={report}"],
            check=False,
        )
        if result.returncode:
            print("Joined SDK -> HTTP acceptance failed.", file=sys.stderr)
            return 1
        try:
            validated = validate_acceptance_report(report, expected_count=EXPECTED_CASES)
        except (OSError, ValueError, ET.ParseError) as exc:
            print(f"REFUSED: incomplete or skipped joined acceptance: {exc}", file=sys.stderr)
            return 1
        print(f"SDE_JOINED_LANGCHAIN_HTTP_ACCEPTANCE_PASSED: {validated} passed, 0 skipped")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
