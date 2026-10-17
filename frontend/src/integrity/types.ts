
export type UUID = string;
export type ISODateTime = string;

export interface LineageRevision {
  source_external_id: string;
  asset_external_id: string;
  revision_external_id: string;
}

export interface InvalidationPolicy {
  policy_external_id: string;
  revision: number;
}

export interface InvalidationDecision {
  id: UUID;
  root: LineageRevision;
  target: LineageRevision;
  policy: InvalidationPolicy;
  disposition:
    | "no_action"
    | "invalidate"
    | "rematerialize";
  reason_code: string;
  causal_path: LineageRevision[];
}

export type EligibilityReason =
  | "eligible"
  | "no_action"
  | "policy_not_active"
  | "policy_revision_superseded"
  | "policy_revision_not_active"
  | "target_authority_missing"
  | "target_not_current";

export interface InvalidationEligibility {
  eligible: boolean;
  reason_code: EligibilityReason;
}

export interface ExecutionIntent {
  id: UUID;
  authorized_at: ISODateTime;
}

export interface ExecutionAttempt {
  id: UUID;
  attempt_number: number;
  binding_revision: number;
  claimed_at: ISODateTime;
  lease_expires_at: ISODateTime;
}

export interface ExecutionResult {
  id: UUID;
  attempt_id: UUID;
  outcome: "succeeded" | "failed";
  recorded_at: ISODateTime;
}

export type VerificationState =
  | "awaiting_evidence"
  | "stale_dependency_present"
  | "verified";

export interface ExecutionVerification {
  state: VerificationState;
  execution_result_id: UUID;
  verification_record_id: UUID | null;
  observation_id: UUID | null;
  stale_dependency: LineageRevision;
}

export interface InvalidationExecution {
  intent: ExecutionIntent | null;
  latest_attempt: ExecutionAttempt | null;
  result: ExecutionResult | null;
  verification: ExecutionVerification | null;
}

export type InvalidationControlState =
  | "historical_no_action"
  | "blocked_current_state"
  | "eligible_awaiting_authority"
  | "authorized_awaiting_execution"
  | "execution_attempt_recorded"
  | "execution_failed"
  | "execution_succeeded_unverified"
  | "awaiting_evidence"
  | "stale_dependency_present"
  | "verified";

export interface IntegrityDecisionView {
  decision: InvalidationDecision;
  control_state: InvalidationControlState;
  eligibility: InvalidationEligibility;
  execution: InvalidationExecution;
}

export interface HistoricalImpactSummary {
  root_count: number;
  target_count: number;
  no_action_count: number;
  invalidate_count: number;
  rematerialize_count: number;
}

export interface CurrentControlSummary {
  eligible_count: number;
  authorized_count: number;
  attempted_count: number;
  succeeded_count: number;
  failed_count: number;
  awaiting_evidence_count: number;
  stale_dependency_present_count: number;
  verified_count: number;
}

export interface ControlStateDistribution {
  historical_no_action_count: number;
  blocked_current_state_count: number;
  eligible_awaiting_authority_count: number;
  authorized_awaiting_execution_count: number;
  execution_attempt_recorded_count: number;
  execution_failed_count: number;
  execution_succeeded_unverified_count: number;
  awaiting_evidence_count: number;
  stale_dependency_present_count: number;
  verified_count: number;
}

export interface IntegritySummary {
  historical_impact: HistoricalImpactSummary;
  current_control: CurrentControlSummary;
  control_state_distribution: ControlStateDistribution;
}

export interface TemporalIntegrityResolution {
  event_id: UUID;
  old_snapshot_id: UUID;
  new_snapshot_id: UUID;
  summary: IntegritySummary;
  decisions: IntegrityDecisionView[];
}
