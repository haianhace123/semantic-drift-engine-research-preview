# Semantic analysis recovery

## Production contract

`semantic_drift_events` is the lifecycle authority. A failed event is
re-evaluated against its *original* snapshot pair; recovery never chooses the
current/latest pair or inserts a second event. Snapshot owner/page/organization
invariants and physical SHA-256 content hashes must match before provider IO.

The service uses a PostgreSQL session advisory lock derived from the event UUID.
The lock spans the external call, while the attempt increment is committed
before the call. A worker crash releases the lock automatically and leaves the
event failed, consuming an attempt; it cannot publish a completed event.
Different workers cannot call the provider for the same event concurrently.
`analysis_status` remains `failed` until a validated result commits, so risk
and lineage gates remain closed during a retry.

Each event has at most three attempts by default, including the original
analysis. Retrying waits 60 seconds after the first failure and uses exponential
backoff. Permanent configuration/input errors are recorded as non-retryable in
`semantic_delta_json.analysis_failure`. For legacy failures without a typed
classification, deterministic known errors are excluded; other failures are
eligible for a bounded retry. Permanent/exhausted events require operator
intervention and are not automatically cleared. After correcting a provider
credential, model or input configuration, an operator can explicitly
requalify one failed event without manually editing the database.

A recovery refuses events with an already-written downstream risk assessment;
those require a separate data-integrity remediation. Success overwrites only
the event's semantic analysis fields and reconstructs event-relationship
enrichment. Its ID, tenant, snapshot pair and detection timestamp are stable.

## Operator workflow

The worker is read-only unless `--execute` is specified. Build the image first,
so the runtime code matches the patch. Keep the normal named `snapshot_data`
volume mounted: binding the repository over `/app` would hide the snapshot files.
Inspect due events:

```powershell
docker compose build api
docker compose run --rm api python -m scripts.semantic_analysis_recovery_worker --limit 5
```

Run one bounded recovery cycle, after confirming provider credentials and
snapshot-volume availability:

```powershell
docker compose run --rm api python -m scripts.semantic_analysis_recovery_worker --execute --limit 5
```

For dedicated deployment, build and enable the opt-in Compose profile:

```powershell
docker compose --profile semantic-recovery up -d --build semantic_recovery_worker
```

The profile is off by default to prevent unexpected billable LLM calls.
Do not enable it without validating the integration tests, provider budget,
logs and operational oversight. `--organization-id` limits manual recovery to
one tenant. `--event-id` selects a particular failed event but does not bypass
backoff, maximum attempts, ownership or risk-integrity checks. For an explicitly
repaired permanent or exhausted event, use **only** one targeted invocation:

```powershell
docker compose run --rm api python -m scripts.semantic_analysis_recovery_worker --execute --requalify --event-id <EVENT_UUID> --organization-id <ORG_UUID>
```

This command bypasses automatic retry eligibility only for a single failed
event. It still validates tenant/snapshot ownership, SHA-256 evidence, the
advisory lock and downstream conflicts, and records the override timestamp in
`semantic_delta_json.analysis_recovery`. `--requalify` is prohibited in loop
mode and requires an explicit tenant ID.

## Limitations

- The lock requires PostgreSQL; this worker is not a SQLite queue.
- Provider output quality is not established by a successful retry.
- If graph enrichment fails, semantic completion is rolled back; the persisted
  failed event remains blocked for downstream processing.
- A permanent error or exhausted attempt budget is not automatically retried,
  even if provider configuration changes. Requalification is a deliberate,
  one-event operation; do not reset attempts by manual SQL.
