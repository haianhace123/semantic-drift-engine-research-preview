# Risk Evidence Qualification at Downstream Read Boundaries

## Problem

Historical risk assessments can exist even when their linked semantic drift
record has `analysis_status = pending` and `vector_analysis_status = completed`.
The production Risk v2 worker now requires both lifecycle states to be
`completed`, but this does not retroactively fix legacy rows.

Before this change, Pattern Detection and What You Missed used Risk v2 rows
without enforcing that lifecycle contract. A legacy score could influence a
new risk escalation pattern or a user-facing missed-risk explanation despite
never having completed semantic analysis.

## Contract

Risk v2 evidence read from these two services must be linked to a
same-organization semantic drift event with both `analysis_status` and
`vector_analysis_status` equal to `completed`.

The qualification is applied to risk assessment evidence, not the underlying
raw event stream. Events with pending or failed semantic analysis remain
available for historical investigation; they are not silently rewritten,
deleted, or marked completed.

The existing assessment materialization-time boundaries, detection-time
windows, and tenant constraints remain unchanged.

## Scope and limits

- `pattern_evidence_service._load_risk_evidence` excludes unqualified Risk v2
  assessments before risk escalation detection.
- `what_you_missed_evidence_service._load_risks` excludes the same rows from
  derived risk evidence without hiding the original timeline events.
- This is read-only filtering. No migration or data cleanup is performed.
- Existing materialized risk escalation patterns and previously issued alerts
  are **not** requalified or revoked by these loaders.
- Other event/risk read models (including dashboard and temporal replay) may
  display historical data. A separate product-semantic review must distinguish
  historical visibility from actionable, verified risk before promoting them
  as trusted signals.
- The lifecycle fields describe current persisted readiness, not a complete
  historical proof of what was known at an earlier `as_of` cursor. Previously
  persisted risk materialization boundaries remain the temporal cutoff.

## Acceptance

PostgreSQL regression creates one qualified and four unqualified assessments
(`pending`/`failed` semantic analysis or vector analysis). It verifies that
only the qualified assessment is loaded while all raw events remain visible.
The full unit suite and both existing PostgreSQL evidence/service integration
suites should also pass.
