import {
  useRef,
} from "react";

import type {
  TemporalEvent,
} from "../temporal/types";

import type {
  TemporalReplayState,
} from "../temporal/useTemporalReplay";


interface ChronoscopeProps {
  events: TemporalEvent[];
  totalEvents: number;
  selectedEventId: string | null;
  cursorTime: string | null;
  replayIsCurrent: boolean;
  replayState: TemporalReplayState;
  onMoveCursor: (cursorTime: string) => void;
}


// A temporal cursor represents an instant, not a particular event.
// Several events can be detected at the same instant, and must share one
// focusable marker rather than covering each other with identical buttons.
interface TemporalMoment {
  timestamp: number;
  detectedAt: string;
  events: TemporalEvent[];
}


function temporalMoments(
  events: TemporalEvent[],
): TemporalMoment[] {
  const byTimestamp = new Map<number, TemporalMoment>();

  for (const event of events) {
    if (event.detected_at === null) {
      continue;
    }

    const timestamp = Date.parse(event.detected_at);
    if (!Number.isFinite(timestamp)) {
      continue;
    }

    const existing = byTimestamp.get(timestamp);
    if (existing) {
      existing.events.push(event);
      // Different ISO spellings of one instant must not change the cursor
      // identity depending on server response ordering.
      existing.detectedAt = new Date(timestamp).toISOString();
    } else {
      byTimestamp.set(timestamp, {
        timestamp,
        detectedAt: event.detected_at,
        events: [event],
      });
    }
  }

  return [...byTimestamp.values()].sort(
    (left, right) => left.timestamp - right.timestamp,
  );
}


function formatCursorTime(
  cursorTime: string | null,
) {
  if (cursorTime === null) {
    return {
      date: "No cursor",
      time: "Select a recorded change",
    };
  }

  const date = new Date(cursorTime);

  if (Number.isNaN(date.getTime())) {
    return {
      date: "Cursor time unavailable",
      time: cursorTime,
    };
  }

  return {
    date: new Intl.DateTimeFormat(
      undefined,
      {
        year: "numeric",
        month: "short",
        day: "2-digit",
      },
    ).format(date),
    time: new Intl.DateTimeFormat(
      undefined,
      {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      },
    ).format(date),
  };
}


function pointPosition(
  point: TemporalMoment,
  firstTimestamp: number,
  lastTimestamp: number,
) {
  if (firstTimestamp === lastTimestamp) {
    return 50;
  }

  return (
    (
      point.timestamp - firstTimestamp
    )
    / (
      lastTimestamp - firstTimestamp
    )
  ) * 100;
}


function adjacentTemporalMoments(
  moments: TemporalMoment[],
  cursorTime: string | null,
) {
  if (moments.length === 0) {
    return {
      previous: null,
      next: null,
    };
  }

  if (cursorTime === null) {
    return {
      previous: null,
      next: moments[0],
    };
  }

  const cursorTimestamp = Date.parse(
    cursorTime,
  );

  if (!Number.isFinite(cursorTimestamp)) {
    return {
      previous: null,
      next: null,
    };
  }

  let previous: TemporalMoment | null = null;
  let next: TemporalMoment | null = null;

  for (const point of moments) {
    if (point.timestamp < cursorTimestamp) {
      previous = point;
      continue;
    }

    if (point.timestamp > cursorTimestamp) {
      next = point;
      break;
    }
  }

  return {
    previous,
    next,
  };
}


