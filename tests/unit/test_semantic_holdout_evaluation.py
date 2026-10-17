"""No network/credentials: external corpus and result-reporting contracts."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from scripts.evaluate_semantic_holdout import main, validate_execution
from src.services.semantic_holdout_evaluation import (
    evaluate_holdout, load_holdout, plan_holdout,
)


def _fixture():
    return {
        "schema_version": 1,
        "dataset_id": "test_private_holdout",
        "provenance": {
            "source_type": "internal",
            "annotation_protocol_id": "review_protocol_v1",
            "annotator_count": 2,
            "adjudicated": True,
            "held_out_before_model_selection": True,
        },
        "cases": [
            {
                "case_id": "sample_material", "domain": "eligibility",
                "expected_material": True,
                "old_text": "Refunds within 30 days. Confidential token AX1.",
                "new_text": "Refunds within 3 days. Confidential token AX1.",
            },
            {
                "case_id": "sample_equivalent", "domain": "privacy",
                "expected_material": False,
                "old_text": "Data is erased after 30 days. Secret BX2.",
                "new_text": "After 30 days, data is deleted. Secret BX2.",
            },
        ],
    }


def _load(tmp_path, data=None):
    file = tmp_path / "private.json"
    file.write_text(json.dumps(data or _fixture()), encoding="utf-8")
    return load_holdout(file), file


def _result(material):
    return {"analysis_status": "completed", "changes_count": int(material),
            "risk_level": "medium" if material else "low"}


def test_plan_only_prints_metadata_without_sensitive_text(tmp_path, capsys):
    (_cases, _digest, metadata), path = _load(tmp_path)
    assert metadata["dataset_id"] == "test_private_holdout"
    assert main(["--corpus", str(path)]) == 0
    output = capsys.readouterr().out
    report = json.loads(output)
    assert report["provider_requests"] == 0
    assert report["maximum_provider_requests"] == 2
    assert report["provenance_status"] == "self_declared_not_verified"
    for secret in ("Confidential", "Secret", "AX1", "BX2", "30 days"):
        assert secret not in output


def test_completed_report_and_text_diff_control(tmp_path):
    (cases, digest, meta), _ = _load(tmp_path)
    report = evaluate_holdout(cases, digest=digest, metadata=meta, model="fake",
                              analyzer=lambda case: _result(case.expected_material))
    assert report["metrics"]["precision_on_completed"] == 1
    assert report["metrics"]["f1_on_completed"] == 1
    assert report["metrics"]["material_detection_yield_including_abstentions"] == 1
    assert report["by_domain"]["eligibility"]["true_positive"] == 1
    assert report["by_domain"]["privacy"]["true_negative"] == 1
    assert report["exact_text_diff_baseline"]["false_positive"] == 1
    assert report["exact_text_diff_baseline"]["precision"] == 0.5
    assert "Confidential" not in json.dumps(report)
    assert "Secret BX2" not in json.dumps(report)
    assert "not_verified" in json.dumps(report)


def test_abstentions_are_not_false_negatives_and_errors_do_not_leak(tmp_path):
    (cases, digest, meta), _ = _load(tmp_path)
    def analyzer(case):
        if case.expected_material:
            raise RuntimeError("Secret text leaked from provider: AX1")
        return _result(False)
    report = evaluate_holdout(cases, digest=digest, metadata=meta,
                              model="fake", analyzer=analyzer)
    assert report["metrics"]["abstained_material"] == 1
    assert report["metrics"]["false_negative"] == 0
    assert report["metrics"]["coverage"] == 0.5
    assert report["metrics"]["recall_on_completed"] is None
    assert report["metrics"]["material_detection_yield_including_abstentions"] == 0
    assert "AX1" not in json.dumps(report)


@pytest.mark.parametrize("mutation", [
    lambda d: d.update({"dataset_id": "Client Alice"}),
    lambda d: d["provenance"].update({"adjudicated": "yes"}),
    lambda d: d["provenance"].update({"annotation_protocol_id": "x"}),
    lambda d: d["cases"][0].update({"expected_material": 1}),
    lambda d: d["cases"][0].update({"old_text": ""}),
    lambda d: d["cases"][0].update({"new_text": d["cases"][0]["old_text"]}),
    lambda d: d["cases"][1].update({"case_id": d["cases"][0]["case_id"]}),
    lambda d: d["cases"][1].update({"domain": "External pii"}),
    lambda d: d["cases"][0].update({"extra_field": "surprise"}),
    lambda d: d["cases"][0].update({"old_text": "A" * 12001}),
    lambda d: d["cases"][0].update({"expected_material": False}),
    lambda d: d.update({"schema_version": True}),
])
def test_corpus_rejects_unsafe_or_inconsistent_annotations(tmp_path, mutation):
    data = deepcopy(_fixture())
    mutation(data)
    with pytest.raises(ValueError):
        _load(tmp_path, data)


def test_reversed_duplicate_document_pair_rejected(tmp_path):
    data = _fixture()
    data["cases"][1]["old_text"] = data["cases"][0]["new_text"]
    data["cases"][1]["new_text"] = data["cases"][0]["old_text"]
    with pytest.raises(ValueError, match="Duplicate"):
        _load(tmp_path, data)


def test_corpus_max_count_is_enforced(tmp_path):
    data = _fixture()
    data["cases"] = [dict(data["cases"][i % 2],
                          case_id=f"synthetic_case_{i}",
                          old_text=f"Unique old {i}", new_text=f"Unique new {i}")
                     for i in range(101)]
    with pytest.raises(ValueError, match="100"):
        _load(tmp_path, data)


def test_live_guard_requires_per_dataset_approval_and_explicit_budget():
    env = {"ENVIRONMENT": "test", "DEEPSEEK_API_KEY": "test_secret",
           "SDE_SEMANTIC_HOLDOUT_EVAL_ALLOW_PROVIDER_CALLS": "1",
           "SDE_SEMANTIC_HOLDOUT_DATA_TRANSFER_APPROVED": "1"}
    validate_execution(execute=True, max_requests=2, count=2, environ=env)
    for change in (
        {"ENVIRONMENT": "production"},
        {"DEEPSEEK_API_KEY": ""},
        {"SDE_SEMANTIC_HOLDOUT_EVAL_ALLOW_PROVIDER_CALLS": "0"},
        {"SDE_SEMANTIC_HOLDOUT_DATA_TRANSFER_APPROVED": "0"},
    ):
        with pytest.raises(ValueError):
            validate_execution(execute=True, max_requests=2, count=2,
                               environ=dict(env, **change))
    for max_requests in (None, 0, 1, 3, True):
        with pytest.raises(ValueError):
            validate_execution(execute=True, max_requests=max_requests,
                               count=2, environ=env)


def test_cli_rejects_provider_before_loading_settings(tmp_path, monkeypatch):
    _, path = _load(tmp_path)
    monkeypatch.delenv("SDE_SEMANTIC_HOLDOUT_EVAL_ALLOW_PROVIDER_CALLS", raising=False)
    with pytest.raises(ValueError, match="opt-in"):
        main(["--corpus", str(path), "--execute", "--max-provider-requests", "2"])


def test_duplicate_json_field_rejected(tmp_path):
    _, path = _load(tmp_path)
    contents = path.read_text(encoding="utf-8")
    path.write_text(contents.replace('"schema_version": 1',
                                     '"schema_version": 1, "schema_version": 1'),
                    encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate JSON field"):
        load_holdout(path)


def test_false_negatives_not_hidden_by_per_domain_metrics(tmp_path):
    (cases, digest, metadata), _ = _load(tmp_path)
    report = evaluate_holdout(cases, digest=digest, metadata=metadata,
                              model="fake", analyzer=lambda _case: _result(False))
    assert report["metrics"]["false_negative"] == 1
    assert report["metrics"]["material_detection_yield_including_abstentions"] == 0.0
    assert report["by_domain"]["eligibility"]["false_negative"] == 1
    assert report["by_domain"]["privacy"]["recall_on_completed"] is None
