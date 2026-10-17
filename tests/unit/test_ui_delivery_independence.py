"""Source-level navigation invariants for the independent alert-delivery plane.

These fail closed on inadvertent JSX restructuring. Browser acceptance is still
required to certify actual rendering and focus behavior.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_delivery_route_has_one_hash_authority():
    route = read("frontend/src/workspace/workspaceRoute.ts")
    assert '| { view: "deliveries"; eventId: null }' in route
    assert 'if (hash === "#deliveries")' in route
    assert 'return { view: "deliveries", eventId: null };' in route
    assert 'if (hash.startsWith("#event/"))' in route


def test_operations_render_before_event_history_status_branch():
    workspace = read("frontend/src/workspace/TemporalWorkspace.tsx")
    branch = workspace.index(') : route.view === "deliveries" ? (')
    history = workspace.index(') : eventsState.status !== "ready" ? (')
    assert branch < history
    assert workspace.count("<AlertOperations ") == 1
    assert 'href="#deliveries"' in workspace
    assert 'route.view === "deliveries"\n        ? "alert-operations-title"' in workspace
    alert = read("frontend/src/alerts/AlertOperations.tsx")
    assert '<h2 id="alert-operations-title" tabIndex={-1}>' in alert


def test_history_fetch_is_disabled_outside_investigation_and_aborted():
    workspace = read("frontend/src/workspace/TemporalWorkspace.tsx")
    hook = read("frontend/src/temporal/useTemporalEvents.ts")
    assert 'useTemporalEvents(\n    route.view === "investigation",\n  )' in workspace
    assert "export function useTemporalEvents(enabled = true)" in hook
    assert "if (!enabled)" in hook
    assert "controllerRef.current?.abort();" in hook
    assert "setState(INITIAL_STATE);" in hook


def test_retry_authorization_stays_role_bound():
    workspace = read("frontend/src/workspace/TemporalWorkspace.tsx")
    alert = read("frontend/src/alerts/AlertOperations.tsx")
    assert '<AlertOperations canRetry={currentUser.role !== "viewer"} />' in workspace
    assert "!canRetry" in alert
    assert "!delivery.retry_allowed" in alert


def test_top_level_deliveries_page_has_distinct_heading_scale():
    css = read("frontend/src/styles/workspace.css")
    assert ".temporal-workspace > .alert-operations" in css
    assert ".temporal-workspace > .alert-operations .alert-operations-header h2" in css
