import type {
  ExecutionVerification,
  IntegrityDecisionView,
  InvalidationControlState,
  LineageRevision,
  TemporalIntegrityResolution,
} from "../integrity/types";

import type {
  TemporalIntegrityState,
} from "../integrity/useTemporalIntegrity";

import type {
  TemporalEvent,
} from "../temporal/types";


interface IntegrityRecordProps {
  selectedEvent: TemporalEvent | null;
  state: TemporalIntegrityState;
  onRetry: () => void;
}


function formatTimestamp(
  value: string,
) {
  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return value;
  }

  return new Intl.DateTimeFormat(
    undefined,
    {
      year: "numeric",
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    },
  ).format(date);
}


function revisionIdentity(
  revision: LineageRevision,
) {
  return [
    revision.source_external_id,
    revision.asset_external_id,
    revision.revision_external_id,
  ].join(":");
}


function RevisionNode({
  revision,
  role,
}: {
  revision: LineageRevision;
  role?: string;
}) {
  return (
    <div className="causal-revision">
      {role !== undefined && (
        <span className="causal-revision-role">
          {role}
        </span>
      )}

      <strong title={revision.asset_external_id}>
        {revision.asset_external_id}
      </strong>

      <dl>
        <div>
          <dt>revision</dt>
          <dd title={revision.revision_external_id}>
            {revision.revision_external_id}
          </dd>
        </div>

        <div>
          <dt>source</dt>
          <dd title={revision.source_external_id}>
            {revision.source_external_id}
          </dd>
        </div>
      </dl>
    </div>
  );
}


function VerificationEvidence({
  verification,
}: {
  verification: ExecutionVerification;
}) {
  if (verification.state === "awaiting_evidence") {
    return (
      <div
        className="integrity-verification"
        data-state="awaiting_evidence"
      >
        <span className="integrity-stage-label">
          Post-repair verification
        </span>

        <strong>Awaiting evidence</strong>

        <p>
          Execution succeeded, but no bound
          observation has yet established whether
          the stale direct dependency remains.
        </p>
      </div>
    );
  }

  if (
    verification.state
    === "stale_dependency_present"
  ) {
    return (
      <div
        className="integrity-verification"
        data-state="stale_dependency_present"
      >
        <span className="integrity-stage-label">
          Post-repair verification
        </span>

        <strong>
          Stale dependency still present
        </strong>

        <p>
          Bound evidence still contains the exact
          stale direct predecessor below.
        </p>

        <RevisionNode
          revision={verification.stale_dependency}
          role="stale predecessor"
        />
      </div>
    );
  }

  return (
    <div
      className="integrity-verification"
      data-state="verified"
    >
      <span className="integrity-stage-label">
        Post-repair verification
      </span>

      <strong>Direct dependency verified</strong>

      <p>
        The bound materialization no longer
        contains the exact stale direct
        predecessor. This does not assert that the
        wider knowledge system is globally safe or
        resolved.
      </p>

      <div className="integrity-verification-proof">
        <span>Observation</span>
        <code>
          {verification.observation_id
            ?? "Unavailable"}
        </code>
      </div>
    </div>
  );
}


const CONTROL_STATE_COPY: Record<
  InvalidationControlState,
  { label: string; detail: string }
