import type {
  TemporalEvent,
} from "../temporal/types";


interface ChangeIndexProps {
  events: TemporalEvent[];
  total: number;
  selectedEventId: string | null;
  onSelectEvent: (event: TemporalEvent) => void;
}


function formatDetectedAt(
  detectedAt: string | null,
) {
  if (detectedAt === null) {
    return {
      date: "Time unavailable",
      time: null,
      machine: null,
    };
  }

  const date = new Date(detectedAt);

  if (Number.isNaN(date.getTime())) {
    return {
      date: "Time unavailable",
      time: null,
      machine: detectedAt,
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
    machine: detectedAt,
  };
}


export function ChangeIndex({
  events,
  total,
  selectedEventId,
  onSelectEvent,
}: ChangeIndexProps) {
  const hasUnloadedEvents =
    total > events.length;

  const loadedCountLabel =
    hasUnloadedEvents
      ? `${events.length} of ${total} loaded`
      : `${events.length} loaded`;

  return (
    <section
      className="change-index"
      aria-labelledby="change-history-title"
    >
      <header className="change-index-header">
        <div>
          <h2 id="change-history-title">
            Change index
          </h2>
          <p className="change-index-description">
            Latest recorded content revisions
          </p>
        </div>

        <p
          className="change-index-count"
          aria-label={loadedCountLabel}
        >
          {hasUnloadedEvents
            ? `${events.length} / ${total}`
            : events.length}
        </p>
      </header>

      <ol className="change-index-list">
        {[...events].reverse().map((event) => {
          const detectedAt = formatDetectedAt(
            event.detected_at,
          );

          const selected =
            event.id === selectedEventId;

          return (
            <li
              key={event.id}
              className="change-index-item"
            >
              <button
                className="change-index-event"
                type="button"
                aria-pressed={selected}
                onClick={() => {
                  onSelectEvent(event);
                }}
              >
                <span className="change-index-event-topline">
                  <span
                    className="change-index-risk"
                    data-risk={event.risk_level.toLowerCase()}
                    title="Model-reported severity; deterministic risk scoring is separate."
                  >
                    Model {event.risk_level}
                  </span>

                  <time
                    className="change-index-time"
                    dateTime={
                      detectedAt.machine
                      ?? undefined
                    }
                  >
                    <span>{detectedAt.date}</span>
                    {detectedAt.time !== null && (
                      <span>{detectedAt.time}</span>
                    )}
                  </time>
                </span>

                <span className="change-index-entity">
                  {event.entity_name}
                </span>

                <span className="change-index-summary">
                  {event.summary}
                </span>

                <span className="change-index-context">
                  <span>{event.domain}</span>
                  <span aria-hidden="true">/</span>
                  <span>{event.page_type}</span>
                </span>
              </button>
            </li>
          );
        })}
      </ol>
    </section>
  );
}