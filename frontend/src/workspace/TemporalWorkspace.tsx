import { useEffect, useRef, useState } from "react";

import type { CurrentUser } from "../api/auth";
import { AccountMenu } from "../account/AccountMenu";
import { AccountSecurity } from "../account/AccountSecurity";
import { AlertOperations } from "../alerts/AlertOperations";
import { useTemporalIntegrity } from "../integrity/useTemporalIntegrity";
import { replaySnapshotForEvent } from "../temporal/replay";
import { useTemporalCursor } from "../temporal/useTemporalCursor";
import { useTemporalEvents } from "../temporal/useTemporalEvents";
import { useTemporalEvidence } from "../temporal/useTemporalEvidence";
import { useTemporalEventDetail } from "../temporal/useTemporalEventDetail";
import { useTemporalReplay, type TemporalReplayState } from "../temporal/useTemporalReplay";
import { useTemporalSelection } from "../temporal/useTemporalSelection";
import { ChangeIndex } from "./ChangeIndex";
import { Chronoscope } from "./Chronoscope";
import { HistoricalEvidence } from "./HistoricalEvidence";
import { IntegrityRecord } from "./IntegrityRecord";
import { OperatorFocus } from "./OperatorFocusPanel";
import { SemanticEvidence } from "./SemanticEvidence";
import { SourceMonitoring } from "./SourceMonitoring";
import { useWorkspaceRoute } from "./useWorkspaceRoute";
import { eventHash } from "./workspaceRoute";