> = {
  historical_no_action: {
    label: "Historical no action",
    detail: (
      "The persisted decision requires no downstream "
      + "control action."
    ),
  },
  blocked_current_state: {
    label: "Blocked by current state",
    detail: (
      "Current control-plane state prevents this "
      + "historical decision from executing."
    ),
  },
  eligible_awaiting_authority: {
    label: "Eligible, awaiting authority",
    detail: (
      "The decision is executable now, but no "
      + "authority-plane intent is persisted."
    ),
  },
  authorized_awaiting_execution: {
    label: "Authorized, awaiting execution",
    detail: (
      "Execution authority is persisted, but no "
      + "attempt has been recorded."
    ),
  },
  execution_attempt_recorded: {
    label: "Execution attempt recorded",
    detail: (
      "An execution claim exists without a terminal "
      + "result in this projection."
    ),
  },
  execution_failed: {
    label: "Execution failed",
    detail: (
      "The latest persisted execution result ended "
      + "without a successful side effect."
    ),
  },
  execution_succeeded_unverified: {
    label: "Succeeded, verification unavailable",
    detail: (
      "The side effect succeeded, but no verification "
      + "projection is available."
    ),
  },
  awaiting_evidence: {
    label: "Awaiting verification evidence",
    detail: (
      "The side effect succeeded and verification is "
      + "waiting for bound observation evidence."
    ),
  },
  stale_dependency_present: {
    label: "Stale dependency remains",
    detail: (
      "Bound post-repair evidence still contains the "
      + "stale direct dependency."
    ),
  },
  verified: {
    label: "Direct dependency verified",
    detail: (
      "Bound evidence no longer contains the exact "
      + "stale direct predecessor."
    ),
  },
};


function ControlState({
  state,
}: {
  state: InvalidationControlState;
}) {
  const copy = CONTROL_STATE_COPY[state];

  return (
    <div
      className="integrity-control-state"
      data-state={state}
    >
      <span>Control state</span>
      <strong>{copy.label}</strong>
      <code>{state}</code>
      <p>{copy.detail}</p>
    </div>
  );
}


function ExecutionRecord({
  view,
}: {
  view: IntegrityDecisionView;
}) {
  const {
    eligibility,
    execution,
  } = view;

  return (
    <section
      className="integrity-execution"
      aria-label="Authority, execution, and verification"
    >
      <ControlState state={view.control_state} />

      <div className="integrity-current-eligibility">
        <span className="integrity-stage-label">
          Current eligibility
        </span>

        <strong
          data-eligible={
            eligibility.eligible
              ? "true"
              : "false"
          }
        >
          {eligibility.eligible
            ? "Eligible now"
            : "Not eligible now"}
        </strong>

        <code>{eligibility.reason_code}</code>

        <p>
          Eligibility is evaluated against current
          control-plane state. It does not rewrite
          the historical decision above.
        </p>
      </div>

      <div className="integrity-execution-sequence">
        <div
          className="integrity-execution-stage"
          data-state={
            execution.intent === null
              ? "absent"
              : "present"
          }
        >
          <span className="integrity-stage-label">
            Authority
          </span>

          {execution.intent === null ? (
            <>
              <strong>
                No execution authorization
              </strong>
              <p>
                No persisted authority-plane intent
                is associated with this decision.
                This workspace does not grant
                execution authority.
              </p>
            </>
          ) : (
            <>
              <strong>
                Authorization recorded
              </strong>
              <time
                dateTime={
                  execution.intent.authorized_at
                }
              >
                {formatTimestamp(
                  execution.intent.authorized_at,
                )}
              </time>
              <p>
                Persisted authority evidence only.
                Browser access does not confer
                execution authority.
              </p>
            </>
          )}
        </div>

        <div
          className="integrity-execution-stage"
          data-state={
            execution.latest_attempt === null
              ? "absent"
              : "present"
          }
        >
          <span className="integrity-stage-label">
            Execution
          </span>

          {execution.latest_attempt === null ? (
            <>
              <strong>No claimed attempt</strong>
              <p>
                No execution attempt has been
                persisted for this decision.
              </p>
            </>
          ) : (
            <>
              <strong>
                Attempt{" "}
                {
                  execution.latest_attempt
                    .attempt_number
                }
              </strong>

              <time
                dateTime={
                  execution.latest_attempt
                    .claimed_at
                }
              >
                Claimed{" "}
                {formatTimestamp(
                  execution.latest_attempt
                    .claimed_at,
                )}
              </time>

              <p>
                Binding revision{" "}
                {
                  execution.latest_attempt
                    .binding_revision
                }
              </p>
            </>
          )}
        </div>

        <div
          className="integrity-execution-stage"
          data-state={
            execution.result?.outcome
              ?? "absent"
          }
        >
          <span className="integrity-stage-label">
            Terminal result
          </span>

          {execution.result === null ? (
            <>
              <strong>No terminal result</strong>
              <p>
                A completed downstream side effect
                has not been recorded.
              </p>
            </>
          ) : (
            <>
              <strong>
                {execution.result.outcome
                  === "succeeded"
                  ? "Side effect succeeded"
                  : "Execution failed"}
              </strong>

              <time
                dateTime={
                  execution.result.recorded_at
                }
              >
                {formatTimestamp(
                  execution.result.recorded_at,
                )}
              </time>

              <p>
                {execution.result.outcome
                  === "succeeded"
                  ? (
                    "The downstream invalidation "
                    + "side effect completed. This "
                    + "alone does not prove that "
                    + "downstream knowledge now "
                    + "reflects current truth."
                  )
                  : (
                    "The persisted execution "
                    + "attempt ended without a "
                    + "successful downstream "
                    + "side effect."
                  )}
              </p>
            </>
          )}
        </div>
      </div>

      {execution.result?.outcome === "succeeded"
      && (
        execution.verification === null ? (
          <div
            className="integrity-verification"
            data-state="unavailable"
          >
            <span className="integrity-stage-label">
              Post-repair verification
            </span>

            <strong>
              No verification projection
            </strong>

            <p>
              The downstream side effect succeeded,
              but this record contains no persisted
              post-repair verification projection.
              No verification state is inferred.
            </p>
          </div>
        ) : (
          <VerificationEvidence
            verification={
              execution.verification
            }
          />
        )
      )}
    </section>
  );
}


