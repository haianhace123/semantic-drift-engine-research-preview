
import { apiGet } from "../api/client";
import type { TemporalIntegrityResolution } from "./types";

export function getTemporalIntegrity(
  eventId: string,
  signal?: AbortSignal,
): Promise<TemporalIntegrityResolution> {
  return apiGet<TemporalIntegrityResolution>(
    `/api/temporal/events/${encodeURIComponent(eventId)}/integrity`,
    signal,
  );
}
