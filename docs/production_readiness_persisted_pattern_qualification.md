# Persisted Pattern evidence qualification (PR.2)

## Risk

PR.1 prevents unqualified Risk v2 assessments from participating in new escalation detection. A `risk_escalation` Pattern persisted before that readiness contract remains available in historical storage. Without a recheck, Alert Generation and Watchlist policy reconciliation can convert that older Pattern into a new actionable Alert. `What You Missed` can likewise report it as current risk evidence.

## Contract

`qualified_pattern_ids` is the shared read-side qualification boundary for these consumers. It does not mutate or delete historical records.

A persisted `risk_escalation` Pattern is eligible only when:

- the Pattern belongs to the caller's organization;
- its `event_ids` are nonempty, unique, well-formed UUIDs;
- every participating event belongs to that organization and its monitored page is tenant-scoped;
- each participating event currently has `analysis_status = completed` and `vector_analysis_status = completed`;
- each event has a deterministic Risk v2 assessment created no later than the Pattern's materialization time and assessed no later than the Pattern's detection time.

The query is bounded by event-ID batches instead of issuing a query per Pattern. Other Pattern types are unaffected by the Risk v2-specific check. Malformed or missing escalation evidence fails closed for current decisions.

The Alert materialization checkpoint still advances over inspected Pattern materializations, including those that fail qualification. Policy reconciliation retains its existing keyset cursor and evaluates historical candidates under the same contract. An unqualified Pattern is not transformed into a new Alert or delivery. Historical Pattern rows and already-persisted Alerts remain unchanged for audit.

## Remaining limitations

Qualification does not independently recalculate a historical escalation's numeric score sequence. It establishes the lifecycle and temporal eligibility of the referenced Risk v2 records; it does not attest the accuracy of a historical detection algorithm. Existing Alerts and deliveries are not revoked automatically.

The `/api/temporal/patterns` listing currently reports persisted administrative `is_active` status independently of this decision-time check. Treat a historical `risk_escalation` row there as recorded detection history, not automatically as qualified current evidence. Aligning the API's active/actionable semantics is a separate read-model contract decision; avoid silently changing `total`, filtering, or pagination without such a contract.

## Verification

- Unit: malformed and duplicate event IDs, non-risk Pattern preservation, materialization/effective-time fences.
- PostgreSQL integration: qualified versus pending semantic member, tenant isolation, Pattern alert generation, policy reconciliation without alert duplication, What You Missed filtering, retained audit rows.

No schema migration or historical backfill is required.
