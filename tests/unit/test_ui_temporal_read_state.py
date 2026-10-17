"""UI read-state invariants; synthetic browser acceptance exercises behavior.

These source checks are intentionally narrow and do not replace HTTP/browser QA.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_temporal_replay_is_scoped_to_exact_visible_cursor():
    ui = source("frontend/src/workspace/TemporalWorkspace.tsx")
    assert 'const scopedReplayState: TemporalReplayState = cursorTime === null' in ui
    assert 'replayState.cursorTime !== cursorTime' in ui
    assert 'replayState={scopedReplayState}' in ui
    assert 'state={scopedReplayState}' in ui
    assert 'replaySnapshotForEvent(scopedReplayState.replay, selectedEvent)' in ui


def test_historical_replay_error_is_announced_as_error():
    ui = source("frontend/src/workspace/HistoricalEvidence.tsx")
    assert 'state.status === "error" && (\n        <div role="alert">' in ui


def test_alert_reads_fail_closed_when_superseded_or_failed():
    hook = source("frontend/src/alerts/useAlertDeliveries.ts")
    assert 'requestId === latestRequestId.current && !signal?.aborted' in hook
    assert 'if (!isCurrent()) return;' in hook
    assert 'deliveries: refreshing ? current.deliveries : []' in hook
    assert 'summary: refreshing ? current.summary : null' in hook
    assert 'error: errorMessage(error),' in hook
    assert 'setSelectedDeliveryId(null);' in hook
    assert 'summary: null,\n          totalCount: 0,' in hook


def test_delivery_inspector_and_pagination_do_not_expose_old_failed_read():
    ui = source("frontend/src/alerts/AlertOperations.tsx")
    assert 'No delivery data is available until a successful read.' in ui
    assert 'error !== null ? "Unavailable"' in ui
    assert 'retryingDeliveryId !== null || loading || refreshing' in ui
    assert 'disabled={!hasNext || loading || refreshing || retryingDeliveryId !== null}' in ui
    assert 'disabled={!hasPrevious || loading || refreshing || retryingDeliveryId !== null}' in ui


def test_retry_action_failure_remains_distinct_from_read_failure():
    hook = source("frontend/src/alerts/useAlertDeliveries.ts")
    ui = source("frontend/src/alerts/AlertOperations.tsx")
    assert "const [retryError, setRetryError]" in hook
    assert "setRetryError(errorMessage(error));" in hook
    assert "retryError," in hook
    assert "retryError !== null && (" in ui
    assert "Delivery retry outcome unverified" in ui
    assert "Previously observed delivery data is retained." in ui


def test_retry_button_blocks_ambiguous_outcome_until_refresh():
    ui = source("frontend/src/alerts/AlertOperations.tsx")
    assert "retryBlocked: boolean;" in ui
    assert "|| retryBlocked" in ui
    assert "retryOutcomeUnverified={retryError !== null}" in ui
    assert "retryError !== null\n            || error !== null\n            || loading\n            || refreshing\n            || retryingDeliveryId !== null" in ui
    assert "Refresh the delivery ledger before retrying." in ui
