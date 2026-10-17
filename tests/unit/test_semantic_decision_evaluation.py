"""Offline contract tests; never invoke the DeepSeek provider."""

from __future__ import annotations

import json

import pytest

from scripts.evaluate_semantic_decisions import main, validate_execution
from src.services.semantic_decision_evaluation import (
    DecisionCase,
    evaluate_decisions,
    load_cases,
    plan,
)


def _case(case_id: str, expected: bool) -> DecisionCase:
    return DecisionCase(case_id, "policy", expected, "Original statement.", "Updated statement.")


def _result(material: bool, risk: str | None = None):
    return {
        "analysis_status": "completed",
        "changes_count": int(material),
        "risk_level": risk or ("medium" if material else "low"),
    }


def _evaluate(analyzer):
    return evaluate_decisions(
        (_case("material_case", True), _case("equivalent_case", False)),
        digest="fixed-digest", model="fake", analyzer=analyzer,
    )


def test_repository_corpus_is_balanced_and_frozen():
    cases, digest = load_cases()
    assert len(cases) == 24
    assert len({c.case_id for c in cases}) == 24
    assert sum(c.expected_material for c in cases) == 12
    assert {c.domain for c in cases} == {
        "policy", "pricing", "privacy", "sla", "security", "eligibility"
    }
    assert len(digest) == 64
    assert plan(cases, digest)["provider_requests"] == 0


def test_correct_diagnostic_has_complete_confusion_matrix():
    report = _evaluate(lambda case: _result(case.expected_material))
    assert report["confusion_matrix"] == {
        "true_positive": 1,
        "false_positive": 0,
        "true_negative": 1,
        "false_negative": 0,
        "abstained_material": 0,
        "abstained_equivalent": 0,
    }
    assert report["diagnostic_passed"] is True
    assert report["coverage"] == 1.0
    assert report["precision_on_completed"] == 1.0
    assert report["recall_on_completed"] == 1.0


def test_meaningful_low_risk_change_is_still_material():
    report = _evaluate(
        lambda case: _result(case.expected_material, "low")
    )
    assert report["diagnostic_passed"] is True
    assert report["confusion_matrix"]["true_positive"] == 1


def test_false_negative_and_false_positive_fail_without_changing_labels():
    report = _evaluate(lambda case: _result(not case.expected_material))
    assert report["confusion_matrix"]["false_negative"] == 1
    assert report["confusion_matrix"]["false_positive"] == 1
    assert report["diagnostic_passed"] is False
    assert report["material_detection_yield"] == 0.0


def test_provider_failure_is_abstention_not_correct_negative():
    def analyzer(case):
        if case.expected_material:
            raise RuntimeError("secret private document text")
        return _result(False)
    report = _evaluate(analyzer)
    assert report["abstained_count"] == 1
    assert report["confusion_matrix"]["abstained_material"] == 1
    assert report["confusion_matrix"]["false_negative"] == 0
    assert report["material_detection_yield"] == 0.0
    assert report["recall_on_completed"] is None
    assert report["diagnostic_passed"] is False
    assert "secret private document text" not in json.dumps(report)
    assert "Original statement" not in json.dumps(report)
    assert "Updated statement" not in json.dumps(report)


@pytest.mark.parametrize("bad_result", [
    None,
    {"analysis_status": "failed", "changes_count": 0, "risk_level": "low"},
    {"analysis_status": "completed", "changes_count": True, "risk_level": "low"},
    {"analysis_status": "completed", "changes_count": 0, "risk_level": "high"},
    {"analysis_status": "completed", "changes_count": -1, "risk_level": "low"},
    {"analysis_status": "completed", "changes_count": 1, "risk_level": "invalid"},
])
def test_inconsistent_results_are_not_treated_as_completions(bad_result):
    report = _evaluate(lambda _case: bad_result)
    assert report["abstained_count"] == 2
    assert report["completed_count"] == 0
    assert report["diagnostic_passed"] is False