export function TemporalWorkspace({ user }: { user: CurrentUser }) {
  const [currentUser, setCurrentUser] = useState(user);
  const { route, navigate } = useWorkspaceRoute();

  useEffect(() => {
    const viewTitle = route.view === "account"
      ? "Account & security"
      : route.view === "sources"
        ? "Source monitoring"
        : route.view === "deliveries"
          ? "Alert operations"
          : "Change investigation";
    document.title = `${viewTitle} · Semantic Drift Engine`;
  }, [route.view]);

  const lastEventId = useRef<string | null>(
    route.view === "investigation" ? route.eventId : null,
  );
  const previousView = useRef(route.view);

  const { state: eventsState, reload: reloadEvents } = useTemporalEvents(
    route.view === "investigation",
  );
  const events = route.view === "investigation" && eventsState.status === "ready"
    ? eventsState.events
    : null;
  const requestedEventId = route.view === "investigation" ? route.eventId : null;
  const eventOutsideWindow = events !== null && requestedEventId !== null
    && !events.some((event) => event.id === requestedEventId);
  const { state: directEventState, reload: reloadDirectEvent } = useTemporalEventDetail(
    eventOutsideWindow ? requestedEventId : null,
  );
  const directEvent = directEventState.status === "ready"
    && directEventState.eventId === requestedEventId
    && directEventState.event.id === requestedEventId
      ? directEventState.event
      : null;
  const { selectedEventId, selectedEvent } = useTemporalSelection(
    events,
    requestedEventId,
    directEvent,
  );
  // This window is incomplete. Include an authenticated direct event solely
  // as a selected point; do not imply that its intervening history is loaded.
  const replayIndex = selectedEvent !== null
    && events !== null
    && !events.some((event) => event.id === selectedEvent.id)
      ? [...events, selectedEvent].sort((left, right) =>
          (left.detected_at ?? "").localeCompare(right.detected_at ?? "")
          || left.id.localeCompare(right.id),
        )
      : events;


  const { state: integrityState, reload: reloadIntegrity } =
    useTemporalIntegrity(selectedEventId);
  const { state: evidenceState, reload: reloadEvidence } =
    useTemporalEvidence(selectedEventId);
  // Async request effects run after render. Do not briefly display evidence
  // from the preceding event under the newly selected event's heading.
  const scopedIntegrityState = selectedEventId !== null
    && (integrityState.status === "idle" || integrityState.eventId !== selectedEventId)
      ? { status: "loading" as const, eventId: selectedEventId }
      : integrityState;
  const scopedEvidenceState = selectedEventId !== null
    && (evidenceState.status === "idle" || evidenceState.eventId !== selectedEventId)
      ? { status: "loading" as const, eventId: selectedEventId }
      : evidenceState;

  const { cursorTime, moveCursor, moveCursorToEvent } =
    useTemporalCursor(selectedEvent);
  const { state: replayState, reload: reloadReplay } =
    useTemporalReplay(route.view === "investigation" && selectedEvent !== null ? cursorTime : null);

  // A temporal cursor can change before the next request effect runs.
  // Never attribute the prior cursor's loading/error state to this cursor.
  const scopedReplayState: TemporalReplayState = cursorTime === null
    ? { status: "idle" }
    : replayState.status === "idle" || replayState.cursorTime !== cursorTime
      ? { status: "loading", cursorTime }
      : replayState;

  const replayIsCurrent =
    cursorTime !== null
    && scopedReplayState.status === "ready"
    && scopedReplayState.cursorTime === cursorTime;

  const historicalSnapshot = selectedEvent !== null && replayIsCurrent
    && scopedReplayState.status === "ready"
    ? replaySnapshotForEvent(scopedReplayState.replay, selectedEvent)
    : null;

  useEffect(() => {
    if (route.view === "investigation") {
      lastEventId.current = route.eventId ?? selectedEventId;
    }
  }, [route.view, route.eventId, selectedEventId]);

  // Back/Forward to another event must restore the temporal cursor too.
  useEffect(() => {
    if (route.view === "investigation" && route.eventId && selectedEvent) {
      moveCursorToEvent(selectedEvent);
    }
  }, [route.view, route.eventId, selectedEvent, moveCursorToEvent]);

  useEffect(() => {
    if (previousView.current === route.view) return;
    previousView.current = route.view;
    const titleId = route.view === "account"
      ? "accountSecurityTitle"
      : route.view === "deliveries"
        ? "alert-operations-title"
        : "workspace-view-title";
    document.getElementById(titleId)?.focus();
  }, [route.view]);

  function investigationHash() {
    return lastEventId.current ? eventHash(lastEventId.current) : "#integrity";
  }

  function openInvestigation() {
    navigate(investigationHash());
  }

  function investigateEvent(eventId: string) {
    navigate(eventHash(eventId));
  }

  const awaiting = eventsState.status === "loading";
  const failed = eventsState.status === "error";
  const missingLinkedEvent =
    route.view === "investigation"
    && eventsState.status === "ready"
    && route.eventId !== null
    && selectedEvent === null;
  const directLookupLoading = missingLinkedEvent
    && (directEventState.status === "idle"
      || directEventState.eventId !== route.eventId
      || directEventState.status === "loading");
  const directLookupMissing = missingLinkedEvent
    && directEventState.status === "not_found";
  const directLookupFailed = missingLinkedEvent
    && directEventState.status === "error"
    && directEventState.eventId === route.eventId;

  return (
    <main className="temporal-workspace" aria-busy={route.view === "investigation" && awaiting}>
      <header className="workspace-header">
        <div className="workspace-identity">
          <img
            className="workspace-mark"
            src="/dashboard/assets/brand/logo.svg"
            alt=""
            aria-hidden="true"
            width={44}
            height={44}
          />
          <div>
            <h1>Temporal Knowledge Integrity</h1>
            <p>Observe sources, investigate evidence and replay knowledge at a point in time.</p>
          </div>
        </div>

        <div className="workspace-header-actions">
          <nav className="workspace-view-nav" aria-label="Workspace views">
            <a
              href={investigationHash()}
              aria-current={route.view === "investigation" ? "page" : undefined}
            >
              Investigation
            </a>
            <a href="#sources" aria-current={route.view === "sources" ? "page" : undefined}>
              Sources
            </a>
            <a href="#deliveries" aria-current={route.view === "deliveries" ? "page" : undefined}>
              Deliveries
            </a>
          </nav>
          <AccountMenu user={currentUser} onOpenAccount={() => navigate("#account")} />
        </div>
      </header>

      {route.view === "sources" ? (
        <SourceMonitoring
          canManage={currentUser.role !== "viewer"}
          onInvestigateEvent={investigateEvent}
        />
      ) : route.view === "deliveries" ? (
        <AlertOperations canRetry={currentUser.role !== "viewer"} />
      ) : route.view === "account" ? (
        <AccountSecurity
          user={currentUser}
          onClose={openInvestigation}
          onUserChanged={setCurrentUser}
        />
      ) : eventsState.status !== "ready" ? (
        <section className="workspace-initial-state">
          <h2 id="workspace-view-title" tabIndex={-1}>
            {failed ? "Change history unavailable" : "No recorded change history yet"}
          </h2>
          <p>
            {awaiting
              ? "Loading the recorded change history..."
              : failed
                ? "The temporal event stream could not be loaded. Source management and alert deliveries remain available."
                : "Enroll a public document to begin monitoring. Recorded content revisions appear here, including revisions with no interpreted meaning shift."}
          </p>
          {failed ? <button type="button" onClick={reloadEvents}>Retry history</button> : null}
          <button type="button" onClick={() => navigate("#sources")}>Add or inspect sources</button>
        </section>
      ) : (
        <>
          <h2 className="workspace-view-heading" id="workspace-view-title" tabIndex={-1}>
            Change investigation
          </h2>
          {directEvent !== null && eventOutsideWindow && !missingLinkedEvent ? (
            <section className="workspace-window-limitation" role="status">
              <strong>Exact event outside the loaded history window</strong>
              <p>Event identity is available, but the Change Index and timeline are partial. Intervening events may be missing.</p>
            </section>
          ) : null}
          {!missingLinkedEvent ? (
            <>
              <OperatorFocus selectedEvent={selectedEvent} state={scopedIntegrityState} />
              <section className="workspace-temporal-access" aria-label="Historical time navigation">
                <Chronoscope
                  events={replayIndex ?? eventsState.events}
                  totalEvents={eventsState.total}
                  selectedEventId={selectedEventId}
                  cursorTime={cursorTime}
                  replayIsCurrent={replayIsCurrent}
                  replayState={scopedReplayState}
                  onMoveCursor={moveCursor}
                />
              </section>
            </>
          ) : null}
          <div className="workspace-body">
            <aside className="workspace-change-rail" aria-label="Recorded change navigation">
              <ChangeIndex
                events={eventsState.events}
                total={eventsState.total}
                selectedEventId={selectedEventId}
                onSelectEvent={(event) => {
                  investigateEvent(event.id);
                  moveCursorToEvent(event);
                }}
              />
            </aside>
            <div className="workspace-investigation">
              {missingLinkedEvent ? (
                <section className="workspace-event-unavailable" role="status">
                  <h3>
                    {directLookupLoading ? "Retrieving requested event"
                      : directLookupMissing ? "Event unavailable"
                        : "Event could not be retrieved"}
                  </h3>
                  <p>
                    {directLookupLoading
                      ? "Looking up the exact event beyond the loaded history window."
                      : directLookupMissing
                        ? "This event does not exist or is not accessible to your organization."
                        : "The exact event could not be retrieved. No other event was substituted."}
                  </p>
                  {directLookupFailed ? (
                    <button type="button" onClick={reloadDirectEvent}>Retry event lookup</button>
                  ) : null}
                  <button type="button" onClick={() => navigate("#integrity")}>
                    Return to loaded change index
                  </button>
                </section>
              ) : (
                <>
                  <IntegrityRecord
                    selectedEvent={selectedEvent}
                    state={scopedIntegrityState}
                    onRetry={reloadIntegrity}
                  />
                  <SemanticEvidence state={scopedEvidenceState} onRetry={reloadEvidence} />
                </>
              )}
            </div>
          </div>

          {!missingLinkedEvent ? (
            <section className="workspace-historical-plane" aria-label="Historical evidence at temporal cursor">
              <HistoricalEvidence
                cursorTime={cursorTime}
                replayIsCurrent={replayIsCurrent}
                state={scopedReplayState}
                selectedEventId={selectedEventId}
                historicalSnapshot={historicalSnapshot}
                onRetry={reloadReplay}
              />
            </section>
          ) : null}
        </>
      )}
    </main>
  );
}
