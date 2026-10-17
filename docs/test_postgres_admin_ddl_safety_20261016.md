# Test governance P0.3 — PostgreSQL admin DDL isolation

## Finding

An opt-in integration flag (`RUN_DB_INTEGRATION_TESTS=1`) was sufficient for
`test_database_backup_restore.py` to issue `CREATE DATABASE`, terminate
sessions and `DROP DATABASE` against the server selected by `DATABASE_URL`.
The DR test required a second flag but did not validate the server identity.
Two other tests created/dropped schemas on whichever server was configured.
Passing these tests against a shared service is not a valid acceptance mode.

## Correct contract

The four tests refuse all admin DDL unless `ENVIRONMENT=test`, both PostgreSQL
URLs equal the **fixed disposable Compose URL**, and the separate
`SDE_DISPOSABLE_PG_DDL_APPROVED=YES` acknowledgement is present. This is
**defense in depth**, not independent proof of Docker isolation: the runner
must independently verify that Compose resources are private and disposable.
Never set the opt-in on the normal/pilot project.

A failed backup test now only drops databases whose creation was confirmed.
No application backup/restore behavior was changed.

## Acceptance

1. Unit: `python -m pytest -q tests/unit/test_disposable_postgres_admin_guard.py`
2. Full unit suite, with skips documented and not silently counted as passes.
3. Isolated PostgreSQL project: exercise all four integration modules, explicitly
   check executed identities and zero skips before claiming database acceptance.
4. Git source integrity: only the seven reviewed paths changed and unrelated
   dirty entries remain intact.

## Coverage and remaining risks

This batch protects *four admin-DDL integration modules*, not every integration
module that can write tenant data. Remaining integration tests still require
strictly isolated PostgreSQL/Redis. Do not run the full integration suite on the
normal project just because the admin-DDL tests now have a guard.

The static review of all integration modules is an inventory, not evidence that
every test has been executed or that old contracts are correct. Review legacy
UI/source-string assertions and customer-value coverage in separate batches.