function IntegritySummary({
  resolution,
}: {
  resolution: TemporalIntegrityResolution;
}) {
  const historical =
    resolution.summary.historical_impact;
  const current =
    resolution.summary.current_control;
  const controlStates =
    resolution.summary.control_state_distribution;

  return (
    <section
      className="integrity-summary"
      aria-label="Integrity impact summary"
    >
      <div className="integrity-summary-group">
        <header>
          <span>Historical impact</span>
          <p>
            Persisted at decision time. Counts are
            not recomputed from current topology.
          </p>
        </header>

        <dl>
          <div>
            <dt>Changed roots</dt>
            <dd>{historical.root_count}</dd>
          </div>
          <div>
            <dt>Downstream targets</dt>
            <dd>{historical.target_count}</dd>
          </div>
          <div>
            <dt>Invalidate</dt>
            <dd>{historical.invalidate_count}</dd>
          </div>
          <div>
            <dt>Rematerialize</dt>
            <dd>{historical.rematerialize_count}</dd>
          </div>
          <div>
            <dt>No action</dt>
            <dd>{historical.no_action_count}</dd>
          </div>
        </dl>
      </div>

      <div className="integrity-summary-group">
        <header>
          <span>Current control state</span>
          <p>
            Live projection over the persisted
            decisions below.
          </p>
        </header>

        <dl>
          <div>
            <dt>Eligible</dt>
            <dd>{current.eligible_count}</dd>
          </div>
          <div>
            <dt>Authorized</dt>
            <dd>{current.authorized_count}</dd>
          </div>
          <div>
            <dt>Attempted</dt>
            <dd>{current.attempted_count}</dd>
          </div>
          <div>
            <dt>Succeeded</dt>
            <dd>{current.succeeded_count}</dd>
          </div>
          <div>
            <dt>Failed</dt>
            <dd>{current.failed_count}</dd>
          </div>
          <div>
            <dt>Awaiting evidence</dt>
            <dd>{current.awaiting_evidence_count}</dd>
          </div>
          <div>
            <dt>Stale remains</dt>
            <dd>
              {current.stale_dependency_present_count}
            </dd>
          </div>
          <div>
            <dt>Verified</dt>
            <dd>{current.verified_count}</dd>
          </div>
        </dl>
      </div>
      <div className="integrity-summary-group">
        <header>
          <span>Control-state distribution</span>
          <p>
            Mutually exclusive projection over the
            decisions below. Each decision appears
            in exactly one control-state bucket.
          </p>
        </header>

        <dl>
          <div>
            <dt>Historical no action</dt>
            <dd>{controlStates.historical_no_action_count}</dd>
          </div>
          <div>
            <dt>Blocked</dt>
            <dd>{controlStates.blocked_current_state_count}</dd>
          </div>
          <div>
            <dt>Awaiting authority</dt>
            <dd>{controlStates.eligible_awaiting_authority_count}</dd>
          </div>
          <div>
            <dt>Awaiting execution</dt>
            <dd>{controlStates.authorized_awaiting_execution_count}</dd>
          </div>
          <div>
            <dt>Attempt recorded</dt>
            <dd>{controlStates.execution_attempt_recorded_count}</dd>
          </div>
          <div>
            <dt>Execution failed</dt>
            <dd>{controlStates.execution_failed_count}</dd>
          </div>
          <div>
            <dt>Succeeded, unverified</dt>
            <dd>{controlStates.execution_succeeded_unverified_count}</dd>
          </div>
          <div>
            <dt>Awaiting evidence</dt>
            <dd>{controlStates.awaiting_evidence_count}</dd>
          </div>
          <div>
            <dt>Stale remains</dt>
            <dd>{controlStates.stale_dependency_present_count}</dd>
          </div>
          <div>
            <dt>Verified</dt>
            <dd>{controlStates.verified_count}</dd>
          </div>
        </dl>
      </div>
    </section>
  );
}


