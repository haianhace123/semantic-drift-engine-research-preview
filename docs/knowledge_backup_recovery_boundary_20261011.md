# Knowledge evidence backup and recovery boundary

The legacy `backup_maintenance` service only writes a PostgreSQL `.dump`.
It **does not** back up raw snapshot files. The separate
`knowledge_backup_maintenance` service produces an operator-only directory
bundle with a verified `database.dump`, all regular files in `snapshot_data`'s
`raw` directory, a versioned manifest and SHA-256 verification of every
copied artifact. It validates that every PostgreSQL snapshot file reference
resolves inside that mounted directory and matches the recorded SHA-256.
Files that have no database reference are retained and counted, not discarded.

This is a new coordinated backup mode, **not an automated live backup**. The
service does not stop any process, lock the database, or make filesystem writes
atomic with PostgreSQL. A stopped-writer maintenance window is mandatory;
the `--acknowledge-writers-quiesced` flag is an operator attestation, not a
runtime lock. The tool performs before/after snapshot-reference checks, but
those checks cannot detect every concurrent mutation to other PostgreSQL
tables. A valid bundle still needs a test restore before being relied upon.

## Safe read-only plan

```powershell
docker compose --profile maintenance run --rm --no-deps --pull never knowledge_backup_maintenance
```

The default command prints `mode=plan_only`; no dump is produced. Both
maintenance jobs use a prebuilt, operator-verified image (`SDE_MAINTENANCE_IMAGE`,
defaulting to the local API image), `pull_policy: never`, and no Compose
dependencies. Never auto-build the maintenance image during a live-data check.
Confirm the approved image includes this source before performing any maintenance;
a previous image may not contain the knowledge-backup command. Do not run
the execute command with any API, scheduler, worker, external API, migration
job or other database/snapshot writer active. Arrange a maintenance window,
stop the relevant services, confirm all external writers are paused, and leave
PostgreSQL running. **Do not use `docker compose down -v`.**

Once writers are externally stopped and verified, a qualified operator may run:

```powershell
docker compose --profile maintenance run --rm --no-deps --pull never knowledge_backup_maintenance `
  python -m scripts.backup_knowledge_state `
  --execute --acknowledge-writers-quiesced
```

The script publishes the `knowledge-bundles/<UTC>-<random>/` directory only
after validating all files and running `pg_restore --list`. A failed run
removes only its own newly created incomplete directory. It does **not** purge
older backups. The existing `backup_maintenance` path and its `.dump` rotation
remain unchanged.

## Offline integrity verification

Supply an existing bundle path in the backup maintenance container:

```powershell
docker compose --profile maintenance run --rm --no-deps --pull never knowledge_backup_maintenance `
  python -m scripts.backup_knowledge_state `
  --verify /app/backups/knowledge-bundles/<bundle-directory>
```

This verifies manifest contents, hashes, completeness of the filesystem
inventory and the PostgreSQL archive format. It does **not** restore a database.

## Recovery requirements before a production promise

Restore `database.dump` to a freshly provisioned isolated PostgreSQL database,
not the production source database. Restore the `snapshots/` directory into a
separate private snapshot volume with the identical logical absolute path
(`/app/data/raw` in the release container); do not merge into the live volume.
Verify content hashes and the database snapshot references in the restored
environment using the read-only inventory. Validate migration compatibility,
worker preflight, downstream lineage, and the external systems before release.
Record measured RTO/RPO and an operator sign-off. This bundle excludes Redis
AOF/queues, external delivery effects, external vector stores, provider-side
copies, and offsite encryption/storage. Snapshot backup files are sensitive
customer data: restrict access, encrypt at rest and during transport, keep
independent off-host copies, and apply an explicit retention/erasure policy.

Important: a hash-verified bundle is still **not** proof of a completed disaster
recovery exercise. It may be created only in a verified maintenance window.

## Explicit preservation of known historical evidence deficits

The strict `--execute --acknowledge-writers-quiesced` path remains the default
and rejects every unresolved snapshot reference. A *separate opt-in* path may
preserve the byte-for-byte PostgreSQL archive and all existing raw files even
when some historical references are known to be unavailable. It **does not**
repair paths, synthesize files or claim full recovery. The only permitted
exceptions are historical synthetic seed paths whose inline bytes match the
stored SHA-256 and the retired seed corpus, and missing absolute snapshot
files (inside the mounted volume) with no inline bytes. Both categories must
belong to one explicitly approved organization. Other failures abort.

After a read-only forensic audit, the operator must specify the organization
UUID and *both* exact expected counts when executing in a stopped-writer
maintenance window:

```powershell
# Example only; do not execute until the maintenance window and external
# writer checks have been completed. Validate current counts first.
docker compose --profile maintenance run --rm --no-deps --pull never knowledge_backup_maintenance `
  python -m scripts.backup_knowledge_state --execute `
  --acknowledge-writers-quiesced --preserve-with-integrity-deficits `
  --preserve-organization-id '<explicit-organization-uuid>' `
  --expect-verified-legacy-inline 178 --expect-missing-raw 10
```

The execution command also releases idle PostgreSQL connections created by
its **own** snapshot-inspection SQLAlchemy pool before the final connection
gate. Other API/worker/external connections still block publication. This is
not a substitute for stopping writers or fencing reconnections.

The separate `sde-knowledge-bundle-preserved-v1` manifest records the
classification and a reference-inventory SHA-256 fingerprint. The isolated
restore rechecks the historical seed bytes, known missing artifacts, exact
counts and reference fingerprint after PostgreSQL restoration. A successful
preservation drill returns `isolated_restore_preserved_with_integrity_deficits`
**not** `isolated_restore_verified`. The missing 10 raw artifacts remain
missing in the isolated restore; their database records are retained intact.

This is recoverable *retained state* with explicitly identified evidence gaps,
not a fully healthy knowledge history. Full disaster recovery is not attested.
The stored SHA-256 fingerprint is an integrity check, not cryptographic
authentication of the backup manifest. Offline/off-host encrypted storage,
trusted signatures and regular drills remain separate production requirements.
