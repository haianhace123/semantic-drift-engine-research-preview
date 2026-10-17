import type { TemporalEvent } from "./types";

/** An explicit event URL never resolves to a different event. */
export function useTemporalSelection(
  events: TemporalEvent[] | null,
  requestedEventId: string | null,
  directEvent: TemporalEvent | null = null,
) {
  const selectedEvent = events === null
    ? null
    : requestedEventId === null
      ? events[events.length - 1] ?? null
      : events.find((event) => event.id === requestedEventId)
        ?? (directEvent?.id === requestedEventId ? directEvent : null);

  return {
    selectedEventId: selectedEvent?.id ?? null,
    selectedEvent,
  };
}
