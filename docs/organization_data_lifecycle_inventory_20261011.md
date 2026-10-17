# Organization data inventory (Security 04)

## Purpose

Prior to implementing organization export or erasure, collect a tenant-scoped
inventory without reading document contents into logs or making state changes.
This is an operator diagnostic, not a customer endpoint or a deletion workflow.

```powershell
# Run in the existing API container; snapshot_data must be mounted.
docker compose exec -T api python -m scripts.audit_organization_data_lifecycle_readonly `
    --organization-id "<UUID>" --max-snapshots 500 --verify-hash
```

The script opens a PostgreSQL `READ ONLY` transaction. It counts tables that
explicitly have an `organization_id`, resolves specific ORM-verified indirect
ownership paths (`consent_logs`, `user_sessions`, lineage decision path revisions,
lineage observation dependencies, and correlations), checks the existence and (when requested)
content hashes of a bounded set of tenant snapshot artifacts inside the
canonical `data/raw` directory, compares mapped tables with the live database
schema, and lists tables that cannot be scoped without
following foreign-key relationships. It does not print original filenames,
URLs, raw content, vectors, tokens or filesystem paths. Output is stdout JSON.

### Interpretation

- `inspection_complete=false`: increase the bounded snapshot scan after reviewing
  the volume; never treat a partial scan as a full audit.
- `relative_path_unverified`: historical relative paths need a separate
  evidence-preserving migration, not a guessed working directory.
- `outside_snapshot_root`: path escapes the expected content volume and is not
  opened, even for hash verification.
- `missing_file` / `hash_mismatch`: do not delete or silently repair evidence.
- `unmapped_database_tables`: tables not registered in the loaded ORM must be
  audited separately; this is not a complete schema-level map. The lineage
  execution verification model is registered in the canonical model package; LangChain
  PGVector collections are separate external-consumer data and cannot be
  attributed to an SDE tenant without collection-level provenance checks.
- `indirect_table_counts`: records attributed by explicit FK joins. These are
  diagnostic counts, not a tenant erasure deletion plan.
- `correlation_integrity.cross_tenant_outgoing` and
  `correlation_integrity.cross_tenant_incoming`: inconsistent associations in
  either direction. Any nonzero count blocks integrity and ownership claims.
- `unattributed_consent_records_global`: historical records not attributable
  to any organization. These are preserved for separate provenance handling.
- `unresolved_tables`: ownership not yet established, such as global worker
  state. A direct-table count alone does not establish completeness.
- `erasure_ready=false`: deliberately hardcoded until an authorized workflow
  has defined a complete dependency, artifact, provider and backup policy.

### Known out-of-scope data

PostgreSQL custom-format dumps hold records across organizations and are not
an archive of the snapshot_data volume. External DeepSeek/Gemini processing,
webhooks, downstream consumer copies, backups stored outside Compose, and caches
need separate inventory, contracts and retention evidence. A successful
`pg_restore --list` check is not a full restore drill.

No database rows or snapshot files are deleted or rewritten by this audit.
