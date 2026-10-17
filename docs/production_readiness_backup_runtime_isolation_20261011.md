# Database backup runtime isolation

## Risk and decision

The API used to mount the `backup_data` named volume read/write. That volume
holds entire PostgreSQL archives, including all organizations' records, not
just the tenant served by an API request. HTTP request handling has no reason
to access database dump files. This is avoidable privilege and blast radius.

The existing volume, `scripts.backup_database` implementation, format,
retention policy, restore utility, and all existing archives are unchanged.
Only the workload that can access them changes: `backup_maintenance` is an
explicit Compose `maintenance` profile job, not a continuously running service.
It uses a prebuilt local image and no `depends_on` startup chain, so a
maintenance command cannot implicitly run `runtime_volume_init` or start a
stopped production database. The image must be approved for the release and
available before maintenance. It receives a database connection and backup
settings, but no `.env` file of
AI provider credentials, and does not expose any network port. The public API
and workers have no mount to the database backup volume.

## Operation

On a development Compose deployment:

```powershell
docker compose --profile maintenance config --quiet
docker compose --profile maintenance run --rm --no-deps --pull never backup_maintenance
```

On production use the *same two Compose files as the live deployment* and
explicitly enable the maintenance profile, for example:

```powershell
docker compose -f docker-compose.yml -f docker-compose.prod.yml --profile maintenance run --rm backup_maintenance
```

Record the archive identifier, validate the job exit code and `pg_restore
--list` output, and transfer the archive to protected off-host storage. The
job is a backup, not a data erasure or tenant export action. Do not make
backups available through the API or upload them to issue trackers.

## Important limitations

- This job backs up the PostgreSQL database **only**. The `snapshot_data`
  volume stores raw content evidence in files and must be backed up separately
  with integrity verification and access controls before claiming recoverability.
- The Redis AOF/queues and externally processed content (AI services,
  webhook consumers and downstream databases) require their own recovery,
  retention and deletion policies.
- Any organization erasure process must reconcile already-created backups
  and any legally required retention windows. None is implemented here.
- The deployment operator must arrange scheduling, off-site encryption,
  key management and periodic restoration drills. This patch does not claim
  recovery completeness or remove archive copies.

## Regression acceptance

`tests/unit/test_backup_runtime_isolation.py` enforces the runtime boundary.
Run `docker compose --profile maintenance config --quiet` and check API health
before running a one-time operator-controlled maintenance backup. Never test
restore against the live customer database.