function DecisionRecord({
  view,
  index,
}: {
  view: IntegrityDecisionView;
  index: number;
}) {
  const {
    decision,
  } = view;

  return (
    <article className="integrity-decision">
      <header className="integrity-decision-header">
        <div>
          <span className="integrity-decision-number">
            Decision{" "}
            {String(index + 1).padStart(2, "0")}
          </span>

          <strong>
            {decision.disposition.replace(
              "_",
              " ",
            )}
          </strong>
        </div>

        <div className="integrity-decision-reason">
          <span>Reason</span>
          <code>{decision.reason_code}</code>
        </div>
      </header>

      <dl
        className="integrity-decision-identity"
        aria-label="Exact downstream decision identity"
      >
        <div>
          <dt>Decision ID</dt>
          <dd><code>{decision.id}</code></dd>
        </div>
        <div>
          <dt>Downstream target</dt>
          <dd title={decision.target.asset_external_id}>
            <strong>{decision.target.asset_external_id}</strong>
          </dd>
        </div>
        <div>
          <dt>Target revision</dt>
          <dd><code>{decision.target.revision_external_id}</code></dd>
        </div>
        <div>
          <dt>Target source</dt>
          <dd><code>{decision.target.source_external_id}</code></dd>
        </div>
      </dl>

      <section
        className="causal-path"
        aria-label="Persisted causal path"
      >
        <header>
          <div>
            <h3>Exact causal path</h3>
            <p>
              Persisted with the historical
              decision. This path is not recomputed
              from current topology.
            </p>
          </div>

          <span>
            {decision.causal_path.length}
            {" "}
            revision
            {decision.causal_path.length === 1
              ? ""
              : "s"}
          </span>
        </header>

        {decision.causal_path.length === 0 ? (
          <div className="causal-path-empty">
            <p>
              No persisted causal path is available for this decision.
              The persisted decision still identifies its changed root
              and downstream target, but no intermediate dependency path
              is established here.
            </p>
            <RevisionNode revision={decision.root} role="changed root" />
          </div>
        ) : (
          <ol className="causal-path-list">
            {decision.causal_path.map(
              (revision, pathIndex) => (
                <li
                  key={[
                    revisionIdentity(revision),
                    pathIndex,
                  ].join(":")}
                >
                  <RevisionNode
                    revision={revision}
                    role={
                      pathIndex === 0
                        ? "changed root"
                        : (
                          pathIndex
                          === (
                            decision.causal_path
                              .length - 1
                          )
                            ? "decision target"
                            : "dependency"
                        )
                    }
                  />
                </li>
              ),
            )}
          </ol>
        )}
      </section>

      <div className="integrity-policy-provenance">
        <span>Historical policy</span>

        <strong>
          {decision.policy.policy_external_id}
        </strong>

        <code>
          revision {decision.policy.revision}
        </code>
      </div>

      {decision.disposition === "no_action" ? (
        <div className="integrity-no-action">
          <span className="integrity-stage-label">
            Execution
          </span>

          <strong>
            No downstream action required
          </strong>

          <p>
            This persisted decision intentionally
            requires no invalidation or
            rematerialization. It is not an
            execution failure.
          </p>
        </div>
      ) : (
        <ExecutionRecord view={view} />
      )}
    </article>
  );
}


