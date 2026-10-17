# Temporal Pattern read contract (PR.3)

## Context

`GET /api/temporal/patterns` returns persisted Pattern records for the authenticated organization. Its existing `is_active` and `is_archived` fields describe database lifecycle, not the validity of Risk v2 evidence used to detect a historical escalation. In particular, older `risk_escalation` patterns can remain active although source events never completed semantic analysis.

The current frontend workspace does not consume this listing directly. The contract matters to integrations and future consumers of the temporal read API; it must not imply that an active historical record is an actionable warning.

## Response contract

Each item now includes an additive field, `risk_evidence_qualified`:

- `true`: for a `risk_escalation` record whose source Risk v2 evidence meets the shared persisted-pattern qualification rules at read time;
- `false`: for a `risk_escalation` record whose source evidence fails those rules, including missing or pending analysis, missing or late assessments, foreign-tenant sources and malformed member IDs;
- `null`: for all other Pattern types, for which the Risk v2 escalation qualification test is not applicable.

`risk_evidence_qualified = true` **does not mean** an Alert may be emitted. Policy, watchlists, lifecycle state and other alert gates remain independent. It does not recompute the historic escalation score or certify a historical detector's correctness. The qualification status may change with evidence/lifecycle state and is not an immutable snapshot at the Pattern's `detected_at`.

The endpoint still applies `active_only` to stored lifecycle flags, returns unqualified history without silently removing it, and preserves existing `total`, `offset`, `limit`, ordering and optional `pattern_type` filtering. Evidence qualification is performed only for rows in the selected page, using the same tenant-scoped batched authority as Alert Generation and What You Missed. No Pattern or Assessment rows are changed.

Consumers should display or filter on `risk_evidence_qualified` explicitly when interpreting a risk-escalation record; do not interpret `is_active` alone as evidence of current risk. Do not display `null` as `false` for non-escalation patterns.

## Verification

The PostgreSQL API integration test verifies valid and pending evidence, the three-valued field, count/pagination stability, archived historical records, type filtering and tenant isolation. No database migration or backfill is required.
