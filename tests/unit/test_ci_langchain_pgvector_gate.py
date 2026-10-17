from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from scripts.ci_langchain_pgvector_gate import (
    REQUIRED_LANGCHAIN_CASES,
    validate_acceptance_report,
)


def _write_report(path, *, cases: int = 6, status: str | None = None):
    """Backward-compatible count-only acceptance used by several gate scripts."""
    suite = ET.Element("testsuite")
    for index in range(cases):
        case = ET.SubElement(suite, "testcase", name=f"test_{index}")
        if index == 0 and status is not None:
            ET.SubElement(case, status)
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)
    return path


def _identified_report(path, *, swap=None, duplicate=None):
    suite = ET.Element("testsuite")
    expected = sorted(REQUIRED_LANGCHAIN_CASES)
    for identity in expected:
        classname, name = identity
        if swap is not None and identity == swap[0]:
            classname, name = swap[1]
        if duplicate is not None and identity == duplicate[0]:
            classname, name = duplicate[1]
        ET.SubElement(suite, "testcase", classname=classname, name=name)
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)
    return path


def test_acceptance_requires_all_six_passed(tmp_path):
    report = _write_report(tmp_path / "junit.xml")
    assert validate_acceptance_report(report) == 6


@pytest.mark.parametrize("status", ["skipped", "failure", "error"])
def test_acceptance_rejects_nonpassing_cases(tmp_path, status):
    report = _write_report(tmp_path / "junit.xml", status=status)
    with pytest.raises(ValueError, match="Acceptance test"):
        validate_acceptance_report(report)


@pytest.mark.parametrize("cases", [0, 5, 7])
def test_acceptance_rejects_missing_or_extra_cases(tmp_path, cases):
    report = _write_report(tmp_path / "junit.xml", cases=cases)
    with pytest.raises(ValueError, match="Expected exactly 6"):
        validate_acceptance_report(report)


def test_acceptance_rejects_malformed_xml(tmp_path):
    report = tmp_path / "junit.xml"
    report.write_text("<testsuite>", encoding="utf-8")
    with pytest.raises(ET.ParseError):
        validate_acceptance_report(report)


def test_six_real_langchain_scenario_identities_are_pinned(tmp_path):
    assert len(REQUIRED_LANGCHAIN_CASES) == 6
    report = _identified_report(tmp_path / "junit.xml")
    assert validate_acceptance_report(report, required_cases=REQUIRED_LANGCHAIN_CASES) == 6


def test_rejects_six_passes_with_one_required_case_substituted(tmp_path):
    victim = sorted(REQUIRED_LANGCHAIN_CASES)[0]
    report = _identified_report(
        tmp_path / "junit.xml", swap=(victim, (victim[0], "test_unrelated_case")),
    )
    with pytest.raises(ValueError, match="identities differ"):
        validate_acceptance_report(report, required_cases=REQUIRED_LANGCHAIN_CASES)


def test_rejects_six_passes_with_duplicate_id_and_missing_scenario(tmp_path):
    first, second = sorted(REQUIRED_LANGCHAIN_CASES)[:2]
    report = _identified_report(tmp_path / "junit.xml", duplicate=(first, second))
    with pytest.raises(ValueError, match="Duplicate acceptance testcase"):
        validate_acceptance_report(report, required_cases=REQUIRED_LANGCHAIN_CASES)


def test_shared_validator_count_only_remains_compatible(tmp_path):
    report = _write_report(tmp_path / "junit.xml", cases=3)
    assert validate_acceptance_report(report, expected_count=3) == 3
