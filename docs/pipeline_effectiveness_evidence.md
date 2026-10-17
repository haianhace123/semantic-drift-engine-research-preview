# Pipeline effectiveness evidence

## Purpose

The Operations capability inventory describes configuration and recent worker
heartbeats. It cannot establish that any particular snapshot was embedded,
analyzed, scored, or linked to a downstream invalidation decision.

`pipeline_effectiveness_evidence` reads the records for **one** semantic drift
event within **one** organization. It does not schedule work, contact Gemini or
OpenAI, invoke an executor, or modify rows. The command opens a PostgreSQL
`READ ONLY` transaction and rolls it back on exit.

The report deliberately separates two different kinds of evidence:

- `vector_risk_complete`: both snapshots and their embeddings are present,
  the persisted drift event has completed analysis, vector metrics are present,
  and a matching Risk v2 assessment supports the persisted score.
- `causal_decisions_observed`: one or more provenance rows link the event to a
  causal decision. This is **not** proof of authorization or physical deletion.

Topology shifts and causal decisions can be absent for legitimate reasons.
Their absence is reported as `not_observed`, not silently converted to success
or attributed to a system failure.

## Local acceptance test

**Only use a development or integration database.** The underlying integration
tests write disposable rows and remove them; they do not use an embedding
provider and do not execute physical deletion. They require an existing
monitored page and snapshot for test-only foreign-key ownership. A skipped
test is not a successful acceptance test.

First bring up the integration database:

```powershell
docker compose --profile integration up -d --wait integration_downstream_db
```

Stop running background workers while the database tests are in progress,
then run these two tests:

```powershell
docker compose run --rm -e RUN_DB_INTEGRATION_TESTS=1 `
     -v "${PWD}:/app" api python -m pytest -q -s `
     tests/integration/test_vector_worker.py::test_worker_cycle_closes_embedding_vector_and_risk_v2_pipeline `
     tests/integration/test_vector_worker.py::test_temporal_invalidation_worker_persists_real_decision_and_provenance `
     --tb=short
```

Restore only the workers you stopped. Both tests must **pass**, without being
skipped. They emit `P2D_VECTOR_RISK_EVIDENCE=` and `P2D_CAUSAL_EVIDENCE=`
followed by JSON with stage statuses, evidence identifiers, persisted scores,
and available timestamps.

The first test drives the production worker and PostgreSQL persistence using
deterministic test embeddings. It creates a drift event fixture; it does **not**
test crawler retrieval, the production semantic detector, or Gemini network
availability. The second test drives temporal invalidation analysis against
lineage observations and proves a decision was persisted with no execution
intent. These are **separate cases**, not one uninterrupted production
transaction. Do not present them as proof of the entire customer workflow.

## Audit an existing event

If an event already exists in the development/integration database, an operator
can inspect it without changing its state:

```powershell
docker compose run --rm -v "${PWD}:/app" api `
     python -m scripts.audit_pipeline_effectiveness `
     --organization-id "<organization-uuid>" `
     --event-id "<event-uuid>" `
     --require-vector-risk
```

The command exits `0` when a matching event is found and, if the optional
`--require-vector-risk` flag is present, the vector/risk evidence is complete.
It exits `2` for incomplete required evidence and `1` when the event does not
belong to the specified organization. SQL failures also result in a nonzero
exit code. Output excludes document content, paths, API keys, and stored
provider error strings. Use the same event and organization together; do not
substitute a different tenant for troubleshooting.

Integration test rows are removed after the test, so identifiers emitted by
the tests cannot be audited again after cleanup. Run this command against a
separately existing event, not the deleted test fixture.

## Reading the report

- `complete`: recorded state is completed and its required evidence matches.
- `pending` / `processing` / `failed`: persisted stage lifecycle state.
- `inconsistent`: a completion flag lacks the data or tenant-scoped records
  needed to substantiate it.
- `not_observed`: no linked topology or causal decision record was found.
- `latency_ms`: elapsed time between recorded timestamps, only for nonnegative
  intervals. `null` means the measurement cannot be calculated reliably. It
  is not a pipeline SLA measurement.

No score is synthesized from configuration or heartbeat. The report does not
claim to establish semantic classification accuracy, Gemini provider health,
end-to-end latency under load, downstream authorization, or execution safety.
Those require separate acceptance tests.

## Next acceptance boundary

P2D.1 establishes the persisted-evidence contract without destructive side
effects. A controlled detector-to-evidence test on a tenant-owned, disposable
monitored source is the next distinct batch. It must not reuse a production
monitored page or trigger a live downstream executor.
