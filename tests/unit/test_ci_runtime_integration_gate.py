"""Runtime CI gate must verify scenario identity, not just the case count."""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from scripts.ci_runtime_integration_gate import (
    EXPECTED_CASES,
    EXPECTED_MODULE_COUNTS,
    validate_runtime_report,
)


def _report(path, *, omit=None, replace=None, duplicate=None, failing=None, bad_tag="skipped"):
    root = ET.Element("testsuite")
    for module, names in EXPECTED_CASES.items():
        for name in sorted(names):
            if (module, name) == omit:
                continue
            actual_name = replace[1] if replace and (module, name) == replace[0] else name
            case = ET.SubElement(
                root, "testcase", classname=f"tests.integration.{module}",
                name=actual_name,
            )
            if failing == (module, name):
                ET.SubElement(case, bad_tag)
            if duplicate == (module, name):
                ET.SubElement(
                    root, "testcase", classname=f"tests.integration.{module}", name=name,
                )
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
    return path


def _first_case(module):
    return module, sorted(EXPECTED_CASES[module])[0]


def test_all_33_mandatory_cases_have_exact_identity(tmp_path):
    report = _report(tmp_path / "results.xml")
    counts = validate_runtime_report(report)
    assert counts == EXPECTED_MODULE_COUNTS
    assert sum(counts.values()) == 33


@pytest.mark.parametrize("bad_tag", ["skipped", "failure", "error"])
def test_rejects_nonpassing_case_even_with_pytest_exit_zero(tmp_path, bad_tag):
    victim = _first_case("test_email_runtime")
    report = _report(tmp_path / "results.xml", failing=victim, bad_tag=bad_tag)
    with pytest.raises(ValueError, match="did not pass"):
        validate_runtime_report(report)


@pytest.mark.parametrize("module", list(EXPECTED_CASES))
def test_rejects_missing_required_identity_in_every_module(tmp_path, module):
    report = _report(tmp_path / "results.xml", omit=_first_case(module))
    with pytest.raises(ValueError, match="Required runtime testcase missing"):
        validate_runtime_report(report)


def test_rejects_substitution_even_when_total_count_is_33(tmp_path):
    victim = _first_case("test_lineage_invalidation_execution_lifecycle")
    report = _report(tmp_path / "results.xml", replace=(victim, "test_unrelated_replacement"))
    with pytest.raises(ValueError, match="Required runtime testcase missing"):
        validate_runtime_report(report)


def test_rejects_duplicated_case_even_when_total_count_is_33(tmp_path):
    module = "test_lineage_invalidation_execution_discovery_service"
    missing = _first_case(module)
    other = module, sorted(EXPECTED_CASES[module])[1]
    report = _report(tmp_path / "results.xml", omit=missing, duplicate=other)
    with pytest.raises(ValueError, match="Duplicate runtime testcase"):
        validate_runtime_report(report)


def test_rejects_missing_one_parametrized_case(tmp_path):
    report = _report(
        tmp_path / "results.xml",
        omit=("test_lineage_invalidation_execution_discovery_service",
              "test_terminal_latest_attempt_is_not_discovered[failed]"),
    )
    with pytest.raises(ValueError, match="Required runtime testcase missing"):
        validate_runtime_report(report)


def test_allows_additional_unique_passed_cases_without_masking_required(tmp_path):
    report = _report(tmp_path / "results.xml")
    root = ET.parse(report).getroot()
    ET.SubElement(root, "testcase", classname="tests.integration.test_email_runtime", name="test_new_safe_case")
    ET.ElementTree(root).write(report)
    assert validate_runtime_report(report)["test_email_runtime"] == 2


def test_rejects_unrecognized_module(tmp_path):
    report = _report(tmp_path / "results.xml")
    root = ET.parse(report).getroot()
    ET.SubElement(root, "testcase", classname="tests.integration.test_unreviewed", name="test_x")
    ET.ElementTree(root).write(report)
    with pytest.raises(ValueError, match="Unexpected runtime test module"):
        validate_runtime_report(report)


def test_rejects_wrong_classname_namespace(tmp_path):
    report = _report(tmp_path / "results.xml")
    root = ET.parse(report).getroot()
    root.find("testcase").set("classname", "untrusted.test_crawler_scheduler_runtime")
    ET.ElementTree(root).write(report)
    with pytest.raises(ValueError, match="Unexpected runtime testcase class"):
        validate_runtime_report(report)


@pytest.mark.parametrize("field", ["errors", "failures", "skipped"])
def test_rejects_suite_level_error_even_without_testcase_error(tmp_path, field):
    report = _report(tmp_path / "results.xml")
    root = ET.parse(report).getroot()
    root.set(field, "1")
    ET.ElementTree(root).write(report)
    with pytest.raises(ValueError, match="JUnit suite reports"):
        validate_runtime_report(report)


def test_rejects_corrupt_or_empty_report(tmp_path):
    bad = tmp_path / "corrupt.xml"
    bad.write_text("<testsuite>", encoding="utf-8")
    with pytest.raises(ET.ParseError):
        validate_runtime_report(bad)
    empty = tmp_path / "empty.xml"
    empty.write_text("<testsuite />", encoding="utf-8")
    with pytest.raises(ValueError, match="Required runtime testcase missing"):
        validate_runtime_report(empty)
