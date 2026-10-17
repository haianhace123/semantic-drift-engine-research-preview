"""UI06 contracts: Chronoscope markers represent instants; events remain distinct."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def source(rel):
    return (ROOT / rel).read_text(encoding="utf-8")

def test_timestamp_equivalence_is_grouped_before_render():
    jsx = source("frontend/src/workspace/Chronoscope.tsx")
    assert "const timestamp = Date.parse(event.detected_at)" in jsx
    assert "new Map<number, TemporalMoment>()" in jsx
    assert "existing.events.push(event)" in jsx
    assert "existing.detectedAt = new Date(timestamp).toISOString()" in jsx
    assert "moments.map((moment, index)" in jsx
    assert "points.map(" not in jsx

def test_shared_timestamp_is_one_focusable_control_and_count_is_disclosed():
    jsx = source("frontend/src/workspace/Chronoscope.tsx")
    assert 'key={moment.timestamp}' in jsx
    assert 'data-event-count={moment.events.length}' in jsx
    assert 'className="chronoscope-point-count"' in jsx
    assert 'Select a specific change in the Change index.' in jsx
    assert 'moment.events.some(' in jsx

def test_arrow_keys_follow_focused_marker_not_stale_cursor():
    jsx = source("frontend/src/workspace/Chronoscope.tsx")
    assert 'onKeyDown={(event) => {' in jsx
    assert 'event.key === "ArrowLeft" && index > 0' in jsx
    assert 'event.key === "ArrowRight" && index < moments.length - 1' in jsx
    assert 'moveKeyboardCursor(moments[targetIndex])' in jsx
    assert 'momentRefs.current.get(moment.timestamp)?.focus()' in jsx
    assert 'onKeyDown={(event) =>' not in jsx.split('className="chronoscope-track"')[1].split('moments.map((moment, index)')[0]

def test_previous_next_skip_equal_instants_without_skipping_unique_moments():
    jsx = source("frontend/src/workspace/Chronoscope.tsx")
    assert "if (point.timestamp < cursorTimestamp)" in jsx
    assert "if (point.timestamp > cursorTimestamp)" in jsx
    assert "previousMoment.detectedAt" in jsx
    assert "nextMoment.detectedAt" in jsx

def test_loaded_window_not_advertised_as_full_history():
    jsx = source("frontend/src/workspace/Chronoscope.tsx")
    assert 'totalEvents > events.length' in jsx
    assert 'changes loaded' in jsx
    assert 'shared-time' in jsx

def test_counter_render_does_not_inherit_tick_background():
    css = source("frontend/src/styles/workspace.css")
    assert ".chronoscope-point .chronoscope-point-count" in css
    assert '.chronoscope-point[aria-pressed="true"] > span:first-child' in css
    assert '.chronoscope-point[data-selected-event="true"] > span:first-child' in css
