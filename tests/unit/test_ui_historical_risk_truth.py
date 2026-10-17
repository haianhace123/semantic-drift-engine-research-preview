"""UI contract for knowledge-time, event-bound Risk v2 evidence.

Source checks are narrow; actual React behavior requires isolated browser fixture.
"""
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_replay_backend_gates_risk_to_knowledge_time_and_v2():
    source = read("src/services/temporal_replay_service.py")
    assert "RiskAssessment.assessed_at\n            <= cursor" in source
    assert "RiskAssessment.created_at\n            <= cursor" in source
    assert "RiskAssessment.risk_version\n            == RISK_VERSION_V2" in source
    ast.parse(source)


def test_replay_api_returns_event_bound_qualified_risk_fields():
    source = read("src/api/temporal_routes.py")
    assert '"semantic_drift_event_id": (' in source
    assert '"risk_version":' in source
    assert '"assessed_at":' in source
    assert '"created_at":' in source
    assert '"score":' in source
    assert '"topology_contribution":' in source
    ast.parse(source)


def test_workspace_passes_selected_event_id_instead_of_global_count():
    source = read("frontend/src/workspace/TemporalWorkspace.tsx")
    assert "selectedEventId={selectedEventId}" in source
    assert "hasSelectedEvent={selectedEvent !== null}" not in source


def test_risk_display_requires_event_exists_at_cursor_and_exact_join():
    source = read("frontend/src/workspace/HistoricalEvidence.tsx")
    assert "eventRecordedAtCursor" in source
    assert "event.event_id === selectedEventId" in source
    assert "item.semantic_drift_event_id === eventId" in source
    assert 'item.risk_version === "deterministic_v2"' in source
    assert "replayIsCurrent && state.status === \"ready\"" in source
    assert "state.replay.risk_assessments" in source


def test_risk_unavailable_does_not_invent_zero_or_downstream_execution():
    source = read("frontend/src/workspace/HistoricalEvidence.tsx")
    assert 'data-risk-state="unavailable"' in source
    assert 'data-risk-state="not-yet-recorded"' in source
    assert "Risk is unknown," in source
    assert "Model-reported risk and downstream execution are separate." in source
    assert "Number.isFinite(eventRisk.assessment.score)" in source
    assert "No qualified topology signal" in source


def test_risk_provenance_exposes_version_assessed_and_recorded_timestamps():
    source = read("frontend/src/workspace/HistoricalEvidence.tsx")
    for token in ("assessment_id", "risk_version", "assessed_at", "created_at", "has_topology_signal"):
        assert token in source
    assert "Latest" not in source or "latestEventRisk" in source


def test_global_replay_totals_are_distinct_from_event_risk():
    source = read("frontend/src/workspace/Chronoscope.tsx")
    assert 'aria-label="Organization-wide replay counts at the temporal cursor"' in source
    assert "risk_assessment_count" in source


def test_historical_risk_styles_preserve_existing_design_system():
    css = read("frontend/src/styles/workspace.css")
    assert ".historical-risk-evidence" in css
    assert "var(--text-muted)" in css
    assert "var(--line)" in css
