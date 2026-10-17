import type {
  TemporalEventEvidence,
  TemporalSemanticChange,
  TemporalSnapshotEvidence,
} from "../temporal/types";

import type {
  TemporalEvidenceState,
} from "../temporal/useTemporalEvidence";


interface SemanticEvidenceProps {
  state: TemporalEvidenceState;
  onRetry: () => void;
}


function formatObservedAt(
  snapshot: TemporalSnapshotEvidence,
) {
  const observedAt =
    snapshot.crawled_at
    ?? snapshot.created_at;

  if (observedAt === null) {
    return {
      label: "Observation time unavailable",
      machine: undefined,
    };
  }

  const date = new Date(observedAt);

  if (Number.isNaN(date.getTime())) {
    return {
      label: observedAt,
      machine: observedAt,
    };
  }

  return {
    label: new Intl.DateTimeFormat(
      undefined,
      {
        year: "numeric",
        month: "short",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      },
    ).format(date),
    machine: observedAt,
  };
}


function similarityPercent(
  similarity: number | undefined,
) {
  if (
    typeof similarity !== "number"
    || !Number.isFinite(similarity)
  ) {
    return "Unavailable";
  }

  return `${(
    Math.max(
      0,
      Math.min(1, similarity),
    ) * 100
  ).toFixed(1)}%`;
}


function isQualifiedSemanticChange(
  value: unknown,
): value is TemporalSemanticChange {
  if (value === null || typeof value !== "object") {
    return false;
  }

  const change = value as Record<string, unknown>;
  return (
    ["pricing", "legal", "policy", "feature", "other"].includes(
      String(change.category),
    )
    && ["high", "medium", "low"].includes(String(change.risk_level))
    && typeof change.old_meaning === "string"
    && typeof change.new_meaning === "string"
    && typeof change.impact === "string"
  );
}


function SemanticChange({
  change,
  index,
}: {
  change: TemporalSemanticChange;
  index: number;
}) {
  return (
    <article className="semantic-change">
      <header className="semantic-change-header">
        <span className="semantic-change-index">
          {String(index + 1).padStart(2, "0")}
        </span>

        <span className="semantic-change-category">
          {change.category}
        </span>

        <span
          className="semantic-change-risk"
          data-risk={change.risk_level}
        >
          Model {change.risk_level} risk
        </span>
      </header>

      <div className="semantic-change-transition">
        <div>
          <span>Before meaning</span>
          <p>{change.old_meaning}</p>
        </div>

        <span
          className="semantic-transition-line"
          aria-hidden="true"
        />

        <div>
          <span>After meaning</span>
          <p>{change.new_meaning}</p>
        </div>
      </div>

      <div className="semantic-change-impact">
        <span>Operational impact</span>
        <p>{change.impact}</p>
      </div>
    </article>
  );
}


function SnapshotEvidence({
  side,
  snapshot,
}: {
  side: "before" | "after";
  snapshot: TemporalSnapshotEvidence;
}) {
  const observedAt =
    formatObservedAt(snapshot);

  const title =
    side === "before"
      ? "Before"
      : "After";

  return (
    <section
      className="snapshot-evidence"
      data-side={side}
      aria-labelledby={`${side}-evidence-title`}
    >
      <header className="snapshot-evidence-header">
        <div>
          <span className="snapshot-evidence-side">
            {title}
          </span>

          <time dateTime={observedAt.machine}>
            {observedAt.label}
          </time>
        </div>

        <dl className="snapshot-evidence-provenance">
          <div>
            <dt>Snapshot</dt>
            <dd title={snapshot.snapshot_id}>
              {snapshot.snapshot_id}
            </dd>
          </div>
          <div>
            <dt>Hash</dt>
            <dd title={snapshot.content_hash}>
              {snapshot.content_hash}
            </dd>
          </div>
        </dl>
      </header>

      <pre className="snapshot-evidence-content">
        {snapshot.content}
      </pre>
    </section>
  );
}