export function Chronoscope({
  events,
  totalEvents,
  selectedEventId,
  cursorTime,
  replayIsCurrent,
  replayState,
  onMoveCursor,
}: ChronoscopeProps) {
  const momentRefs = useRef(
    new Map<number, HTMLButtonElement>(),
  );

  const moments = temporalMoments(events);
  const cursor = formatCursorTime(cursorTime);

  const {
    previous: previousMoment,
    next: nextMoment,
  } = adjacentTemporalMoments(
    moments,
    cursorTime,
  );

  const hasUnloadedEvents =
    totalEvents > events.length;

  const loadedWindowLabel =
    hasUnloadedEvents
      ? (
        `${events.length} of ${totalEvents} `
        + "changes loaded"
      )
      : `${events.length} changes loaded`;

  const firstTimestamp =
    moments.length > 0
      ? moments[0].timestamp
      : 0;

  const lastTimestamp =
    moments.length > 0
      ? moments[moments.length - 1].timestamp
      : 0;

  const repeatedMomentCount = moments.filter(
    (moment) => moment.events.length > 1,
  ).length;

  const moveKeyboardCursor = (
    moment: TemporalMoment,
  ) => {
    onMoveCursor(moment.detectedAt);
    momentRefs.current.get(moment.timestamp)?.focus();
  };

  return (
    <section
      className="chronoscope"
      aria-labelledby="chronoscope-title"
    >
      <div className="chronoscope-heading">
        <div>
          <h2 id="chronoscope-title">
            Chronoscope
          </h2>
          <p>
            Navigate recorded change time and inspect
            persisted knowledge at the temporal cursor.
          </p>
          <span className="chronoscope-window">
            {loadedWindowLabel}
            {repeatedMomentCount > 0 && (
              <>
                {" / "}
                {repeatedMomentCount} shared-time
                {repeatedMomentCount === 1 ? " marker" : " markers"}
              </>
            )}
          </span>
        </div>

        <div
          className="chronoscope-cursor-readout"
          aria-live="polite"
        >
          <span>Temporal cursor</span>
          <strong>{cursor.date}</strong>
          <time dateTime={cursorTime ?? undefined}>
            {cursor.time}
          </time>
        </div>
      </div>

      {moments.length === 0 ? (
        <p className="chronoscope-unavailable">
          No timestamped changes are available for
          temporal navigation.
        </p>
      ) : (
        <>
          <div className="chronoscope-controls">
            <button
              type="button"
              disabled={previousMoment === null}
              onClick={() => {
                if (previousMoment !== null) {
                  onMoveCursor(previousMoment.detectedAt);
                }
              }}
            >
              Previous change
            </button>

            <div
              className="chronoscope-track"
              role="group"
              aria-label="Loaded recorded change timeline; each marker represents one recorded instant"
            >
              <span
                className="chronoscope-axis"
                aria-hidden="true"
              />

              {moments.map((moment, index) => {
                const selected = moment.events.some(
                  (event) => event.id === selectedEventId,
                );

                const atCursor = cursorTime !== null
                  && Date.parse(cursorTime) === moment.timestamp;

                const markerLabel = moment.events.length > 1
                  ? `Move temporal cursor to ${moment.events.length} loaded changes at ${moment.detectedAt}. Select a specific change in the Change index.`
                  : `Move temporal cursor to ${moment.events[0].entity_name} at ${moment.detectedAt}`;

                return (
                  <button
                    key={moment.timestamp}
                    ref={(element) => {
                      if (element === null) {
                        momentRefs.current.delete(moment.timestamp);
                      } else {
                        momentRefs.current.set(moment.timestamp, element);
                      }
                    }}
                    className="chronoscope-point"
                    type="button"
                    style={{
                      left: `${pointPosition(
                        moment,
                        firstTimestamp,
                        lastTimestamp,
                      )}%`,
                    }}
                    aria-label={markerLabel}
                    aria-pressed={atCursor}
                    data-event-count={moment.events.length}
                    data-selected-event={selected ? "true" : "false"}
                    onClick={() => onMoveCursor(moment.detectedAt)}
                    onKeyDown={(event) => {
                      let targetIndex: number | null = null;
                      if (event.key === "ArrowLeft" && index > 0) {
                        targetIndex = index - 1;
                      } else if (event.key === "ArrowRight" && index < moments.length - 1) {
                        targetIndex = index + 1;
                      } else if (event.key === "Home") {
                        targetIndex = 0;
                      } else if (event.key === "End") {
                        targetIndex = moments.length - 1;
                      }

                      if (targetIndex !== null) {
                        event.preventDefault();
                        moveKeyboardCursor(moments[targetIndex]);
                      }
                    }}
                  >
                    <span aria-hidden="true" />
                    {moment.events.length > 1 && (
                      <span className="chronoscope-point-count" aria-hidden="true">
                        {moment.events.length}
                      </span>
                    )}
                  </button>
                );
              })}
            </div>

            <button
              type="button"
              disabled={nextMoment === null}
              onClick={() => {
                if (nextMoment !== null) {
                  onMoveCursor(nextMoment.detectedAt);
                }
              }}
            >
              Next change
            </button>
          </div>

          <div
            className="chronoscope-state"
            aria-live="polite"
            aria-label="Organization-wide replay counts at the temporal cursor"
          >
            {replayState.status === "loading" && (
              <span>
                Reconstructing this point...
              </span>
            )}

            {replayState.status === "error" && (
              <span>
                Replay unavailable at this cursor.
              </span>
            )}

            {replayIsCurrent
            && replayState.status === "ready" && (
              <>
                <span>
                  {replayState.replay.snapshot_count}
                  {" "}
                  snapshot
                  {replayState.replay.snapshot_count
                    === 1
                    ? ""
                    : "s"}
                </span>
                <span aria-hidden="true">/</span>
                <span>
                  {replayState.replay.event_count}
                  {" "}
                  event
                  {replayState.replay.event_count
                    === 1
                    ? ""
                    : "s"}
                </span>
                <span aria-hidden="true">/</span>
                <span>
                  {
                    replayState.replay
                      .risk_assessment_count
                  }
                  {" "}
                  risk assessment
                  {
                    replayState.replay
                      .risk_assessment_count === 1
                      ? ""
                      : "s"
                  }
                </span>
              </>
            )}
          </div>
        </>
      )}
    </section>
  );
}