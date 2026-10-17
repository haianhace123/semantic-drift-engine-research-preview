import { useCallback, useEffect, useState, type FormEvent } from "react";

import { ApiError } from "../api/client";
import {
  enrollSource,
  loadSources,
  loadSourceDiagnostics,
  setSourceActive,
  updateSourceFrequency,
  type CrawlFrequency,
  type EnrollmentInput,
  type MonitoredSource,
  type SourceType,
  type SourcesPage,
  type SourceDiagnostics,
} from "../sources/api";

const PAGE_SIZE = 25;
const SOURCE_TYPES: SourceType[] = ["docs", "tos", "privacy", "sla", "pricing"];
const FREQUENCIES: CrawlFrequency[] = ["hourly", "daily", "weekly"];

function timeLabel(value: string | null): string {
  if (!value) return "Not recorded";
  const time = Date.parse(value);
  return Number.isNaN(time) ? "Unavailable" : new Date(time).toLocaleString();
}

const FAILURE_EXPLANATIONS: Record<string, string> = {
  blocked_destination: "The destination or redirect was blocked by network safety rules. Check the public HTTPS URL.",
  fetch_failed: "The site could not be fetched as public HTML. Check reachability, content type and page size.",
  content_rejected: "The extracted document was empty, too short or too large for monitoring.",
  unsupported_source: "The URL is not supported by the configured crawler adapter.",
  snapshot_unavailable: "The crawler did not produce a persisted snapshot. Check the document and retry schedule.",
  internal_error: "The crawler encountered an internal error. Contact the operator if this persists.",
};

function stage(source: MonitoredSource): { title: string; detail: string } {
  if (!source.is_active) return {
    title: "Paused",
    detail: source.last_crawl_status === "failed"
      ? `Scheduled crawls are disabled. Previous failure: ${FAILURE_EXPLANATIONS[source.last_crawl_failure_code ?? ""] ?? "crawl did not complete"}`
      : "Scheduled crawls are disabled.",
  };
  if (source.last_crawl_status === "failed") return {
    title: "Crawl retry scheduled",
    detail: FAILURE_EXPLANATIONS[source.last_crawl_failure_code ?? ""]
      ?? "The previous crawl failed. The scheduler will retry when eligible.",
  };
  if (source.snapshot_count === undefined) {
    return source.last_crawled_at
      ? { title: "Crawled", detail: "Snapshot history is not available in this response." }
      : { title: "Awaiting first crawl", detail: "No successful scheduled crawl recorded." };
  }
  if (source.snapshot_count === 0) return {
    title: "Awaiting first snapshot", detail: "The scheduler has not persisted a snapshot yet.",
  };
  if (source.snapshot_count === 1) return {
    title: "Baseline captured", detail: "Another distinct content version is needed for drift comparison.",
  };
  return {
    title: "Snapshot history available", detail: "Multiple snapshots exist; analysis completion is tracked separately.",
  };
}

function diagnosis(evidence: SourceDiagnostics): string {
  if (!evidence.latest_snapshot) {
    return "No persisted snapshot. A crawl may be scheduled, in progress or retrying; a failure reason is not stored here.";
  }
  if (!evidence.latest_comparison) {
    return "One persisted baseline. Another snapshot is required before the content can be compared.";
  }
  const comparison = evidence.latest_comparison;
  if (!comparison.content_changed) {
    return "The latest two persisted snapshots have identical content hashes; no change was detected for this pair.";
  }
  if (!comparison.event_id) {
    return "Content hashes differ, but no semantic event is persisted for this exact pair. Analysis may be incomplete or unavailable; this is not a risk decision.";
  }
  if (comparison.analysis_status === "failed") {
    return "A content change was recorded, but semantic analysis failed. No successful semantic-risk finding is available for this pair.";
  }
  if (comparison.analysis_status === "completed") {
    return "Semantic analysis completed for this pair. Vector analysis and downstream impact have separate evidence requirements.";
  }
  return "A content change has a persisted semantic event, but semantic analysis is not recorded as completed.";
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.kind === "unauthenticated") return "Session expired. Sign in again to manage sources.";
    if (error.kind === "forbidden") return "Your role does not permit this action.";
    if (error.status === 409) return error.message || "This source cannot be changed right now.";
    return error.message;
  }
  return "The request could not be completed. Please retry.";
}

