"""Unit tests for the DeepSeek semantic diff engine."""

from types import SimpleNamespace

import pytest

from src.core.config import settings
from src.diff_engine.deepseek_diff import (
    DeepSeekSemanticDiffEngine,
    DeepSeekSemanticDiffError,
    DeepSeekPermanentError,
    DeepSeekTransientError,
)


def test_extract_json_from_plain_json():
    raw = """
    {
        "changes": [],
        "summary": "No meaningful changes.",
        "overall_risk": "low"
    }
    """

    result = (
        DeepSeekSemanticDiffEngine._extract_json(
            raw
        )
    )

    assert result["changes"] == []
    assert result["overall_risk"] == "low"


def test_extract_json_from_markdown_fence():
    raw = """```json
{
    "changes": [],
    "summary": "No meaningful changes.",
    "overall_risk": "low"
}
```"""

    result = (
        DeepSeekSemanticDiffEngine._extract_json(
            raw
        )
    )

    assert result["summary"] == (
        "No meaningful changes."
    )


def test_normalize_valid_result():
    result = (
        DeepSeekSemanticDiffEngine._normalize_result(
            {
                "changes": [
                    {
                        "category": "legal",
                        "old_meaning": (
                            "Cancellation is allowed."
                        ),
                        "new_meaning": (
                            "Cancellation is restricted."
                        ),
                        "impact": (
                            "Customers have less flexibility."
                        ),
                        "risk_level": "high",
                    }
                ],
                "summary": (
                    "Cancellation policy became more restrictive."
                ),
                "overall_risk": "high",
            }
        )
    )

    assert result["analysis_status"] == "success"
    assert len(result["changes"]) == 1
    assert result["changes"][0]["category"] == "legal"
    assert result["overall_risk"] == "high"


def test_normalize_rejects_missing_changes():
    with pytest.raises(
        DeepSeekSemanticDiffError,
        match="missing 'changes'",
    ):
        DeepSeekSemanticDiffEngine._normalize_result(
            {
                "summary": "Test",
                "overall_risk": "low",
            }
        )


def test_normalize_rejects_invalid_risk():
    with pytest.raises(
        DeepSeekSemanticDiffError,
        match="invalid overall_risk",
    ):
        DeepSeekSemanticDiffEngine._normalize_result(
            {
                "changes": [],
                "summary": "Test",
                "overall_risk": "unknown",
            }
        )


def test_normalize_rejects_invalid_category():
    with pytest.raises(
        DeepSeekSemanticDiffError,
        match="invalid category",
    ):
        DeepSeekSemanticDiffEngine._normalize_result(
            {
                "changes": [
                    {
                        "category": "security",
                        "old_meaning": "Old",
                        "new_meaning": "New",
                        "impact": "Impact",
                        "risk_level": "high",
                    }
                ],
                "summary": "Test",
                "overall_risk": "high",
            }
        )


def test_compare_raises_when_api_fails(
    monkeypatch,
):
    monkeypatch.setattr(
        settings,
        "DEEPSEEK_API_KEY",
        "test-key",
    )

    engine = (
        DeepSeekSemanticDiffEngine()
    )

    def failing_create(*args, **kwargs):
        raise RuntimeError(
            "simulated API failure"
        )

    monkeypatch.setattr(
        engine.client.chat.completions,
        "create",
        failing_create,
    )

    with pytest.raises(
        DeepSeekSemanticDiffError,
        match="request failed",
    ):
        engine.compare(
            "Old policy text.",
            "New policy text.",
        )


def test_compare_rejects_malformed_model_response(
    monkeypatch,
):
    monkeypatch.setattr(
        settings,
        "DEEPSEEK_API_KEY",
        "test-key",
    )

    engine = (
        DeepSeekSemanticDiffEngine()
    )

    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content="not valid json"
                )
            )
        ]
    )

    monkeypatch.setattr(
        engine.client.chat.completions,
        "create",
        lambda *args, **kwargs: response,
    )

    with pytest.raises(
        DeepSeekSemanticDiffError,
        match="valid JSON",
    ):
        engine.compare(
            "Old policy text.",
            "New policy text.",
        )


