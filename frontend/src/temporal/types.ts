import type {
  LineageRevision,
} from "../integrity/types";

export interface TemporalEventLineage {
  status: "available" | "unavailable";
  roots: LineageRevision[];
}

export interface TemporalEvent {
  id: string;
  organization_id: string;
  entity_id: string;
  entity_name: string;
  domain: string;
  category: string | null;
  url: string;
  page_type: string;
  monitored_page_id: string;
  old_snapshot_id: string;
  new_snapshot_id: string;
  lineage: TemporalEventLineage;
  summary: string;
  risk_level: string;
  detected_at: string | null;
}

export interface TemporalEventStream {
  total: number;
  offset: number;
  limit: number;
  data: TemporalEvent[];
}

export interface TemporalSnapshotEvidence {
  snapshot_id: string;
  monitored_page_id: string;
  content_hash: string;
  content_size: number | null;
  crawled_at: string | null;
  created_at: string | null;
  content: string;
}

export type SemanticChangeCategory =
  | "pricing"
  | "legal"
  | "policy"
  | "feature"
  | "other";

export type SemanticRiskLevel =
  | "high"
  | "medium"
  | "low";

export interface TemporalSemanticChange {
  category: SemanticChangeCategory;
  old_meaning: string;
  new_meaning: string;
  impact: string;
  risk_level: SemanticRiskLevel;
}

export interface TemporalSemanticAnalysis {
  // The persisted semantic payload can be incomplete while an event is
  // registered for recovery, or contain older/unknown provider states.
  analysis_status?: string;
  changes?: TemporalSemanticChange[];
  summary?: string;
  overall_risk?: SemanticRiskLevel;
}

export interface TemporalSemanticDelta {
  change_detected?: boolean;
  old_hash?: string;
  new_hash?: string;
  similarity?: number;
  chunks_analyzed?: number;
  semantic_analysis?: TemporalSemanticAnalysis;
  detected_at?: string;
}

export interface TemporalEventEvidence {
  event_id: string;
  monitored_page_id: string;
  analysis_status: string;
  summary: string;
  delta: TemporalSemanticDelta;
  before: TemporalSnapshotEvidence;
  after: TemporalSnapshotEvidence;
}

export interface TemporalReplaySnapshot {
  snapshot_id: string;
  monitored_page_id: string;
  entity_id: string;
  content_hash: string;
  crawled_at: string;
  created_at: string;
}

export interface TemporalReplayEvent {
  event_id: string;
  monitored_page_id: string;
  entity_id: string;
  old_snapshot_id: string;
  new_snapshot_id: string;
  detected_at: string;
  risk_level: string;
  semantic_magnitude: number | null;
}

export interface TemporalReplayGraphEntity {
  entity_id: string;
  in_degree: number;
  out_degree: number;
  degree: number;
  degree_centrality: number;
  downstream_reach: number;
  downstream_reach_ratio: number;
}

export interface TemporalReplayGraph {
  observed_at: string;
  created_at: string;
  semantic_drift_event_id: string | null;
  entity_count: number;
  active_edge_count: number;
  entities: TemporalReplayGraphEntity[];
}

export interface TemporalReplayTopologyEntity {
  entity_id: string;
  classification: string;
  severity: string;
  shift_score: number;
  degree_delta: number;
  degree_centrality_delta: number;
  downstream_reach_delta: number;
  downstream_reach_ratio_delta: number;
}

export interface TemporalReplayTopology {
  observed_at: string;
  created_at: string;
  semantic_drift_event_id: string | null;
  previous_observed_at: string | null;
  entities: TemporalReplayTopologyEntity[];
}

export interface TemporalReplayRiskAssessment {
  assessment_id: string;
  semantic_drift_event_id: string | null;
  risk_version: string;
  assessed_at: string;
  created_at: string;
  score: number;
  topology_score: number | null;
  topology_contribution: number | null;
  has_topology_signal: boolean;
  has_previous_topology_state: boolean;
}

export interface TemporalReplay {
  at: string;
  organization_id: string;
  snapshot_count: number;
  event_count: number;
  risk_assessment_count: number;
  snapshots: TemporalReplaySnapshot[];
  events: TemporalReplayEvent[];
  graph: TemporalReplayGraph | null;
  topology: TemporalReplayTopology | null;
  risk_assessments: TemporalReplayRiskAssessment[];
}