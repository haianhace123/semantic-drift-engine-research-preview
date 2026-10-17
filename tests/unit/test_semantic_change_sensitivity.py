from __future__ import annotations

import math

import pytest

from scripts.evaluate_semantic_change_sensitivity import validate_runtime
from src.services.semantic_change_sensitivity import (
    CASES,
    SemanticChangeCase,
    evaluate_semantic_sensitivity,
)


def _metric(left, right):
    # Stand-in for the production metric in isolated tests. The live CLI uses
    # calculate_vector_metrics from EmbeddingService directly.
    dot = sum(x * y for x, y in zip(left, right))
    norm = math.sqrt(sum(x*x for x in left) * sum(x*x for x in right))
    return {"semantic_distance": 1.0 - dot / norm}


def _case():
    return SemanticChangeCase("test_policy", "baseline", "rephrased", "reversed")


def _embed(vectors, calls):
    def impl(text):
        calls.append(text)
        return vectors[text]
    return impl


def _evaluate(vectors, cases=None, calls=None):
    return evaluate_semantic_sensitivity(
        embed_text=_embed(vectors, calls if calls is not None else []),
        provider="fake", model="test", metric_fn=_metric,
        cases=[_case()] if cases is None else cases,
    )


def test_material_change_farther_than_paraphrase_passes():
    calls = []
    report = _evaluate({
        "baseline": [1, 0, 0],
        "rephrased": [0.99, 0.1, 0],
        "reversed": [0, 1, 0],
    }, calls=calls)
    assert report["strict_separation_passed"] is True
    assert report["passed_count"] == 1
    assert report["provider_requests"] == 3
    assert calls == ["baseline", "rephrased", "reversed"]
    assert report["cases"][0]["identity_distance"] == 0


def test_material_change_not_farther_is_honest_failure():
    report = _evaluate({
        "baseline": [1, 0, 0],
        "rephrased": [0, 1, 0],
        "reversed": [0.99, 0.1, 0],
    })
    assert report["strict_separation_passed"] is False
    assert report["passed_count"] == 0
    assert report["cases"][0]["separation_margin"] < 0


def test_tied_distances_fail():
    report = _evaluate({
        "baseline": [1, 0, 0],
        "rephrased": [1, 0, 0],
        "reversed": [1, 0, 0],
    })
    assert report["strict_separation_passed"] is False


@pytest.mark.parametrize("field", ["baseline", "paraphrase", "material_change"])
def test_invalid_text_rejected_without_provider_calls(field):
    case = _case()
    obj = {"case_id": case.case_id, "baseline": case.baseline,
           "paraphrase": case.paraphrase, "material_change": case.material_change}
    obj[field] = "   "
    calls = []
    with pytest.raises(ValueError, match="nonempty"):
        _evaluate({}, cases=[SemanticChangeCase(**obj)], calls=calls)
    assert calls == []


def test_duplicate_case_ids_rejected():
    vectors = {"baseline": [1, 0], "rephrased": [0.9, 0.1], "reversed": [0, 1]}
    calls = []
    with pytest.raises(ValueError, match="unique"):
        _evaluate(vectors, cases=[_case(), _case()], calls=calls)
    assert calls == []


def test_zero_vector_rejected():
    with pytest.raises(ValueError, match="zero vector"):
        _evaluate({"baseline": [0, 0], "rephrased": [1, 0], "reversed": [0, 1]})


def test_nonfinite_vector_rejected():
    with pytest.raises(ValueError, match="non-finite"):
        _evaluate({"baseline": [1, float("nan")], "rephrased": [1, 0], "reversed": [0, 1]})


def test_dimension_mismatch_rejected():
    with pytest.raises(ValueError, match="dimensions"):
        _evaluate({"baseline": [1, 0], "rephrased": [1, 0, 0], "reversed": [0, 1]})


def test_no_raw_text_or_vectors_in_report():
    vectors = {"baseline": [1, 0], "rephrased": [0.99, 0.1], "reversed": [0, 1]}
    report = _evaluate(vectors)
    assert "baseline" not in str(report)
    assert "rephrased" not in str(report)
    assert "reversed" not in str(report)
    assert "embedding" not in report["cases"][0]


def test_default_cases_have_unique_ids_and_distinct_text():
    assert len(CASES) == 3
    assert len({case.case_id for case in CASES}) == len(CASES)
    assert all(len({case.baseline, case.paraphrase, case.material_change}) == 3 for case in CASES)


def test_empty_cases_rejected():
    with pytest.raises(ValueError, match="At least one"):
        _evaluate({}, cases=[])


@pytest.mark.parametrize("overrides", [
    {"SDE_SEMANTIC_BENCHMARK_ALLOW_PROVIDER_CALLS": "0"},
    {"ENVIRONMENT": "production"},
    {"EMBEDDING_PROVIDER": "openai"},
    {"GEMINI_API_KEY": ""},
])
def test_live_provider_guard_rejects_unsafe_configuration(overrides):
    env = {
        "SDE_SEMANTIC_BENCHMARK_ALLOW_PROVIDER_CALLS": "1",
        "ENVIRONMENT": "development",
        "EMBEDDING_PROVIDER": "gemini",
        "GEMINI_API_KEY": "fake-test-key",
    }
    env.update(overrides)
    with pytest.raises(ValueError):
        validate_runtime(execute=True, environ=env)


def test_live_provider_guard_rejects_missing_execute():
    with pytest.raises(ValueError):
        validate_runtime(execute=False, environ={
            "SDE_SEMANTIC_BENCHMARK_ALLOW_PROVIDER_CALLS": "1",
            "ENVIRONMENT": "development", "EMBEDDING_PROVIDER": "gemini",
            "GEMINI_API_KEY": "fake-test-key",
        })


def test_live_provider_guard_accepts_dev_opt_in():
    validate_runtime(execute=True, environ={
        "SDE_SEMANTIC_BENCHMARK_ALLOW_PROVIDER_CALLS": "1",
        "ENVIRONMENT": "development", "EMBEDDING_PROVIDER": "gemini",
        "GEMINI_API_KEY": "fake-test-key",
    })
