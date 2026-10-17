# Migration DDL / Ledger Atomicity

The migration runner is the only authority that commits a migration. Each
migration's PostgreSQL DDL and its `schema_migrations` ledger entry must succeed
or fail in the **same transaction**. The runner continues to hold its existing
session-level advisory lock for the entire migration run.

## Previous crash window

Many historical SQL files were authored for direct `psql` execution and include
an outer `BEGIN; ... COMMIT;`. Executing that text as-is commits DDL before the
runner inserts the ledger row. Process termination or a ledger insert failure
between those operations can leave a committed schema change with no recorded
version, and the next deployment can accidentally re-run it.

## Contract

- For files with one complete outer `BEGIN; ... COMMIT;`, the runner excludes
  only those two transaction-control statements at execution time. It leaves the
  file on disk unmodified.
- Files without transaction wrappers execute unchanged.
- Partial, nested, or misplaced standalone transaction wrappers are rejected.
- The runner's existing DB connection commits only after both DDL and ledger
  insert succeed; any failure rolls the complete transaction back.
- Existing migration ledger rows and already-applied schema are not rewritten.
- This change does not promise transactional safety for **future** statements
  PostgreSQL prohibits inside a transaction (such as `CREATE INDEX CONCURRENTLY`).
  Such a migration requires an explicit, separately reviewed execution strategy.

## Verification

Run focused unit regression, including a contract check against every SQL
migration in the repository, then run the isolated PostgreSQL integration test.
The integration test creates a unique temporary schema, intentionally rejects a
ledger insert, verifies that its DDL was rolled back, then proves that a valid
migration records DDL and ledger together. It drops the temporary schema afterward.

Do not execute the integration test against a customer production database.
Back up before normal schema migrations and stop all application write paths
when an incompatible schema deployment requires maintenance.
