import type { TemporalEvent } from "../temporal/types";
import type { TemporalIntegrityState } from "../integrity/useTemporalIntegrity";

export type OperatorFocusVerdict = {
  kind: "unknown" | "attention" | "limited";
  title: string;
  explanation: string;
  decisionCount: number | null;
  verifiedCount: number | null;
  outstandingCount: number | null;
  target: string | null;
};

const UNKNOWN = (title: string, explanation: string): OperatorFocusVerdict => ({
  kind: "unknown",
  title,
  explanation,
  decisionCount: null,
  verifiedCount: null,
  outstandingCount: null,
  target: null,
});

export function operatorFocusVerdict(
  event: TemporalEvent | null,
  state: TemporalIntegrityState,
): OperatorFocusVerdict {
  if (event === null) {
    return UNKNOWN("No event selected", "Choose a recorded event to examine downstream control evidence.");
  }
  if (state.status === "idle" || state.status === "loading" || state.eventId !== event.id) {
    return UNKNOWN("Control evidence loading", "No downstream control conclusion can be drawn until the selected event's record loads.");
  }
  if (state.status === "not_found") {
    return UNKNOWN("No integrity record available", "Absence of a record is not proof that downstream consumers are current.");
  }
  if (state.status === "error") {
    return UNKNOWN("Control evidence unavailable", "The integrity record could not be loaded. The downstream state is unknown.");
  }
  const resolution = state.resolution;
  if (
    resolution.event_id !== event.id
    || resolution.old_snapshot_id !== event.old_snapshot_id
    || resolution.new_snapshot_id !== event.new_snapshot_id
  ) {
    return UNKNOWN("Integrity identity mismatch", "The returned evidence does not match the event and snapshot pair. No control conclusion is shown.");
  }

  const decisions = resolution.decisions;
  if (decisions.length === 0) {
    return {
      ...UNKNOWN("No registered downstream decision", "No downstream decision was recorded for this event. Unregistered consumers remain unknown."),
      decisionCount: 0,
    };
  }
  const verified = decisions.filter((entry) => entry.control_state === "verified").length;
  const historicalNoAction = decisions.filter((entry) => entry.control_state === "historical_no_action").length;
  const outstanding = decisions.length - verified - historicalNoAction;
  const stale = decisions.some((entry) => entry.control_state === "stale_dependency_present");
  const failed = decisions.some((entry) => entry.control_state === "execution_failed");
  const awaiting = decisions.some((entry) => entry.control_state === "awaiting_evidence" || entry.control_state === "execution_succeeded_unverified");
  const target = decisions.find((entry) => entry.control_state !== "historical_no_action")?.decision.target.asset_external_id ?? null;

  const result = (kind: OperatorFocusVerdict["kind"], title: string, explanation: string): OperatorFocusVerdict => ({
    kind, title, explanation,
    decisionCount: decisions.length,
    verifiedCount: verified,
    outstandingCount: outstanding,
    target,
  });

  if (stale) return result("attention", "Stale direct dependency observed", "Bound post-repair evidence still shows a stale predecessor for at least one recorded target.");
  if (failed) return result("attention", "Downstream execution failed", "At least one recorded control action failed. Review the exact decision and execution result.");
  if (awaiting) return result("attention", "Post-repair verification outstanding", "A downstream action may have succeeded, but direct-dependency verification remains unavailable or pending.");
  if (outstanding > 0) return result("attention", "Downstream control still in progress", "One or more registered decisions have not reached narrow direct-dependency verification.");
  if (verified > 0) return result("limited", "Direct predecessor checks recorded", "Recorded checks concern exact direct predecessors only. They do not establish global knowledge safety.");
  return result("limited", "Historical decisions require no action", "The registered decisions were classified as no action. Unregistered consumers are not assessed.");
}