export function IntegrityRecord({
  selectedEvent,
  state,
  onRetry,
}: IntegrityRecordProps) {
  // Client-side attribution is a last-line display guard, not a substitute
  // for server tenant authorization. Never display a foreign event or a
  // different snapshot pair under the selected event's heading.
  const mismatchedEvidence = state.status === "ready"
    && selectedEvent !== null
    && (
      state.resolution.event_id !== selectedEvent.id
      || state.resolution.old_snapshot_id !== selectedEvent.old_snapshot_id
      || state.resolution.new_snapshot_id !== selectedEvent.new_snapshot_id
    );
  const resolution = state.status === "ready"
    && selectedEvent !== null
    && !mismatchedEvidence
      ? state.resolution
      : null;

  return (
    <section
      className="investigation-surface integrity-record"
      aria-labelledby="integrity-record-title"
    >
      <header className="integrity-record-heading">
        <div>
          <h2 id="integrity-record-title">
            Integrity record
          </h2>

          <p>
            Persisted causal decisions, current
            eligibility, execution evidence, and
            narrow post-repair verification.
          </p>
        </div>
      </header>

      {selectedEvent === null ? (
        <p className="integrity-record-state">
          Select a recorded change to inspect its
          integrity record.
        </p>
      ) : (
        <div className="integrity-event-context">
          <div>
            <span>Changed entity</span>
            <strong>
              {selectedEvent.entity_name}
            </strong>
          </div>

          <p>{selectedEvent.summary}</p>
        </div>
      )}

      {state.status === "idle" && (
        <p className="integrity-record-state">
          No integrity record is selected.
        </p>
      )}

      {state.status === "loading" && (
        <p
          className="integrity-record-state"
          role="status"
          aria-busy="true"
        >
          Loading persisted integrity evidence...
        </p>
      )}

      {state.status === "not_found" && (
        <p
          className="integrity-record-state"
          role="status"
        >
          Persisted integrity evidence is not
          available for this recorded change.
        </p>
      )}

      {state.status === "error" && (
        <div
          className="integrity-record-error"
          role="status"
        >
          <p>
            The integrity record could not be
            loaded.
          </p>

          <button
            type="button"
            onClick={onRetry}
          >
            Retry integrity record
          </button>
        </div>
      )}

      {mismatchedEvidence && (
        <div
          className="integrity-record-error"
          data-integrity-state="mismatched"
          role="alert"
        >
          <p>
            Integrity evidence does not match the selected event and
            snapshot pair. No downstream decision is displayed.
          </p>
          <button type="button" onClick={onRetry}>
            Retry integrity record
          </button>
        </div>
      )}

      {resolution !== null && resolution.decisions.length === 0 && (
        <div className="integrity-empty" data-integrity-state="unobserved">
          <strong>No recorded downstream decision</strong>
          <p>
            This change has no persisted downstream invalidation decision
            in the integrity projection. That does not establish that
            no consumers exist or that their knowledge is current.
            Unregistered dependencies remain unknown.
          </p>
        </div>
      )}

      {resolution !== null && resolution.decisions.length > 0 && (
        <IntegritySummary resolution={resolution} />
      )}

      {resolution !== null && resolution.decisions.length > 0 && (
        <div className="integrity-decision-list">
          {resolution.decisions.map(
            (view, index) => (
              <DecisionRecord
                key={view.decision.id}
                view={view}
                index={index}
              />
            ),
          )}
        </div>
      )}
    </section>
  );
}