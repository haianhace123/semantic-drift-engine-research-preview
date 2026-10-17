# Global knowledge backup readiness audit

This is a read-only, all-tenant diagnostic to run **before** scheduling a
PostgreSQL/snapshot maintenance window. Organization-scoped inventory cannot
establish global backup feasibility: historic data may retain relative paths,
missing artifacts, inconsistent hashes or unreferenced files that did not occur
in the controlled pilot organization.

The diagnostic reuses the reference-reader and volume enumeration from
`backup_knowledge_state.py`. It checks every `page_snapshots` row and the
entire raw snapshot directory; it never emits document bodies, snapshot IDs,
customer file paths, provider credentials or SQL statements. Reported issue
counts identify the class of repair needed without selecting data for deletion.

Run while the live stack is still running, through the maintenance image so
`/app/data/raw` is the same **read-only** snapshot volume used by the backup:

```powershell
docker compose --profile maintenance run --rm --no-deps --pull never `
    knowledge_backup_maintenance `
    python -m scripts.audit_global_knowledge_backup_readiness_readonly --verify-hash
```

`--no-deps` avoids starting `runtime_volume_init`, whose permissions step can
write metadata to the snapshot volume. `--pull never` and `pull_policy: never`
refuse image retrieval; the maintenance image must already exist locally,
contain the reviewed audit source, and be explicitly verified by the operator.
No backup/restore command in this document is authorized by a passing unit test. This command does not stop services,
create backups, or modify database state. Its successful exit code means the
inspection completed; inspect the JSON `snapshot_integrity` result:

- `pass`: all inspected absolute raw-file references exist with matching
  hashes, or the snapshot row has inline content. Any unreferenced regular
  files are counted and will be preserved by the bundle builder.
- `blocked`: inspect the aggregated `issues`. Do not schedule downtime until
  the blockers are understood. Do not delete or overwrite historic evidence to
  force a green result.
- `inspection_failed`: the audit could not establish global integrity; output
  deliberately redacts exception messages to protect customer data.
- `hash_not_verified`: hash verification was not requested; not sufficient for
  the recovery milestone.

Even a `pass` does not mean a consistent backup is safe **right now**: this
read-only audit is not a quiescence lock, does not inspect the other writers,
and does not prove PostgreSQL can be restored. Use the existing Security 08/09
connection gate and a controlled maintenance window for actual execution, then
restore into the Security 07 isolated target to establish recoverability.

The legacy `backup_maintenance` `.dump` alone excludes raw snapshots; it cannot
be substituted for a knowledge bundle when making disaster-recovery claims.
