# Customer pilot source evidence acceptance

## Scope

PR.16 adds a read-only tenant-scoped diagnostics endpoint and a source-level
inspection control in the React workspace. It does not change the ingestion,
semantic provider, embedding, risk, or lineage authorities.

`GET /api/sources/{page_id}/diagnostics` returns the newest persisted snapshot
and compares the two newest persisted snapshots for the requested source. The
response distinguishes no baseline, one baseline, unchanged content hashes,
changed hashes without a persisted semantic event, and the status of an exact
snapshot-pair event. The response also surfaces the latest snapshot embedding
status and the event vector-analysis status as distinct stages. It never
returns raw provider errors, snapshot text, or other tenants' evidence.

The latest two *database* snapshots are not guaranteed to be the detector's
latest two *usable* snapshots. Missing snapshot files, worker admission,
provider availability, recovery, risk qualification and exact downstream
lineage are separate concerns. No event must not be called a completed
analysis or an analysis failure. Neither a provider-completed semantic analysis
nor a snapshot-count increase implies downstream impact.

## Acceptance

The integration suite exercises a new source from authenticated enrollment
through scheduler claim, two local-HTML crawls, immutable snapshot persistence,
a deterministic semantic-provider stub, and a persisted semantic event. It then
queries diagnostics over the same authenticated API. A second test validates
empty/unchanged/changed/missing-event/failed-event states and cross-tenant 404.
No external network or LLM call occurs in the tests.

```powershell
 docker compose run --rm -e RUN_DB_INTEGRATION_TESTS=1 -v "${PWD}:/app" api `
     python -m pytest -q tests/integration/test_source_diagnostics_journey.py --tb=short
```

The PostgreSQL integration must pass before PR.16 is committed. As a separate
product acceptance step, verify the evidence inspection control in a running
browser session for admin, user and viewer roles. These automated tests do not
simulate user interaction in a real browser or exercise external websites.

## Deliberately outstanding

Crawl failure reasons are not persisted in the current monitored-page schema;
the UI must not invent them. The backend also does not yet provide a robust
historical stage-by-stage processing timeline or RTO/SLO measurements. Those
capabilities require separately justified contracts and operational evidence.
