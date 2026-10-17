# Customer pilot crawl outcome visibility

## Problem

Before PR.17, a monitored source recorded its last successful crawl and next
scheduled attempt, but not the outcome of a failed attempt. A customer could
have zero snapshots for an unreachable URL and no way to distinguish that from
a source that the scheduler had not visited yet. Worker-level operational logs
were not an appropriate tenant-facing source of truth.

## Contract

Migration `048_monitored_page_crawl_outcomes.sql` adds fields to
`monitored_pages` for the most recently **finalized** crawl attempt:

- `last_crawl_attempt_at`: PostgreSQL server time of finalization
- `last_crawl_status`: `succeeded`, `failed`, or null for historical/unattempted rows
- `last_crawl_failure_code`: one of six stable, source-safe categories, or null
- `consecutive_crawl_failures`: count reset after a successful finalization

The existing scheduler claim token and row lock remain the sole authority to
update these fields. An expired or superseded claim cannot alter the latest
outcome. A failure advances `next_crawl_at` to the existing retry interval but
never advances `last_crawled_at`. A success records the successful crawl time,
clears the failure classification and resets the failure count.

Public HTML crawler transport and extraction failures are grouped as
`blocked_destination`, `fetch_failed`, or `content_rejected`. Other adapters
report `unsupported_source`, `snapshot_unavailable` or `internal_error` as
appropriate. These are intentionally coarse categories. Never persist or send
upstream exception messages, response bodies, host resolution information or
provider credentials through the source inventory API.

A committed snapshot remains a successful **crawl** even if subsequent semantic
detection throws an exception. The source diagnostics endpoint already
separates changed snapshot hashes without a semantic event from a completed
analysis. This patch does not claim to automatically recover every missing
semantic event; that is a separate recovery concern.

The React source inventory displays last finalized attempt, crawl result,
consecutive failures and an explanation for known failure categories. Its
`Next scheduled` field uses the existing PostgreSQL retry schedule. These
fields describe crawl execution, not semantic-provider availability, vector
analysis, verified risk or downstream lineage.

## Migration and deployment

Apply migration 048 on a backed-up development/staging database before testing
or starting a new binary. PR.12 production schema compatibility preflight will
reject a mismatched schema until all migrations for this binary have been
applied. For production rollouts, stop old database-writing processes before
executing a migration incompatible with their ORM, apply the migration, then
start services using the new binary. No historical outcome is backfilled or
fabricated. A rollback to an older binary must follow the documented schema
compatibility/deployment policy; application rollback alone is not sufficient.

## Acceptance

`tests/unit/test_crawl_outcome_contract.py` covers classification, atomic
success/failure bookkeeping and the persisted-snapshot/failed-detector boundary.
`tests/integration/test_source_crawl_outcome.py` uses PostgreSQL for a failing
scheduled crawl, tenant-scoped API visibility, a successful retry and the
rejection of a superseded claim's late failure finalization.

The test uses deterministic mocked upstream fetch behavior and does not visit a
public website. Production failure-rate measurements, real-site HTML formats,
semantic model quality and browser interaction are not certified by this test.
