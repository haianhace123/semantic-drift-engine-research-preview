import { apiGet, apiMutation } from "../api/client";

export type SourceType = "pricing" | "tos" | "privacy" | "sla" | "docs";
export type CrawlFrequency = "hourly" | "daily" | "weekly";

export interface MonitoredSource {
  id: string;
  entity_id: string;
  entity_name: string | null;
  url: string;
  page_type: SourceType;
  crawl_frequency: CrawlFrequency;
  is_active: boolean;
  last_crawled_at: string | null;
  next_crawl_at: string | null;
  snapshot_count?: number;
  last_crawl_attempt_at: string | null;
  last_crawl_status: "succeeded" | "failed" | null;
  last_crawl_failure_code: string | null;
  consecutive_crawl_failures: number;
}

export interface SourcesPage {
  total: number;
  limit: number;
  offset: number;
  items: MonitoredSource[];
}

export interface SourceDiagnostics {
  source_id: string;
  latest_snapshot: {
    id: string;
    crawled_at: string | null;
    embedding_status: string;
  } | null;
  latest_comparison: {
    old_snapshot_id: string;
    new_snapshot_id: string;
    content_changed: boolean;
    event_id: string | null;
    analysis_status: string | null;
    vector_analysis_status: string | null;
  } | null;
}

export function loadSourceDiagnostics(sourceId: string): Promise<SourceDiagnostics> {
  return apiGet<SourceDiagnostics>(`/api/sources/${encodeURIComponent(sourceId)}/diagnostics`);
}

export interface EnrollmentInput {
  url: string;
  label: string;
  page_type: SourceType;
  crawl_frequency: CrawlFrequency;
}

export function loadSources(offset: number, signal?: AbortSignal): Promise<SourcesPage> {
  return apiGet<SourcesPage>(`/api/sources?limit=25&offset=${offset}`, signal);
}

export function enrollSource(body: EnrollmentInput): Promise<MonitoredSource> {
  return apiMutation<MonitoredSource, EnrollmentInput>("/api/sources", "POST", body);
}

export function setSourceActive(sourceId: string, active: boolean): Promise<MonitoredSource> {
  const action = active ? "resume" : "pause";
  return apiMutation<MonitoredSource>(`/api/sources/${encodeURIComponent(sourceId)}/${action}`, "POST");
}

export function updateSourceFrequency(sourceId: string, frequency: CrawlFrequency): Promise<MonitoredSource> {
  return apiMutation<MonitoredSource, { crawl_frequency: CrawlFrequency }>(
    `/api/sources/${encodeURIComponent(sourceId)}/frequency`, "POST", { crawl_frequency: frequency },
  );
}
