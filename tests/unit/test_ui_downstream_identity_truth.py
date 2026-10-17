"""Integrity Record must preserve exact downstream identity and evidence attribution.

Source contract checks are intentionally narrow; runtime React behavior is
verified separately by the synthetic Playwright acceptance script.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_integrity_response_declares_event_and_snapshot_pair():
    api = source("src/api/lineage_control_plane_responses.py")
    for field in ("event_id: uuid.UUID", "old_snapshot_id: uuid.UUID", "new_snapshot_id: uuid.UUID"):
        assert field in api


def test_selected_event_exposes_same_identity_triplet():
    types = source("frontend/src/temporal/types.ts")
    for field in ("id: string", "old_snapshot_id: string", "new_snapshot_id: string"):
        assert field in types


def test_client_refuses_mismatched_integrity_event_and_snapshots():
    code = source("frontend/src/workspace/IntegrityRecord.tsx")
    assert 'state.resolution.event_id !== selectedEvent.id' in code
    assert 'state.resolution.old_snapshot_id !== selectedEvent.old_snapshot_id' in code
    assert 'state.resolution.new_snapshot_id !== selectedEvent.new_snapshot_id' in code
    assert 'data-integrity-state="mismatched"' in code
    assert 'role="alert"' in code
    assert 'No downstream decision is displayed.' in code


def test_server_authority_unchanged_by_client_validation():
    code = source("frontend/src/workspace/IntegrityRecord.tsx")
    assert 'not a substitute' in code
    assert 'const resolution = state.status === "ready"' in code
    assert '&& !mismatchedEvidence' in code
    assert '{resolution.decisions.map(' in code
    assert '{state.resolution.decisions.map(' not in code


def test_decision_target_and_record_id_visible_even_if_path_empty():
    code = source("frontend/src/workspace/IntegrityRecord.tsx")
    for token in ('decision.id', 'decision.target.asset_external_id',
                  'decision.target.revision_external_id', 'decision.target.source_external_id',
                  'className="integrity-decision-identity"', 'decision.root'):
        assert token in code
    assert 'No persisted causal path is available for this decision.' in code


def test_no_decisions_cannot_be_presented_as_safety_or_zero_risk():
    code = source("frontend/src/workspace/IntegrityRecord.tsx")
    assert 'data-integrity-state="unobserved"' in code
    assert 'Unregistered dependencies remain unknown.' in code
    assert '{resolution !== null && resolution.decisions.length > 0 && (' in code
    assert '<IntegritySummary resolution={resolution} />' in code


def test_identity_layout_stays_dense_and_handles_long_revision_names():
    css = source("frontend/src/styles/workspace.css")
    assert '.integrity-decision-identity {' in css
    assert 'overflow-wrap: anywhere;' in css
    assert 'grid-template-columns: repeat(auto-fit, minmax(min(100%, 15rem), 1fr));' in css
