# Semantic analysis gap recovery

## Failure being addressed

Snapshot storage commits before semantic detection runs. A crawler can therefore
persist a new snapshot and terminate before creating its `SemanticDriftEvent`.
The existing semantic recovery worker previously selected only events with
`analysis_status = 'failed'`. With no event row, there was nothing to retry.

## Recovery contract

The existing semantic recovery worker now has an optional reconciliation step.
It inspects a bounded window of recently persisted snapshots, finds changed
adjacent pairs without an event, and registers one `pending` event per pair.
Registration rechecks organization ownership, active organization status,
chronological adjacency and changed content while holding the monitored-page
row lock. PostgreSQL's existing unique snapshot-pair constraint is the final
deduplication authority. Registration does not call a semantic provider.

Only `pending` rows marked `analysis_recovery.source = snapshot_gap` are admitted
to the existing semantic recovery path. Unrelated legacy `pending` rows are not
changed. The worker uses its existing event-specific PostgreSQL advisory lock,
durable attempt reservation, bounded exponential backoff, snapshot-file hash
verification, provider-result validation and downstream-decision guards.
Semantic analysis is persisted in the **same event**, never a replacement row.

A pending or failed analysis is not downstream-ready. Missing snapshot files,
corrupt content and exhausted attempts remain visible as incomplete evidence;
none are converted into successful semantic changes.

## Operation

The Compose `semantic_recovery_worker` profile remains opt-in. Missing-event
reconciliation is **separately** opt-in via
`SEMANTIC_RECOVERY_RECONCILE_MISSING=1` (default `0`). This prevents a routine
worker restart from silently launching historical provider work. Review the
detected backlog, provider capacity and tenant authorization before enabling
this flag. A manual inspection does not write:

```bash
python -m scripts.semantic_analysis_recovery_worker \
  --reconcile-missing --organization-id <ORG_UUID> --limit 5
```

For an approved recovery run:

```bash
python -m scripts.semantic_analysis_recovery_worker \
  --execute --reconcile-missing --organization-id <ORG_UUID> \
  --limit 5 --gap-scan-limit 2000
```

The default scan examines at most 2,000 latest *new* snapshot observations per
cycle, and at most five missing pairs are registered per cycle. The scan limit
can be increased to 10,000 for a controlled repair. These bounds protect a
shared production database from an unbounded history scan. They also mean
that gaps older than the selected window are **not automatically recovered**.
An operator must first assess the historical backlog before treating the
pipeline as fully caught up. The scan is not a general migration/backfill tool.

## Acceptance criteria

- A changed pair with no event becomes an explicit pending event.
- Two concurrent registrars cannot create duplicate events.
- Earlier changed adjacent pairs remain discoverable even after newer crawls.
- Unchanged pairs and cross-tenant or non-adjacent pairs are rejected.
- A provider crash after reservation consumes an attempt, respects backoff and
  leaves the event recoverable with its original identity.
- Verified provider results complete the existing event; invalid results do
  not make it downstream-ready.
- No external website or semantic provider is called from the integration test.

The unit tests and PostgreSQL acceptance tests do not prove the quality of
model output on customer documents, nor recovery of external indexes or queues.
