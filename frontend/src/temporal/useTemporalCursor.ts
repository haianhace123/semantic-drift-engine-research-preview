import {
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import type {
  TemporalEvent,
} from "./types";

export function useTemporalCursor(
  selectedEvent: TemporalEvent | null,
) {
  const [
    cursorTime,
    setCursorTime,
  ] = useState<string | null>(null);

  const activeEventId = useRef<string | null>(null);

  useEffect(() => {
    if (selectedEvent === null) {
      return;
    }

    // A change of event identity must select that event's revision time.
    // A manual temporal cursor adjustment survives rerenders of the same event.
    if (activeEventId.current !== selectedEvent.id) {
      activeEventId.current = selectedEvent.id;
      setCursorTime(selectedEvent.detected_at);
    }
  }, [selectedEvent]);

  const moveCursor = useCallback(
    (nextCursorTime: string) => {
      setCursorTime(
        nextCursorTime,
      );
    },
    [],
  );

  const moveCursorToEvent = useCallback(
    (event: TemporalEvent) => {
      setCursorTime(
        event.detected_at,
      );
    },
    [],
  );

  const clearCursor = useCallback(() => {
    setCursorTime(null);
  }, []);

  // Never expose a cursor from the preceding event while route state is
  // changing; its replay can otherwise be mistaken for the new selection.
  const visibleCursorTime = selectedEvent !== null
    && activeEventId.current === selectedEvent.id
      ? cursorTime
      : null;

  return {
    cursorTime: visibleCursorTime,
    moveCursor,
    moveCursorToEvent,
    clearCursor,
  };
}