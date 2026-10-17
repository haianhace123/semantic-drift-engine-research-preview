# Legacy Risk Assessment Alert Qualification

## Problem

Some assessments can predate semantic-analysis lifecycle enforcement. In a
read-only audit of a historic cohort, events were `analysis_status=pending`,
while vector analysis and risk scoring were `completed`. This is not enough
to certify that those assessments were produced from complete semantic
evidence. Preserving historical records is required for auditability, but
using them to issue *new* risk alerts would promote unqualified evidence.

## Policy

`build_risk_alert_candidate()` is the central policy authority for creating
Risk v2 alert candidates from persisted assessments. For event-backed
assessments, the source event must have both `analysis_status=completed` and
`vector_analysis_status=completed`. Otherwise no alert candidate is generated.
Tenant, event and monitored-page ownership checks run before this readiness
decision. Eventless assessments retain existing policy for backward
compatibility because there is no event from which to derive readiness.

This change does not alter scoring, assessments, existing alerts, historical
replay or event lifecycle states. It cannot retract an alert already
persisted or delivered. It also does not requalify legacy assessments for
analytics, patterns, or risk-model performance claims; those require their
own evidence qualification.

## Validation

- Existing ready-event and eventless-alert cases continue to produce their
  expected candidates.
- Pending, failed or missing semantic/vector status produces no new risk alert
  even if severity is high and a Watchlist matches.
- The unchanged source discovery/checkpoint flow still advances past
  historical records whose candidates are suppressed.

Do not backfill `analysis_status=completed` solely to make legacy assessments
eligible. An auditable re-evaluation from verified source evidence is a
separate operation.
