import { apiGet } from "../api/client";

import type {
  TemporalEvent,
  TemporalEventEvidence,
  TemporalEventStream,
  TemporalReplay,
} from "./types";

const TEMPORAL_EVENTS_PATH =
  "/api/temporal/events";

export interface TemporalEventQuery {
  start?: string;
  end?: string;
  entityIds?: string[];
  limit?: number;
  offset?: number;
  order?: "asc" | "desc";
}

function temporalEventsPath(
  query: TemporalEventQuery = {},
): `/api/${string}` {
  const params = new URLSearchParams();

  if (query.start) {
    params.set(
      "start",
      query.start,
    );
  }

  if (query.end) {
    params.set(
      "end",
      query.end,
    );
  }

  for (
    const entityId
    of query.entityIds ?? []
  ) {
    params.append(
      "entity_ids",
      entityId,
    );
  }

  if (query.limit !== undefined) {
    params.set(
      "limit",
      String(query.limit),
    );
  }

  if (query.offset !== undefined) {
    params.set(
      "offset",
      String(query.offset),
    );
  }

  if (query.order) {
    params.set("order", query.order);
  }

  const search = params.toString();

  return search
    ? `${TEMPORAL_EVENTS_PATH}?${search}`
    : TEMPORAL_EVENTS_PATH;
}

export function getTemporalEvents(
  query: TemporalEventQuery = {},
  signal?: AbortSignal,
): Promise<TemporalEventStream> {
  return apiGet<TemporalEventStream>(
    temporalEventsPath(query),
    signal,
  );
}

export function getTemporalEventById(
  eventId: string,
  signal?: AbortSignal,
): Promise<TemporalEvent> {
  return apiGet<TemporalEvent>(
    `/api/temporal/events/${encodeURIComponent(eventId)}`,
    signal,
  );
}

export function getTemporalEventEvidence(
  eventId: string,
  signal?: AbortSignal,
): Promise<TemporalEventEvidence> {
  return apiGet<TemporalEventEvidence>(
    `/api/temporal/events/${encodeURIComponent(eventId)}/evidence`,
    signal,
  );
}

export function getTemporalReplay(
  at: string,
  signal?: AbortSignal,
): Promise<TemporalReplay> {
  const params = new URLSearchParams({
    at,
  });

  return apiGet<TemporalReplay>(
    `/api/temporal/replay?${params.toString()}`,
    signal,
  );
}