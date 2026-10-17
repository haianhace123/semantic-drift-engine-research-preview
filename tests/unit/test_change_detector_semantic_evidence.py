"""Regression tests for the production semantic detector safety gate."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.detector import change_detector as detector
from src.diff_engine.deepseek_diff import DeepSeekSemanticDiffEngine


def _snapshots(tmp_path: Path, old_text: str, new_text: str):
    old_path = tmp_path / "old.txt"
    new_path = tmp_path / "new.txt"
    old_path.write_text(old_text, encoding="utf-8")
    new_path.write_text(new_text, encoding="utf-8")
    return (
        SimpleNamespace(id="old", raw_content_path=str(old_path), content_hash="old-hash"),
        SimpleNamespace(id="new", raw_content_path=str(new_path), content_hash="new-hash"),
    )


def _install_snapshot_pair(monkeypatch, old_snapshot, new_snapshot, captured):
    monkeypatch.setattr(
        detector,
        "detect_changes_for_page",
        lambda _page: (True, old_snapshot, new_snapshot),
    )
    monkeypatch.setattr(detector, "_find_existing_event", lambda *_: None)

    def persist(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(detector, "create_drift_event", persist)


@pytest.mark.parametrize(
    ("old_clause", "new_clause"),
    [
        ("Data may be shared with partners.", "Data may not be shared with partners."),
        ("Refunds are available for 30 days.", "Refunds are available for 3 days."),
    ],
)
def test_near_identical_documents_reach_semantic_analysis(
    monkeypatch, tmp_path, old_clause, new_clause
):
    # A small contractual reversal must not be auto-classified as low risk.
    suffix = "\n" + "General terms remain unchanged.\n" * 20
    old_snapshot, new_snapshot = _snapshots(
        tmp_path, old_clause + suffix, new_clause + suffix
    )
    captured = []
    _install_snapshot_pair(monkeypatch, old_snapshot, new_snapshot, captured)
    compare_calls = []

    class FakeEngine:
        def compare(self, old_text, new_text):
            compare_calls.append((old_text, new_text))
            return {
                "analysis_status": "success",
                "changes": [{
                    "category": "policy",
                    "old_meaning": old_clause,
                    "new_meaning": new_clause,
                    "impact": "Contractual meaning changed.",
                    "risk_level": "high",
                }],
                "summary": "Material policy change.",
                "overall_risk": "high",
            }

    monkeypatch.setattr(detector, "DeepSeekSemanticDiffEngine", FakeEngine)

    result = detector.check_and_create_drift("page-1")

    assert len(compare_calls) == 1
    assert old_clause in compare_calls[0][0]
    assert new_clause in compare_calls[0][1]
    assert len(captured) == 1
    assert result is not None
    assert result.analysis_status == "completed"
    assert result.risk_level == "high"
    assert result.changes_count == 1


def test_oversized_semantic_evidence_is_persisted_as_failure(
    monkeypatch, tmp_path
):
    old_snapshot, new_snapshot = _snapshots(
        tmp_path, "The price is $10.", "The price is $20."
    )
    captured = []
    _install_snapshot_pair(monkeypatch, old_snapshot, new_snapshot, captured)

    def limited_engine():
        # Reject before consulting a provider or creating a client.
        engine = object.__new__(DeepSeekSemanticDiffEngine)
        engine.max_input_chars = 10
        return engine

    monkeypatch.setattr(detector, "DeepSeekSemanticDiffEngine", limited_engine)
    result = detector.check_and_create_drift("page-1")

    assert result is not None
    assert len(captured) == 1
    assert result.analysis_status == "failed"
    assert result.confidence == 0.0
    assert result.changes_count == 0
    assert "no partial analysis" in result.analysis_error
    assert result.delta_json["semantic_analysis"]["analysis_status"] == "failed"


def test_multiple_distant_edits_remain_separate_in_provider_input(
    monkeypatch, tmp_path
):
    old_text = "Price: $10." + "Unchanged supporting text. " * 40 + "Refund: 30 days."
    new_text = "Price: $20." + "Unchanged supporting text. " * 40 + "Refund: 3 days."
    old_snapshot, new_snapshot = _snapshots(tmp_path, old_text, new_text)
    captured = []
    _install_snapshot_pair(monkeypatch, old_snapshot, new_snapshot, captured)
    sent = []

    class FakeEngine:
        def compare(self, old_text, new_text):
            sent.append((old_text, new_text))
            return {
                "analysis_status": "success",
                "changes": [],
                "summary": "No significant change reported by provider.",
                "overall_risk": "low",
            }

    monkeypatch.setattr(detector, "DeepSeekSemanticDiffEngine", FakeEngine)
    result = detector.check_and_create_drift("page-1")

    assert result is not None
    assert len(sent) == 1
    old, new = sent[0]
    assert "[Changed region 1]" in old
    assert "[Changed region 2]" in old
    assert "$10" in old and "$20" in new
    assert "30 days" in old and "3 days" in new


@pytest.mark.parametrize("overall_risk", ["medium", "high"])
def test_unqualified_ai_risk_is_persisted_as_failed_analysis(
    monkeypatch, tmp_path, overall_risk
):
    old_snapshot, new_snapshot = _snapshots(
        tmp_path, "Policy: refunds within 30 days.", "Policy: refunds within 3 days."
    )
    persisted = []
    _install_snapshot_pair(monkeypatch, old_snapshot, new_snapshot, persisted)

    class ContradictoryEngine:
        def compare(self, old, new):
            return {
                "analysis_status": "success", "changes": [],
                "summary": "No material changes found.",
                "overall_risk": overall_risk,
            }

    monkeypatch.setattr(detector, "DeepSeekSemanticDiffEngine", ContradictoryEngine)
    # The detector maps results through the real response contract, even
    # when an alternative provider caller bypassed the engine's validator.
    event = detector.check_and_create_drift("page-1")

    assert event is not None
    assert len(persisted) == 1
    assert event.analysis_status == "failed"
    assert event.confidence == 0.0
    assert event.changes_count == 0
    assert event.analysis_attempts == 1
    assert event.delta_json["semantic_analysis"]["analysis_status"] == "failed"
    assert event.delta_json["analysis_failure"]["retryable"] is True


def test_event_mapping_rejects_raw_malformed_success_from_alternate_caller():
    with pytest.raises(detector.DeepSeekSemanticDiffError, match="understates"):
        detector._semantic_event_fields(
            {"semantic_analysis": {
                "analysis_status": "success",
                "changes": [{
                    "category": "legal", "old_meaning": "Allowed",
                    "new_meaning": "Banned", "impact": "Customers lose permission",
                    "risk_level": "high",
                }],
                "summary": "Contract restrictions increased.",
                "overall_risk": "low",
            }, "chunks_analyzed": 1},
            "Contract restrictions increased.",
        )
