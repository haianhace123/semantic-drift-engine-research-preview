"""Provider-reported model and token usage must survive semantic persistence.

These are contract tests, not paid provider tests. Missing usage is explicitly
unknown, and model-authored text is never used as usage evidence.
"""

from types import SimpleNamespace

import pytest

from src.core.config import settings
from src.diff_engine.deepseek_diff import DeepSeekSemanticDiffEngine
from src.detector import change_detector


VALID_RESPONSE = (
    '{"changes": [{"category": "policy", "old_meaning": "30 days", '
    '"new_meaning": "90 days", "impact": "Longer retention", '
    '"risk_level": "medium"}], "summary": "Retention extended.", '
    '"overall_risk": "medium"}'
)


def _mock_response(*, total_tokens=None, model=None):
    usage = None if total_tokens is None else SimpleNamespace(total_tokens=total_tokens)
    return SimpleNamespace(
        model=model,
        usage=usage,
        choices=[SimpleNamespace(message=SimpleNamespace(content=VALID_RESPONSE))],
    )


def test_provider_model_and_usage_are_from_sdk_metadata(monkeypatch):
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "synthetic-test-key")
    monkeypatch.setattr(settings, "DEEPSEEK_MODEL", "deepseek-flash")
    engine = DeepSeekSemanticDiffEngine()
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return _mock_response(total_tokens=193, model="deepseek-v4.1-flash")

    monkeypatch.setattr(engine.client.chat.completions, "create", create)
    result = engine.compare("Retain for 30 days.", "Retain for 90 days.")
    assert calls[0]["model"] == "deepseek-flash"
    assert result["provider_execution"] == {
        "requested_model": "deepseek-flash",
        "reported_model": "deepseek-v4.1-flash",
        "usage_reported": True,
        "total_tokens": 193,
    }
    assert result["changes"][0]["old_meaning"] == "30 days"


@pytest.mark.parametrize("usage,reported,expected", [
    (None, False, None),
    (-1, False, None),
    (True, False, None),
    (2_147_483_648, False, None),
    (0, True, 0),
])
def test_usage_never_invented_or_coerced(monkeypatch, usage, reported, expected):
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "synthetic-test-key")
    engine = DeepSeekSemanticDiffEngine()
    monkeypatch.setattr(
        engine.client.chat.completions, "create",
        lambda **kwargs: _mock_response(total_tokens=usage, model=None),
    )
    execution = engine.compare("Old.", "New.")["provider_execution"]
    assert execution["usage_reported"] is reported
    assert execution["total_tokens"] == expected
    assert execution["reported_model"] is None


def test_initial_and_recovery_paths_share_token_mapping(monkeypatch):
    execution = {
        "requested_model": "deepseek-flash",
        "reported_model": "deepseek-v4.1-flash",
        "usage_reported": True,
        "total_tokens": 532,
    }
    semantic = {
        "analysis_status": "success",
        "changes": [{
            "category": "policy", "old_meaning": "30 days",
            "new_meaning": "90 days", "impact": "Longer retention",
            "risk_level": "medium",
        }],
        "summary": "Retention extended.",
        "overall_risk": "medium",
        "provider_execution": execution,
    }

    class FakeDeepSeek:
        def compare(self, old_text, new_text):
            return dict(semantic)

    monkeypatch.setattr(change_detector, "DeepSeekSemanticDiffEngine", FakeDeepSeek)
    monkeypatch.setattr(
        change_detector.TextChunker, "find_changed_regions_with_context",
        lambda *a, **kw: (["Retain 30 days"], ["Retain 90 days"]),
    )
    old = SimpleNamespace(content_hash="a" * 64)
    new = SimpleNamespace(content_hash="b" * 64)
    delta, summary = change_detector._process_semantic_analysis(
        "synthetic-page", old, new,
        "Retain records 30 days.", "Retain records 90 days.",
    )
    fields = change_detector._semantic_event_fields(delta, summary)
    assert fields["analysis_status"] == "completed"
    assert fields["llm_tokens_used"] == 532
    assert fields["delta_json"]["provider_execution"] == execution
    assert "provider_execution" not in fields["delta_json"]["semantic_analysis"]


def test_missing_usage_is_not_misrepresented_as_measured_zero():
    delta = {
        "semantic_analysis": {
            "analysis_status": "success", "changes": [],
            "summary": "No material changes.", "overall_risk": "low",
        },
        "provider_execution": {
            "requested_model": "deepseek-flash",
            "reported_model": None,
            "usage_reported": False,
            "total_tokens": None,
        },
    }
    fields = change_detector._semantic_event_fields(delta, "No material changes.")
    assert fields["llm_tokens_used"] == 0
    assert fields["delta_json"]["provider_execution"]["usage_reported"] is False