function ReadySemanticEvidence({
  evidence,
}: {
  evidence: TemporalEventEvidence;
}) {
  // The evidence endpoint returns the persisted delta verbatim. Pending
  // recovery events can have an incomplete delta with no similarity or
  // semantic-analysis result. No result is not evidence of no meaning shift.
  const delta = evidence.delta ?? {};
  const analysis = delta.semantic_analysis;
  const qualifiedChanges =
    Array.isArray(analysis?.changes)
    && analysis.changes.every(isQualifiedSemanticChange);
  const changes = qualifiedChanges && analysis
    ? analysis.changes ?? []
    : [];

  const interpretationComplete =
    evidence.analysis_status === "completed"
    && analysis?.analysis_status === "success"
    && qualifiedChanges;
  const interpretationNotRequired =
    evidence.analysis_status === "completed"
    && analysis?.analysis_status === "not_required";
  const interpretationFailed =
    evidence.analysis_status === "failed"
    || analysis?.analysis_status === "failed";
  const interpretationPending =
    evidence.analysis_status === "pending"
    && !interpretationFailed;

  return (
    <>
      <div className="semantic-evidence-overview">
        <div className="semantic-evidence-summary">
          <span>
            {interpretationComplete
              ? "Recorded interpretation"
              : "Recorded event summary"}
          </span>
          <p>{evidence.summary || "No summary recorded."}</p>
        </div>

        <dl className="semantic-evidence-measures">
          <div>
            <dt>Text similarity</dt>
            <dd>
              {similarityPercent(
                delta.similarity,
              )}
            </dd>
          </div>

          <div>
            <dt>Analyzed chunks</dt>
            <dd>
              {typeof delta.chunks_analyzed === "number"
              && Number.isInteger(delta.chunks_analyzed)
              && delta.chunks_analyzed >= 0
                ? delta.chunks_analyzed
                : "Unavailable"}
            </dd>
          </div>

          <div>
            <dt>Analysis</dt>
            <dd>
              {evidence.analysis_status.replace(
                "_",
                " ",
              )}
            </dd>
          </div>
        </dl>
      </div>

      {interpretationComplete
      && changes.length > 0 && (
        <section
          className="semantic-change-register"
          aria-labelledby="semantic-change-register-title"
        >
          <header>
            <h3 id="semantic-change-register-title">
              Meaning changed
            </h3>

            <p>
              {changes.length}
              {" "}
              interpreted semantic change
              {changes.length === 1 ? "" : "s"}
              {" "}
              persisted with this event.
            </p>
          </header>

          <div className="semantic-change-list">
            {changes.map((change, index) => (
              <SemanticChange
                key={[
                  change.category,
                  change.old_meaning,
                  change.new_meaning,
                  index,
                ].join(":")}
                change={change}
                index={index}
              />
            ))}
          </div>
        </section>
      )}

      {interpretationComplete
      && changes.length === 0 && (
        <div className="semantic-no-change">
          <span>No model-reported meaning changes</span>
          <p>
            {analysis?.summary
              || evidence.summary
              || "The completed model interpretation reported no changes."}
          </p>
        </div>
      )}

      {interpretationPending && (
        <div className="semantic-analysis-unavailable" role="status">
          <span>Semantic analysis pending</span>
          <p>
            There is no completed interpretation yet.
            The recorded source snapshots remain available below.
          </p>
        </div>
      )}

      {interpretationFailed && (
        <div className="semantic-analysis-unavailable" role="alert">
          <span>Semantic interpretation failed</span>
          <p>
            No semantic conclusion can be drawn from this run.
            The recorded source snapshots remain available below.
          </p>
        </div>
      )}

      {interpretationNotRequired && (
        <div className="semantic-analysis-unavailable" role="status">
          <span>Semantic interpretation not required</span>
          <p>
            No model interpretation was required for this event.
            This is not a conclusion that its meaning was unchanged.
            The exact recorded source snapshots remain available below.
          </p>
        </div>
      )}

      {!interpretationComplete
      && !interpretationPending
      && !interpretationFailed
      && !interpretationNotRequired && (
        <div className="semantic-analysis-unavailable" role="status">
          <span>Semantic interpretation unavailable</span>
          <p>
            The persisted analysis statuses or result fields do not establish
            a completed interpretation. Inspect the source snapshots below.
          </p>
        </div>
      )}

      <div className="snapshot-evidence-pair">
        <SnapshotEvidence
          side="before"
          snapshot={evidence.before}
        />

        <SnapshotEvidence
          side="after"
          snapshot={evidence.after}
        />
      </div>
    </>
  );
}


export function SemanticEvidence({
  state,
  onRetry,
}: SemanticEvidenceProps) {
  return (
    <section
      className="investigation-surface semantic-evidence"
      aria-labelledby="semantic-evidence-title"
    >
      <header className="semantic-evidence-heading">
        <div>
          <h2 id="semantic-evidence-title">
            Semantic evidence
          </h2>
          <p>
            Persisted meaning change and its exact
            source snapshots.
          </p>
        </div>
      </header>

      {state.status === "idle" && (
        <p className="semantic-evidence-state">
          Select a recorded change to inspect its
          persisted semantic evidence.
        </p>
      )}

      {state.status === "loading" && (
        <p
          className="semantic-evidence-state"
          role="status"
          aria-busy="true"
        >
          Loading persisted evidence...
        </p>
      )}

      {state.status === "not_found" && (
        <p
          className="semantic-evidence-state"
          role="status"
        >
          Persisted semantic evidence is not
          available for this recorded change.
        </p>
      )}

      {state.status === "error" && (
        <div
          className="semantic-evidence-error"
          role="alert"
        >
          <p>
            The persisted semantic evidence could
            not be loaded.
          </p>

          <button
            type="button"
            onClick={onRetry}
          >
            Retry evidence
          </button>
        </div>
      )}

      {state.status === "ready" && (
        <ReadySemanticEvidence
          evidence={state.evidence}
        />
      )}
    </section>
  );
}