"""UI contract regression for semantic-evidence truth states.

These narrowly scoped source checks do not replace compiled-browser acceptance.
The temporal evidence API returns the persisted delta without normalization,
including pending gaps and failed semantic provider calls.
"""
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_backend_exposes_persisted_analysis_status_and_delta_without_inventing_a_result():
    source = read("src/api/temporal_routes.py")
    assert '"analysis_status":\n            event.analysis_status' in source
    assert '"delta":\n            event.semantic_delta_json\n            or {}' in source
    ast.parse(source)


def test_persisted_payload_types_allow_incomplete_semantic_analysis():
    types = read("frontend/src/temporal/types.ts")
    assert "analysis_status?: string;" in types
    assert "semantic_analysis?: TemporalSemanticAnalysis;" in types
    assert "similarity?: number;" in types
    assert "chunks_analyzed?: number;" in types


def test_pending_cannot_become_a_completed_no_change_conclusion():
    ui = read("frontend/src/workspace/SemanticEvidence.tsx")
    assert 'evidence.analysis_status === "completed"' in ui
    assert 'analysis?.analysis_status === "success"' in ui
    assert 'Array.isArray(analysis?.changes)' in ui
    assert 'analysis.changes.every(isQualifiedSemanticChange)' in ui
    assert '{interpretationComplete\n      && changes.length === 0 && (' in ui
    assert 'Semantic analysis pending' in ui
    assert 'No model-reported meaning changes' in ui
    assert 'No interpreted meaning shift' not in ui


def test_failed_and_unqualified_analysis_never_display_interpreted_changes():
    ui = read("frontend/src/workspace/SemanticEvidence.tsx")
    assert 'evidence.analysis_status === "failed"' in ui
    assert 'analysis?.analysis_status === "failed"' in ui
    assert 'Semantic interpretation failed' in ui
    assert 'Semantic interpretation unavailable' in ui
    assert 'This is not a conclusion that its meaning was unchanged.' in ui
    assert '{interpretationComplete\n      && changes.length > 0 && (' in ui


def test_metric_absence_is_explicit_not_an_implicit_zero():
    ui = read("frontend/src/workspace/SemanticEvidence.tsx")
    assert 'typeof similarity !== "number"' in ui
    assert 'typeof delta.chunks_analyzed === "number"' in ui
    assert 'Number.isInteger(delta.chunks_analyzed)' in ui
    assert '<dt>Analyzed chunks</dt>' in ui
    assert '"Unavailable"' in ui


def test_snapshot_content_remains_accessible_regardless_of_analysis_status():
    ui = read("frontend/src/workspace/SemanticEvidence.tsx")
    assert 'className="snapshot-evidence-pair"' in ui
    assert 'snapshot={evidence.before}' in ui
    assert 'snapshot={evidence.after}' in ui
    assert 'interpretationComplete' not in ui.split('<div className="snapshot-evidence-pair">')[1].split('</div>')[0]


def test_accessibility_distinguishes_pending_status_from_failed_alert():
    ui = read("frontend/src/workspace/SemanticEvidence.tsx")
    assert '<div className="semantic-analysis-unavailable" role="status">\n          <span>Semantic analysis pending</span>' in ui
    assert '<div className="semantic-analysis-unavailable" role="alert">\n          <span>Semantic interpretation failed</span>' in ui
    assert 'className="semantic-evidence-error"\n          role="alert"' in ui


def test_evidence_status_is_not_conflated_with_provider_risk_or_invalidation():
    ui = read("frontend/src/workspace/SemanticEvidence.tsx")
    assert 'Model {change.risk_level} risk' in ui
    assert 'Recorded event summary' in ui
    assert 'No completed semantic interpretation is recorded' not in ui or 'status' in ui
