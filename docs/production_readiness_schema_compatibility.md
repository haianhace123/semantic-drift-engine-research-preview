# Production schema compatibility preflight

Production database consumers must not start against a schema for which their
binary has no explicit migration contract. The release's `scripts/migrations`
directory is the authoritative migration manifest; PostgreSQL
`schema_migrations` is the record of what was applied.

## Enforcement

- Production Compose invokes `python -m scripts.schema_preflight -- <command>`
  before starting the API, vector worker, alert delivery worker, crawler
  scheduler, lineage invalidation executor and optional semantic recovery
  worker. The preflight is read-only.
- Production `/ready` rechecks schema compatibility on every probe, including
  after the process has started. `/health` remains a liveness-only endpoint.
  The production API container healthcheck uses `/ready`.
- Both missing ledger versions and versions unknown to the running binary
  cause a failure. An uninitialized database cannot pass preflight. This
  deliberately disallows speculative forward compatibility.
- The email worker and Redis-only infrastructure do not require PostgreSQL
  schema preflight.

## Deployment sequence

1. Build immutable release images with the matching migration manifest.
2. Back up the database and verify the backup is recoverable.
3. Stop application database writers before any potentially incompatible
   migration. Do not assume rolling app versions are safe.
4. Apply migrations once using the matching release's `python -m scripts.migrate`.
5. Verify that the database ledger contains every release migration version
   and no unknown versions. Do not manually manufacture ledger entries.
6. Start the release's PostgreSQL-dependent services using production Compose.
7. Check `/ready`, verify workers and execute smoke tests before enabling traffic.

Rollback of an application binary on a newer schema is **not** implicitly
supported. Rollback requires a separately reviewed compatibility proof or a
restored compatible database. Do not remove migration ledger rows to force a
binary to start; this cannot reverse applied DDL.

## Boundaries

This gate checks migration **version identity**, not the checksum or full
structural integrity of schema objects. A modified SQL file with the same
filename cannot be detected retroactively by the current ledger, which only
stores `version` and `applied_at`. Introducing content hashes would require an
explicit historical migration verification strategy.

The entrypoint checks once at process startup. `/ready` detects later schema
changes for API routing, but does not stop already-running background workers.
For this reason, operations must stop old workers before applying incompatible
migrations. Automatic migration on worker startup is intentionally prohibited.

## Acceptance

The unit suite tests manifests, old/new ledger mismatches and failure to
start a worker command. The integration test uses an isolated PostgreSQL test
schema to prove missing and unknown versions fail without mutating the ledger.
Run the integration test only on a development/integration database.
