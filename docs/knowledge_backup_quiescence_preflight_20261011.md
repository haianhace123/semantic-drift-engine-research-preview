# Knowledge backup maintenance: read-only preflight

The knowledge backup and isolated restore paths remain **unproven at runtime**.
Do not use a successful static check or this preflight as evidence of recoverability.

## Why a separate gate exists

`--acknowledge-writers-quiesced` in `backup_knowledge_state` is an operator
assertion. SHA-256 and PostgreSQL dump checks cannot guarantee a consistent
cross-store state while a scheduler or external client writes database records.

`check_knowledge_backup_window.ps1` first refuses any running Compose service
with SDE write capability. It then launches an ephemeral read-only PostgreSQL
observer with no dependencies; the observer refuses any other client session
connected to the source database, or insufficient session visibility. The
observer prints counts, not SQL text, IP addresses or credentials. It runs
with `--no-deps --pull never` using an existing prebuilt maintenance image;
the preflight must not build, pull or restart the production runtime. A missing
or outdated image is an operational blocker, not a reason to rebuild it
during a live-data inspection.

**This is not an exclusive lock.** Clients could reconnect immediately after
preflight. Operator must also verify and stop any host-based scripts, external
clients, cron tasks, migrations, provider callbacks and unknown writers.
A maintenance window with controlled ingress remains mandatory.

## Enforcement inside backup execution

The knowledge backup executor also performs the read-only PostgreSQL session
check before creating a staging directory and immediately before publishing
its finished bundle. The `--acknowledge-writers-quiesced` flag cannot bypass
an active connection or insufficient session visibility. A failed final
check removes the incomplete staging directory and preserves prior backups.

This is defense in depth, **not writer fencing or a cross-store lock**.
Applications and external writers still have to be stopped and prevented from
reconnecting. The checks cannot detect unconnected filesystem writers or a
write that completes between two inspections.

## Before making an actual backup

1. Notify affected users about scheduled downtime, confirm independent copies
   of prior backups and snapshot artifacts. Record the exact running services.
2. Stop SDE writer-capable services deliberately (not PostgreSQL itself),
   including any optional profile processes. Ensure no external writers run.
3. Execute the preflight immediately before the backup:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File ".\scripts\check_knowledge_backup_window.ps1"
if ($LASTEXITCODE -ne 0) { throw "Preflight refused the maintenance window." }
```

4. If preflight fails, **do not use** `--acknowledge-writers-quiesced`. Investigate
   and rerun the read-only check. Never terminate database connections blindly.
5. Only a qualified operator with confirmed ingress isolation may invoke the
   opt-in knowledge backup command documented in
   `docs/knowledge_backup_recovery_boundary_20261011.md`, then verify the bundle
   and perform the isolated restore drill from
   `docs/isolated_knowledge_restore_drill_20261011.md`.
6. Restart only the services intentionally stopped, and verify health and the
   evidence pipeline before reopening traffic.

Do not use `docker compose down -v` or reset a PostgreSQL or snapshot volume.
The preflight neither creates a backup nor changes production service state.
This operational guard does not cover Redis state, provider retention, off-host
durability or destructive recovery and cannot establish RTO/RPO.