def test_corrupt_corpus_duplicate_id_rejected(tmp_path):
    cases = [
        {"case_id": "duplicate", "domain": "policy", "expected_material": True,
         "old_text": "A", "new_text": "B"},
        {"case_id": "duplicate", "domain": "policy", "expected_material": False,
         "old_text": "A", "new_text": "C"},
    ]
    path = tmp_path / "cases.json"
    path.write_text(json.dumps({"schema_version": 1, "cases": cases}))
    with pytest.raises(ValueError, match="Duplicate"):
        load_cases(path)


@pytest.mark.parametrize("env_update", [
    {"ENVIRONMENT": "production"},
    {"ENVIRONMENT": ""},
    {"DEEPSEEK_API_KEY": ""},
    {"SDE_SEMANTIC_DECISION_EVAL_ALLOW_PROVIDER_CALLS": "0"},
])
def test_execution_guard_rejects_unsafe_config(env_update):
    env = {
        "ENVIRONMENT": "development",
        "DEEPSEEK_API_KEY": "fake",
        "SDE_SEMANTIC_DECISION_EVAL_ALLOW_PROVIDER_CALLS": "1",
    }
    env.update(env_update)
    with pytest.raises(ValueError):
        validate_execution(execute=True, environ=env)


def test_execution_guard_blocks_missing_explicit_execute():
    with pytest.raises(ValueError):
        validate_execution(execute=False, environ={
            "ENVIRONMENT": "test",
            "DEEPSEEK_API_KEY": "fake",
            "SDE_SEMANTIC_DECISION_EVAL_ALLOW_PROVIDER_CALLS": "1",
        })


def test_plan_does_not_initialize_provider(monkeypatch, capsys):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert main([]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "plan"
    assert report["provider_requests"] == 0
    assert report["case_count"] == 24


def test_production_adapter_uses_detector_authority(monkeypatch):
    from src.detector import change_detector as detector
    from src.services.semantic_decision_evaluation import analyze_via_production
    observed = []

    def process(page_id, old_snapshot, new_snapshot, old_text, new_text):
        observed.append((page_id, old_snapshot.content_hash, new_snapshot.content_hash))
        assert old_text != new_text
        return {"semantic_analysis": {"analysis_status": "success", "changes": []},
                "chunks_analyzed": 1}, "Equivalent"

    def mapping(delta, summary):
        assert summary == "Equivalent"
        assert delta["chunks_analyzed"] == 1
        return {"analysis_status": "completed", "changes_count": 0,
                "risk_level": "low"}

    monkeypatch.setattr(detector, "_process_semantic_analysis", process)
    monkeypatch.setattr(detector, "_semantic_event_fields", mapping)
    assert analyze_via_production(_case("adapter_case", False)) == _result(False)
    assert observed[0][0] == "evaluation:adapter_case"
    assert len(observed[0][1]) == 64 and len(observed[0][2]) == 64


def test_production_adapter_uses_real_region_extraction(monkeypatch):
    from src.detector import change_detector as detector
    from src.services.semantic_decision_evaluation import analyze_via_production
    old_clause = "Refunds are available for 30 days."
    new_clause = "Refunds are available for 3 days."
    sent = []

    class FakeEngine:
        def compare(self, old_text, new_text):
            sent.append((old_text, new_text))
            return {
                "analysis_status": "success",
                "changes": [{
                    "category": "policy", "old_meaning": old_clause,
                    "new_meaning": new_clause, "impact": "Shorter refund window.",
                    "risk_level": "low",
                }],
                "summary": "Refund window reduced.",
                "overall_risk": "low",
            }

    monkeypatch.setattr(detector, "DeepSeekSemanticDiffEngine", FakeEngine)
    case = DecisionCase(
        "live_path_contract", "policy", True,
        old_clause + "\nUnchanged legal terms. " * 20,
        new_clause + "\nUnchanged legal terms. " * 20,
    )
    result = analyze_via_production(case)
    assert result == {
        "analysis_status": "completed", "changes_count": 1,
        "risk_level": "low",
    }
    assert len(sent) == 1
    assert old_clause in sent[0][0]
    assert new_clause in sent[0][1]