export function SourceMonitoring({ canManage, onInvestigateEvent }: {
  canManage: boolean;
  onInvestigateEvent: (eventId: string) => void;
}) {
  const [page, setPage] = useState<SourcesPage | null>(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [problem, setProblem] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [inspectingId, setInspectingId] = useState<string | null>(null);
  const [inspection, setInspection] = useState<{ sourceId: string; evidence: SourceDiagnostics } | null>(null);
  const [inspectionError, setInspectionError] = useState<{ sourceId: string; message: string } | null>(null);
  const [url, setUrl] = useState("");
  const [label, setLabel] = useState("");
  const [type, setType] = useState<SourceType>("docs");
  const [frequency, setFrequency] = useState<CrawlFrequency>("daily");

  const refresh = useCallback((signal?: AbortSignal) => {
    setLoading(true);
    return loadSources(offset, signal).then((data) => {
      if (!signal?.aborted) {
        setPage(data);
        setProblem(null);
      }
    }).catch((error: unknown) => {
      if (!signal?.aborted) {
        setPage(null);
        setProblem(errorMessage(error));
      }
    }).finally(() => {
      if (!signal?.aborted) setLoading(false);
    });
  }, [offset]);

  useEffect(() => {
    const controller = new AbortController();
    void refresh(controller.signal);
    return () => controller.abort();
  }, [refresh]);

  async function inspectSource(sourceId: string) {
    if (inspectingId !== null) return;
    if (inspection?.sourceId === sourceId) {
      setInspection(null);
      setInspectionError(null);
      return;
    }
    setInspectingId(sourceId);
    setInspectionError(null);
    try {
      const evidence = await loadSourceDiagnostics(sourceId);
      setInspection({ sourceId, evidence });
    } catch (error) {
      setInspection(null);
      setInspectionError({ sourceId, message: errorMessage(error) });
    } finally {
      setInspectingId(null);
    }
  }

  async function addSource(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!canManage || submitting) return;
    setSubmitting(true);
    setNotice(null);
    setProblem(null);
    try {
      const body: EnrollmentInput = {
        url: url.trim(), label: label.trim(), page_type: type, crawl_frequency: frequency,
      };
      await enrollSource(body);
      setUrl("");
      setLabel("");
      setNotice("Source enrolled. The scheduler will capture its first snapshot when eligible.");
      if (offset === 0) await refresh();
      else setOffset(0);
    } catch (error) {
      setProblem(errorMessage(error));
    } finally {
      setSubmitting(false);
    }
  }

  async function changeSource(source: MonitoredSource, action: "pause" | "resume" | "frequency", nextFrequency?: CrawlFrequency) {
    if (!canManage || busyId !== null) return;
    setBusyId(source.id);
    setProblem(null);
    setNotice(null);
    try {
      if (action === "frequency" && nextFrequency) {
        await updateSourceFrequency(source.id, nextFrequency);
        setNotice("Monitoring frequency updated. The scheduler will reschedule this source.");
      } else {
        await setSourceActive(source.id, action === "resume");
        setNotice(action === "resume" ? "Monitoring resumed." : "Monitoring paused.");
      }
      await refresh();
    } catch (error) {
      setProblem(errorMessage(error));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section className="source-monitor" aria-label="Knowledge source monitoring">
      <div className="source-monitor-heading">
        <div>
          <h2 id="workspace-view-title" tabIndex={-1}>Source monitoring</h2>
          <p>Enroll public HTTPS documents. A source is not a semantic finding until snapshots have been compared and analyzed.</p>
        </div>
      </div>

      {problem ? <p role="alert" className="source-monitor-error">{problem}</p> : null}
      {notice ? <p role="status" className="source-monitor-notice">{notice}</p> : null}

      <div className="source-monitor-layout">
        <div className="source-monitor-list">
          <div className="source-monitor-section-heading">
            <h3>Tracked documents</h3>
            <button type="button" disabled={loading} onClick={() => void refresh()}>Refresh</button>
          </div>
          {page ? (
            <p className="source-monitor-count">
              {page.total === 1 ? "1 monitored source" : `${page.total} monitored sources`}
              {page.total > PAGE_SIZE
                ? ` · ${page.offset + 1}–${Math.min(page.offset + page.items.length, page.total)} shown`
                : ""}
            </p>
          ) : null}
          {loading && !page ? <p role="status">Loading sources...</p> : null}
          {!loading && !page ? <p>Source inventory is unavailable.</p> : null}
          {page && page.items.length === 0 ? <p>No monitored sources on this page. Add a public document to begin.</p> : null}
          <ol className="source-monitor-items">
            {page?.items.map((source) => {
              const status = stage(source);
              return (
                <li key={source.id} className="source-monitor-item">
                  <div className="source-monitor-item-head">
                    <span className="source-monitor-state">{status.title}</span>
                    <span>{source.page_type.toUpperCase()}</span>
                  </div>
                  {source.entity_name ? <strong className="source-monitor-entity">{source.entity_name}</strong> : null}
                  {source.url.startsWith("https://") ? (
                    <a href={source.url} target="_blank" rel="noopener noreferrer">{source.url}</a>
                  ) : <span>{source.url}</span>}
                  <p>{status.detail}</p>
                  <dl>
                    <div><dt>Successful crawl</dt><dd>{timeLabel(source.last_crawled_at)}</dd></div>
                    <div><dt>Next scheduled</dt><dd>{source.is_active ? timeLabel(source.next_crawl_at) : "Paused"}</dd></div>
                    <div><dt>Snapshots</dt><dd>{source.snapshot_count ?? "Unknown"}</dd></div>
                    <div><dt>Last attempt</dt><dd>{timeLabel(source.last_crawl_attempt_at)}</dd></div>
                    <div><dt>Crawl result</dt><dd>{source.last_crawl_status ?? "Not recorded"}</dd></div>
                    {source.consecutive_crawl_failures > 0 ? (
                      <div><dt>Consecutive failures</dt><dd>{source.consecutive_crawl_failures}</dd></div>
                    ) : null}
                  </dl>
                  <div className="source-monitor-evidence-actions">
                    <button type="button" disabled={inspectingId !== null}
                      aria-expanded={inspection?.sourceId === source.id}
                      onClick={() => void inspectSource(source.id)}>
                      {inspectingId === source.id ? "Inspecting..." : inspection?.sourceId === source.id ? "Hide diagnostics" : "Inspect pipeline evidence"}
                    </button>
                  </div>
                  {inspectionError?.sourceId === source.id ? (
                    <p role="alert" className="source-monitor-error">{inspectionError.message}</p>
                  ) : null}
                  {inspection?.sourceId === source.id ? (
                    <div className="source-monitor-diagnostics" aria-label={`Pipeline evidence for ${source.url}`}>
                      <p>{diagnosis(inspection.evidence)}</p>
                      <dl>
                        <div><dt>Latest snapshot</dt><dd>{timeLabel(inspection.evidence.latest_snapshot?.crawled_at ?? null)}</dd></div>
                        <div><dt>Embedding</dt><dd>{inspection.evidence.latest_snapshot?.embedding_status ?? "No snapshot"}</dd></div>
                        <div><dt>Semantic analysis</dt><dd>{inspection.evidence.latest_comparison?.analysis_status ?? "No completed record"}</dd></div>
                        <div><dt>Vector analysis</dt><dd>{inspection.evidence.latest_comparison?.vector_analysis_status ?? "No event"}</dd></div>
                      </dl>
                      {inspection.evidence.latest_comparison?.event_id ? (
                        <button
                          type="button"
                          className="source-monitor-investigate"
                          onClick={() => {
                            const eventId = inspection.evidence.latest_comparison?.event_id;
                            if (eventId) onInvestigateEvent(eventId);
                          }}
                        >
                          Open recorded event
                        </button>
                      ) : null}
                      <p className="source-monitor-diagnostic-note">This view reads the latest two database observations. It does not verify snapshot files, represent the complete history, or assert downstream lineage.</p>
                    </div>
                  ) : null}
                  {canManage ? (
                    <div className="source-monitor-controls">
                      <label>
                        Interval
                        <select aria-label={`Interval for ${source.url}`} value={source.crawl_frequency}
                          disabled={busyId !== null}
                          onChange={(event) => void changeSource(source, "frequency", event.target.value as CrawlFrequency)}>
                          {FREQUENCIES.map((value) => <option key={value} value={value}>{value}</option>)}
                        </select>
                      </label>
                      <button type="button" disabled={busyId !== null}
                        onClick={() => void changeSource(source, source.is_active ? "pause" : "resume")}>
                        {busyId === source.id ? "Updating..." : source.is_active ? "Pause" : "Resume"}
                      </button>
                    </div>
                  ) : null}
                </li>
              );
            })}
          </ol>
          {page && page.total > PAGE_SIZE ? (
            <nav className="source-monitor-pagination" aria-label="Source pages">
              <button type="button" disabled={offset === 0 || loading} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Previous</button>
              <span>{offset + 1}–{Math.min(offset + PAGE_SIZE, page.total)} of {page.total}</span>
              <button type="button" disabled={offset + PAGE_SIZE >= page.total || loading} onClick={() => setOffset(offset + PAGE_SIZE)}>Next</button>
            </nav>
          ) : null}
        </div>

        <div className="source-monitor-enroll">
          <h3>Add public document</h3>
          <p>HTTPS only. Public HTML without login, query strings or custom ports. Private sources require a separate integration.</p>
          {canManage ? (
            <form onSubmit={(event) => void addSource(event)}>
              <label>Document URL<input type="url" placeholder="https://docs.example.org/policy" required maxLength={2048} value={url} onChange={(event) => setUrl(event.target.value)} /></label>
              <label>Domain label (optional, for new domains)<input maxLength={255} value={label} placeholder="Company policy" onChange={(event) => setLabel(event.target.value)} /></label>
              <label>Document type<select value={type} onChange={(event) => setType(event.target.value as SourceType)}>{SOURCE_TYPES.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
              <label>Monitor every<select value={frequency} onChange={(event) => setFrequency(event.target.value as CrawlFrequency)}>{FREQUENCIES.map((item) => <option key={item} value={item}>{item}</option>)}</select></label>
              <button className="source-monitor-primary" disabled={submitting} type="submit">{submitting ? "Enrolling..." : "Add source"}</button>
            </form>
          ) : <p>Your viewer role can inspect sources but cannot change monitoring.</p>}
          <p className="source-monitor-footnote">Enrollment schedules a crawl; it does not immediately fetch the page or create a semantic event. The latest finalized crawl result is shown without raw upstream error details; analysis and downstream evidence are separate.</p>
        </div>
      </div>
    </section>
  );
}
