# PostgreSQL disaster recovery acceptance

PR.13 validates the combination of the existing migration runner, PostgreSQL
backup/restore utilities and production schema compatibility gate. It does not
introduce a second backup system or modify production runtime code.

## Scope

The opt-in integration test creates two randomly named PostgreSQL databases
with the `sde_pr13_` prefix. It applies the entire shipped migration manifest
to the source, inserts two distinct organizations with overlapping source and
asset identities, and records related entities, monitored pages, snapshots,
semantic events, Risk v2 assessments and exact-revision lineage observations.
It creates a custom-format backup using `scripts.backup_database` and restores
it into the other **empty, disposable** database using `scripts.restore_database`.

Acceptance requires:

- The restored migration ledger passes `assert_schema_compatible` with the
  current release manifest.
- Representative records, content, statuses, relationships and tenant-scoped
  row counts agree exactly between source and restored databases.
- The production worker schema preflight accepts the restored database. The
  test uses a harmless command in place of a live worker.
- The real FastAPI `/health` and production `/ready` handlers pass against the
  restored database and reachable test Redis.
- Injecting an unknown migration version into the restored **test** database
  makes `/ready` return 503 and worker preflight refuse startup; removing the
  injected version restores readiness without altering application data.

All test databases are removed in `finally`. The configured `DATABASE_URL`
serves **only as an admin connection for CREATE/DROP DATABASE**; the test never
runs migrations, restore or data seeding on that database. The run requires a
PostgreSQL user with `CREATEDB` privileges and the pgvector extension available
on the same server. Do not run it against a production server.

## Run

Run this test **only** in a reviewed, disposable Docker Compose project with
separate PostgreSQL/Redis volumes, no host ports, no production secrets and
verified database URL `postgresql://sde_integration:sde_local_test_only@db:5432/sde_integration`.
Do not run it through the default `docker compose run api`, which may share the
normal project database. In that isolated test container, set:

```text
ENVIRONMENT=test
RUN_DB_INTEGRATION_TESTS=1
RUN_DR_ACCEPTANCE_TESTS=1
SDE_DISPOSABLE_PG_DDL_APPROVED=YES
DATABASE_URL=postgresql://sde_integration:sde_local_test_only@db:5432/sde_integration
TEST_DATABASE_URL=postgresql://sde_integration:sde_local_test_only@db:5432/sde_integration
```

Execute `python -m pytest -q tests/integration/test_disaster_recovery_acceptance.py`
there, with `--junitxml` and an explicit no-skip acceptance check. The backup
round-trip, schema compatibility and migration atomic-ledger tests use the
same guard. Without the relevant test opt-in the DR test is skipped; if a
caller enables it on an unapproved database, the test **fails closed**. The test does
not send email, run crawlers, run active workers, contact AI providers, or
perform live downstream invalidation.

## Production recovery procedure (not automated by this test)

1. Declare the recovery incident. Stop all API writes and background writers;
   isolate old processes and do not allow them to resume against a restored DB.
2. Select a verified, access-controlled backup stored outside the failed host,
   from a known release and migration manifest. Establish the allowed data-loss
   window and record the recovery point.
3. Provision a new PostgreSQL instance and a **new empty database**. Verify
   PostgreSQL/pgvector versions and restore permissions. Restore into that
   target, never over an active source database.
4. Point the matching release of SDE at the restored DB. Check schema preflight
   and `/ready` before accepting API traffic or re-enabling PostgreSQL workers.
5. Validate tenant-scoped evidence and referential integrity. Reconcile
   external system state, Redis queues and any pending side effects before
   resuming workers; replay/rebuild only through the owning subsystem.
6. Record observed recovery time, last recoverable data point, missing side
   effects, and operator sign-off before reopening traffic.

## Limitations

This is an **isolated integration acceptance**, not a completed disaster
recovery exercise. It does not restore a production-sized dataset or simulate
loss of an entire host or volume. It does not validate the contents of Redis
AOF, pending email jobs, producer acknowledgments, external PGVector indexes,
webhook delivery or other external systems. PostgreSQL-only backup is not an
exactly-once recovery boundary across these systems.

The representative fixtures validate data retention and tenant partitioning
of persisted records, not authorization of every API endpoint. Existing
cross-tenant API and worker tests remain the authority for those contracts.
Preflight with a harmless process proves **admission**, not successful worker
job replay. RTO/RPO targets and backup off-site durability remain unproven
until an operational drill is run and measured on the intended deployment.
