"""Enforce a complete, non-skipped LangChain/PGVector acceptance run in CI."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path


ACCEPTANCE_MODULES = (
    "tests/integration/test_lineage_causal_chunk_langchain_pgvector.py",
    "tests/integration/test_temporal_knowledge_integrity_langchain_pgvector.py",
)
EXPECTED_TEST_COUNT = 6
# Exact IDs of the six production-facing LangChain/PGVector cases.
# Other consumers of validate_acceptance_report keep their own count contract.
REQUIRED_LANGCHAIN_CASES = frozenset({
    ("tests.integration.test_lineage_causal_chunk_langchain_pgvector",
     "test_real_langchain_pgvector_materialization_executes_causally"),
    ("tests.integration.test_temporal_knowledge_integrity_langchain_pgvector",
     "test_index_preparation_closes_provenance_write_boundary"),
    ("tests.integration.test_temporal_knowledge_integrity_langchain_pgvector",
     "test_pgvector_round_trip_preserves_exact_provenance"),
    ("tests.integration.test_temporal_knowledge_integrity_langchain_pgvector",
     "test_pgvector_storage_identity_is_not_causal_revision"),
    ("tests.integration.test_temporal_knowledge_integrity_langchain_pgvector",
     "test_pgvector_accepts_missing_revision_but_sde_fails_closed"),
    ("tests.integration.test_temporal_knowledge_integrity_langchain_pgvector",
     "test_pgvector_preserves_historical_chunk_revisions"),
})


def validate_acceptance_report(
    report: Path, *, expected_count: int = EXPECTED_TEST_COUNT,
    required_cases: frozenset[tuple[str, str]] | None = None,
) -> int:
    """Reject missing cases, failures, and skips (including importorskip)."""
    root = ET.parse(report).getroot()
    if root.tag not in {"testsuite", "testsuites"}:
        raise ValueError(f"Unexpected JUnit report root: {root.tag}")

    cases = list(root.iter("testcase"))
    if len(cases) != expected_count:
        raise ValueError(
            f"Expected exactly {expected_count} acceptance tests, got {len(cases)}."
        )

    identities: set[tuple[str, str]] = set()
    for case in cases:
        name = case.get("name", "<unnamed>")
        if required_cases is not None:
            identity = (case.get("classname", ""), name)
            if identity in identities:
                raise ValueError(f"Duplicate acceptance testcase: {identity}")
            identities.add(identity)
        if case.find("skipped") is not None:
            raise ValueError(f"Acceptance test was skipped: {name}")
        if case.find("failure") is not None or case.find("error") is not None:
            raise ValueError(f"Acceptance test failed: {name}")

    if required_cases is not None and identities != required_cases:
        missing = required_cases - identities
        unexpected = identities - required_cases
        raise ValueError(
            f"Acceptance testcase identities differ: missing={len(missing)}, "
            f"unexpected={len(unexpected)}"
        )
    return len(cases)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="sde-langchain-acceptance-") as temporary:
        report = Path(temporary) / "junit.xml"
        command = [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--tb=short",
            *ACCEPTANCE_MODULES,
            f"--junitxml={report}",
        ]
        result = subprocess.run(command, check=False)
        if result.returncode:
            print(f"LangChain acceptance pytest exited {result.returncode}.", file=sys.stderr)
            return 1
        try:
            count = validate_acceptance_report(report, required_cases=REQUIRED_LANGCHAIN_CASES)
        except (OSError, ValueError, ET.ParseError) as exc:
            print(f"LangChain acceptance gate failed: {exc}", file=sys.stderr)
            return 1

        print(f"LangChain/PGVector acceptance: {count} passed, 0 skipped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
