"""CI contract: run every named mandatory PostgreSQL/Redis runtime scenario.

This is a bounded runtime suite, not certification of every integration test.
When a mandatory scenario is changed, review and update its identity explicitly.
Adding unrelated passing tests must never conceal a missing required scenario.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path


EXPECTED_CASES = {
    "test_crawler_scheduler_runtime": frozenset({
        "test_success_cycle_commits_claim_before_crawl_and_finalizes",
        "test_none_snapshot_persists_failure_retry",
        "test_crawler_exception_persists_failure_retry",
    }),
    "test_email_runtime": frozenset({
        "test_email_producer_and_worker_share_redis_stream_contract",
    }),
    "test_lineage_invalidation_execution_discovery_service": frozenset({
        "test_no_authorized_intent_returns_none",
        "test_never_attempted_intent_is_discovered",
        "test_unexpired_latest_attempt_is_not_discovered",
        "test_expired_latest_attempt_without_result_is_discovered",
        "test_terminal_latest_attempt_is_not_discovered[succeeded]",
        "test_terminal_latest_attempt_is_not_discovered[failed]",
        "test_discovery_uses_latest_attempt",
        "test_oldest_authorized_claimable_intent_is_discovered",
        "test_organization_filter_isolates_discovery",
        "test_discovery_batch_returns_candidates_in_authorization_order",
        "test_discovery_batch_respects_limit",
        "test_expired_attempt_within_retry_cooldown_is_not_discovered",
        "test_expired_attempt_after_retry_cooldown_is_discovered",
    }),
    "test_lineage_invalidation_execution_lifecycle": frozenset({
        "test_execution_lifecycle_commits_side_effect_then_terminal_success",
        "test_execution_lifecycle_recovers_after_crash_between_delete_and_result",
        "test_targeted_invalidation_only_deletes_causally_affected_target",
        "test_execution_worker_autonomously_executes_authorized_intent_end_to_end",
        "test_execution_cycle_executes_authorized_intent_end_to_end",
        "test_execution_cycle_fails_closed_when_document_revision_advanced",
        "test_execution_cycle_is_idle_without_authorized_intent",
        "test_execution_cycle_does_not_terminalize_connection_resolution_failure",
        "test_execution_cycle_does_not_terminalize_ambiguous_downstream_commit",
        "test_execution_cycle_recovers_after_result_publication_failure",
        "test_retry_preserves_new_document_revision_after_publication_failure",
        "test_historical_unsafe_attempt_recovers_after_safe_binding_upgrade",
        "test_execution_cycle_terminalizes_definite_downstream_failure",
        "test_execution_cycle_skips_unclaimable_oldest_candidate",
        "test_execution_cycle_pages_past_full_unclaimable_batch",
        "test_execution_cycle_rejects_unrelated_physical_document_binding",
    }),
}
EXPECTED_MODULE_COUNTS = {module: len(names) for module, names in EXPECTED_CASES.items()}
RUNTIME_MODULES = tuple(f"tests/integration/{name}.py" for name in EXPECTED_CASES)


def validate_runtime_report(report: Path) -> dict[str, int]:
    """Reject substituted, duplicated, missing, skipped or failed mandatory cases."""
    root = ET.parse(report).getroot()
    if root.tag not in {"testsuite", "testsuites"}:
        raise ValueError("Invalid JUnit report root")

    # Pytest can report collection errors at suite level without a testcase.
    for suite in root.iter("testsuite"):
        for field in ("errors", "failures", "skipped"):
            if int(suite.get(field, "0")) > 0:
                raise ValueError(f"JUnit suite reports {field}")

    seen: dict[str, set[str]] = {module: set() for module in EXPECTED_CASES}
    totals: Counter[str] = Counter()
    for case in root.iter("testcase"):
        name = case.get("name", "")
        classname = case.get("classname", "")
        if not classname.startswith("tests.integration."):
            raise ValueError(f"Unexpected runtime testcase class: {classname}")
        module = classname.removeprefix("tests.integration.")
        if module not in EXPECTED_CASES:
            raise ValueError(f"Unexpected runtime test module: {classname}")
        if not name.startswith("test_"):
            raise ValueError(f"Unexpected JUnit testcase name: {name}")
        if name in seen[module]:
            raise ValueError(f"Duplicate runtime testcase: {module}.{name}")
        seen[module].add(name)
        if any(case.find(tag) is not None for tag in ("skipped", "failure", "error")):
            raise ValueError(f"Runtime test did not pass: {module}.{name}")
        totals[module] += 1

    for module, required in EXPECTED_CASES.items():
        missing = required - seen[module]
        if missing:
            raise ValueError(f"Required runtime testcase missing: {module}.{sorted(missing)[0]}")
    return dict(totals)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="sde-runtime-ci-") as temporary:
        report = Path(temporary) / "junit.xml"
        command = [
            sys.executable, "-m", "pytest", "-q", "--strict-markers",
            *RUNTIME_MODULES, f"--junitxml={report}",
        ]
        result = subprocess.run(command, check=False)
        if result.returncode:
            print(f"Runtime tests failed with pytest exit {result.returncode}", file=sys.stderr)
            return 1
        try:
            module_counts = validate_runtime_report(report)
        except (OSError, ValueError, ET.ParseError) as exc:
            print(f"Runtime test acceptance rejected: {exc}", file=sys.stderr)
            return 1
        print("SDE_CI_RUNTIME_TEST_GATE_PASSED")
        print(f"PASSED_CASES: {sum(module_counts.values())}")
        print("SKIPPED_CASES: 0")
        print("MANDATORY_CASE_IDENTITIES: VERIFIED")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