def test_compare_returns_normalized_result(
    monkeypatch,
):
    monkeypatch.setattr(
        settings,
        "DEEPSEEK_API_KEY",
        "test-key",
    )

    engine = (
        DeepSeekSemanticDiffEngine()
    )

    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        '{"changes": ['
                        '{"category": "legal",'
                        '"old_meaning": "Allowed",'
                        '"new_meaning": "Restricted",'
                        '"impact": "Higher customer friction",'
                        '"risk_level": "high"}'
                        '],'
                        '"summary": "Policy changed.",'
                        '"overall_risk": "high"}'
                    )
                )
            )
        ]
    )

    monkeypatch.setattr(
        engine.client.chat.completions,
        "create",
        lambda *args, **kwargs: response,
    )

    result = engine.compare(
        "Customers may cancel anytime.",
        "Customers may only cancel during renewal.",
    )

    assert result["analysis_status"] == "success"
    assert result["overall_risk"] == "high"
    assert len(result["changes"]) == 1

def test_overlimit_evidence_fails_before_provider_request():
    engine = object.__new__(DeepSeekSemanticDiffEngine)
    engine.max_input_chars = 10

    assert engine._validate_input_length("short") == "short"
    with pytest.raises(DeepSeekSemanticDiffError, match="no partial analysis"):
        engine.compare("A" * 11, "changed")
    with pytest.raises(DeepSeekSemanticDiffError, match="no partial analysis"):
        engine.compare("original", "B" * 11)


def test_provider_status_distinguishes_permanent_and_transient(monkeypatch):
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "test-key")
    engine = DeepSeekSemanticDiffEngine()

    class FakeProviderError(Exception):
        def __init__(self, code):
            self.status_code = code
            super().__init__("fake provider failure")

    for status, error_type in ((401, DeepSeekPermanentError), (429, DeepSeekTransientError), (503, DeepSeekTransientError)):
        def fail(**kwargs):
            raise FakeProviderError(status)
        monkeypatch.setattr(engine.client.chat.completions, "create", fail)
        with pytest.raises(error_type):
            engine.compare("Old text.", "New text.")


@pytest.mark.parametrize(
    ("changes", "summary", "overall_risk", "expected_error"),
    [
        ([], "No changes found.", "medium", "empty changes require low"),
        ([], "No changes found.", "high", "empty changes require low"),
        ([], "  ", "low", "nonempty string"),
        ([{"category": "policy", "old_meaning": "", "new_meaning": "  ",
           "impact": "Unspecified", "risk_level": "low"}],
         "Policy changed.", "low", "no old or new meaning"),
        ([{"category": "policy", "old_meaning": "Opt-out allowed",
           "new_meaning": "Opt-out prohibited", "impact": "   ",
           "risk_level": "medium"}],
         "Policy changed.", "medium", "no business impact explanation"),
        ([{"category": "legal", "old_meaning": "Refunds allowed",
           "new_meaning": "Refunds prohibited", "impact": "Loss of refunds",
           "risk_level": "high"}],
         "Refund policy changed.", "low", "understates a reported change"),
    ],
)
def test_semantic_response_rejects_internally_contradictory_evidence(
    changes, summary, overall_risk, expected_error
):
    with pytest.raises(DeepSeekSemanticDiffError, match=expected_error):
        DeepSeekSemanticDiffEngine._normalize_result({
            "changes": changes, "summary": summary, "overall_risk": overall_risk,
        })


def test_semantic_response_keeps_valid_additions_and_removals():
    result = DeepSeekSemanticDiffEngine._normalize_result({
        "changes": [
            {"category": "policy", "old_meaning": "", "new_meaning": "Two-factor login required",
             "impact": "Adds authentication requirement", "risk_level": "medium"},
            {"category": "policy", "old_meaning": "Public access allowed", "new_meaning": "",
             "impact": "Public access is removed", "risk_level": "medium"},
        ],
        "summary": "Access obligations changed.", "overall_risk": "high",
    })
    assert result["analysis_status"] == "success"
    assert len(result["changes"]) == 2
