# Isolated knowledge recovery drill

## Purpose and scope

Security 06 writes a PostgreSQL + raw snapshot knowledge bundle with a
verified manifest. This release adds a **separate, disposable** restore drill:
`docker-compose.restore-isolated.yml` and
`scripts.restore_knowledge_state_isolated`. The drill restores into a fresh
PostgreSQL/pgvector database and a fresh snapshot filesystem volume, then
checks every stored database-to-snapshot reference against SHA-256.

The restore job cannot use live SDE credentials or volumes. Its Compose stack:
- uses a distinct project name beginning `sde-restore-`;
- uses a separate internal-only Docker network without host ports;
- mounts only the existing *backup* volume read-only;
- creates two new project-scoped volumes for restored PostgreSQL and snapshots;
- passes no `.env` settings, API keys, Gemini or DeepSeek credentials;
- refuses nonempty target volumes and unexpected database host/name.

The source backup volume contains **all organizations**. Treat access to it
and the restored test database as privileged customer-data access. Do not
publish or upload either set of data. The bundle verifier detects corruption;
this is not a cryptographic signature or proof against a malicious operator.

## Before starting

You need a real knowledge bundle produced by Security 06 during a verified
writer-quiesced maintenance window. The earlier `backup_maintenance` output
`driftdb-*.dump` is **not** a knowledge bundle and cannot be used here.

First run the isolated static/contract checks against source. This is safe
without a knowledge bundle:

```powershell
Set-Location C:\HaiAnh\semantic_drift_engine

docker compose run --rm -v "${PWD}:/app" api python -m pytest -q `
  tests/unit/test_knowledge_restore_isolation.py `
  tests/unit/test_knowledge_backup_boundary.py `
  --tb=short
```

The restore runner must use an approved, **prebuilt local** image
(`SDE_RESTORE_IMAGE`, defaulting to `semantic_drift_engine-api:latest`)
that contains this exact restore source. Its Compose service has no `build`
and `pull_policy: never`. If the image is missing or outdated, stop and
plan a separate, controlled image release. Do not rebuild the existing
production image or stack as part of this drill.

Only after a knowledge bundle exists, identify the production Compose backup
volume *from the effective Compose config*, rather than assuming its name:

```powershell
$source = docker compose config --format json | ConvertFrom-Json
$env:SDE_RESTORE_SOURCE_BACKUP_VOLUME = $source.volumes.backup_data.name
if (-not $env:SDE_RESTORE_SOURCE_BACKUP_VOLUME) {
  throw 'Cannot identify source backup volume.'
}
```

Identify the actual knowledge-bundle directory name using a read-only,
network-disabled inspection of the backup volume. This does not copy contents
onto the host or invoke a production worker:

```powershell
docker run --rm --network none `
  --mount "type=volume,source=$($env:SDE_RESTORE_SOURCE_BACKUP_VOLUME),target=/source,readonly" `
  semantic_drift_engine-api:latest `
  python -c "from pathlib import Path; p=Path('/source/knowledge-bundles'); print('Knowledge bundle IDs:', [x.name for x in sorted(p.iterdir()) if x.is_dir()] if p.exists() else [])"
```

If this prints an empty list, **no combined knowledge backup exists yet**.
Do not substitute a standalone PostgreSQL dump. Once a real bundle has
been produced in an approved maintenance window, set its exact identifier:

```powershell
$env:SDE_RESTORE_BUNDLE_ID = '<real bundle directory name>'
$env:COMPOSE_PROJECT_NAME = 'sde-restore-' + [guid]::NewGuid().ToString('N').Substring(0, 12)
$isolated = '.\docker-compose.restore-isolated.yml'
docker compose -f $isolated -p $env:COMPOSE_PROJECT_NAME config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Isolated Compose validation failed.' }
```

Never combine `-f docker-compose.yml` with the isolated Compose file, never
reuse the live project name, and never run a bare `docker compose down -v`.

## Restore verification in disposable environment

The following command creates **only** the dedicated restore DB/snapshot
volumes and executes the isolated restore. It is not read-only inside those
newly created volumes. No live PostgreSQL or snapshot volume is mounted.

```powershell
docker compose -f $isolated -p $env:COMPOSE_PROJECT_NAME run --rm --pull never restore_runner
if ($LASTEXITCODE -ne 0) { throw 'Isolated restore verification FAILED.' }
```

A successful run reports `result=isolated_restore_verified` with the counts
of restored snapshot rows, referenced files, and unreferenced files. The job
first verifies every bundle hash and `pg_restore --list`, refuses nonempty
target stores, copies files with SHA-256 checks, invokes `pg_restore` with
`--single-transaction --exit-on-error`, then re-reads restored PostgreSQL
snapshot references and verifies corresponding files.

A failed run is **not** a successful recovery. Do not retry against the same
project; inspect the error and recreate a fresh isolated project. If the
source bundle needs correction, never modify the original bundle in place.

After retaining the sanitized operator report, delete only the disposable
environment using *the exact same isolated file and project ID*:

```powershell
docker compose -f $isolated -p $env:COMPOSE_PROJECT_NAME down --volumes --remove-orphans
```

The source backup volume is declared `external: true`; Compose does not remove
it through this cleanup. Nevertheless, verify the command arguments before
execution. Avoid leaving full customer-database copies in disposable volumes.

## Boundaries of success

This drill proves the backup can be restored into a disposable PostgreSQL
instance with matching raw snapshot data; it does not prove Redis jobs,
provider-side retention, external downstream targets, webhook history,
future schema compatibility, availability, tested RTO/RPO, or offsite
durability. It does not authorize erasure of any tenant. The isolated network
prevents ordinary external service access; conduct further penetration and
backup-key management reviews before an international production claim.
