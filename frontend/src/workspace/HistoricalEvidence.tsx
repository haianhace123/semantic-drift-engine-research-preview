import type {
  TemporalReplayRiskAssessment,
  TemporalReplaySnapshot,
} from "../temporal/types";

import type {
  TemporalReplayState,
} from "../temporal/useTemporalReplay";


interface HistoricalEvidenceProps {
  cursorTime: string | null;
  replayIsCurrent: boolean;
  state: TemporalReplayState;
  selectedEventId: string | null;
  historicalSnapshot: TemporalReplaySnapshot | null;
  onRetry: () => void;
}


// Risk v2 is an event-scoped, persisted assessment. Organization-wide totals
// are not evidence that the selected change has been assessed.
function latestEventRisk(
  assessments: TemporalReplayRiskAssessment[],
  eventId: string | null,
): { assessment: TemporalReplayRiskAssessment; count: number } | null {
  if (eventId === null) {
    return null;
  }

  const matches = assessments.filter(
    (item) => item.semantic_drift_event_id === eventId
      && item.risk_version === "deterministic_v2",
  );

  if (matches.length === 0) {
    return null;
  }

  // The replay service returns assessments by assessed_at then id.
  // Sort explicitly here so a client does not depend on response order.
  matches.sort((left, right) =>
    right.assessed_at.localeCompare(left.assessed_at)
    || right.assessment_id.localeCompare(left.assessment_id),
  );

  return { assessment: matches[0], count: matches.length };
}


export function HistoricalEvidence({
  cursorTime,
  replayIsCurrent,
  state,
  selectedEventId,
  historicalSnapshot,
  onRetry,
}: HistoricalEvidenceProps) {
  const eventRecordedAtCursor = replayIsCurrent
    && state.status === "ready"
    && selectedEventId !== null
    && state.replay.events.some(
      (event) => event.event_id === selectedEventId,
    );

  const eventRisk = eventRecordedAtCursor && state.status === "ready"
    ? latestEventRisk(state.replay.risk_assessments, selectedEventId)
    : null;

  return (
    <section
      className="historical-evidence"
      aria-labelledby="temporal-evidence-title"
    >
      <h2 id="temporal-evidence-title">
        Historical evidence
      </h2>

      {cursorTime === null && (
        <p>
          No temporal cursor is active.
        </p>
      )}

      {cursorTime !== null && (
        <p>
          Temporal cursor: {cursorTime}
        </p>
      )}

      {state.status === "loading" && (
        <p
          role="status"
          aria-busy="true"
        >
          Reconstructing persisted knowledge state...
        </p>
      )}

      {state.status === "error" && (
        <div role="alert">
          <p>
            The historical knowledge state could
            not be reconstructed.
          </p>
          <button
            type="button"
            onClick={onRetry}
          >
            Retry historical evidence
          </button>
        </div>
      )}

      {replayIsCurrent && state.status === "ready" && (
        <>
          <p>
            {state.replay.snapshot_count}
            {" "}
            persisted snapshot
            {state.replay.snapshot_count === 1
              ? ""
              : "s"}
            {" "}
            at this cursor.
          </p>

          {selectedEventId !== null
          && historicalSnapshot === null && (
            <p>
              No historical snapshot is present
              for the selected monitored page at
              this cursor.
            </p>
          )}

          {historicalSnapshot !== null && (
            <>
              <dl className="historical-evidence-provenance">
                <div>
                  <dt>Snapshot</dt>
                  <dd>
                    {historicalSnapshot.snapshot_id}
                  </dd>
                </div>

                <div>
                  <dt>Observed</dt>
                  <dd>
                    <time
                      dateTime={
                        historicalSnapshot.crawled_at
                      }
                    >
                      {historicalSnapshot.crawled_at}
                    </time>
                  </dd>
                </div>
              </dl>
            </>
          )}

          <section
            className="historical-risk-evidence"
            aria-labelledby="historical-risk-title"
          >
            <h3 id="historical-risk-title">
              Deterministic risk (v2)
            </h3>
            <p>
              Assessments persisted by this cursor for the
              selected change, not the organization-wide total.
              Model-reported risk and downstream execution are separate.
            </p>

            {selectedEventId === null ? (
              <p data-risk-state="unselected">
                Select a recorded change to inspect its risk evidence.
              </p>
            ) : !eventRecordedAtCursor ? (
              <p data-risk-state="not-yet-recorded">
                This change is not recorded at the selected temporal
                cursor. No event-linked risk can be attributed yet.
              </p>
            ) : eventRisk === null ? (
              <p data-risk-state="unavailable">
                No deterministic v2 assessment for this change
                is recorded by this temporal cursor. Risk is unknown,
                not zero.
              </p>
            ) : (
              <dl
                className="historical-evidence-provenance"
                data-risk-state="recorded"
              >
                <div>
                  <dt>Score</dt>
                  <dd>
                    {Number.isFinite(eventRisk.assessment.score)
                      ? eventRisk.assessment.score.toFixed(1)
                      : "Unavailable"}
                  </dd>
                </div>
                <div>
                  <dt>Version</dt>
                  <dd>{eventRisk.assessment.risk_version}</dd>
                </div>
                <div>
                  <dt>Assessed</dt>
                  <dd>
                    <time dateTime={eventRisk.assessment.assessed_at}>
                      {eventRisk.assessment.assessed_at}
                    </time>
                  </dd>
                </div>
                <div>
                  <dt>Persisted</dt>
                  <dd>
                    <time dateTime={eventRisk.assessment.created_at}>
                      {eventRisk.assessment.created_at}
                    </time>
                  </dd>
                </div>
                <div>
                  <dt>Assessment ID</dt>
                  <dd>{eventRisk.assessment.assessment_id}</dd>
                </div>
                {eventRisk.count > 1 && (
                  <div>
                    <dt>Recorded</dt>
                    <dd>{eventRisk.count} assessments for this change</dd>
                  </div>
                )}
                <div>
                  <dt>Topology</dt>
                  <dd>
                    {eventRisk.assessment.has_topology_signal
                      && eventRisk.assessment.topology_contribution !== null
                      && Number.isFinite(eventRisk.assessment.topology_contribution)
                        ? `Contribution ${eventRisk.assessment.topology_contribution.toFixed(1)}`
                        : "No qualified topology signal"}
                  </dd>
                </div>
              </dl>
            )}
          </section>
        </>
      )}
    </section>
  );
}