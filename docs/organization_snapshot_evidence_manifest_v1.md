# Organization Snapshot Evidence Manifest v1 (operator only)

## Purpose

Give an authorized SDE operator a bounded, reproducible snapshot-evidence record for a tenant during customer-pilot support or incident investigation. This deliberately is **not** a self-service organization data export, data portability package, audit attestation, deletion certificate, or full evidence bundle.

The primary authority is PostgreSQL plus the existing shared tenant ownership rules. No new database table, ORM authority, external provider call, or endpoint is introduced.

## Exact scope

- Reads organization state, monitored page IDs, snapshot IDs, SHA-256 content hash, crawl timestamp, page type, embedding status, and counts.
- Does not select or output source URLs, raw document content, raw paths, embedding vector values, embedding errors, user data, API keys, or provider inputs.
- Source/snapshot relationships are checked for cross-organization integrity before any records are serialized.
- Compares the live PostgreSQL organization-id schema to SDE's registered direct ownership authority.
- Limits records to at most 200 latest snapshots; explicitly returns `TRUNCATED` when there are more. No claim of total-organization completeness.
- Uses `REPEATABLE READ, READ ONLY` and per-query timeout. Invalid hash, inconsistent joins, missing active organization, schema changes and failed reads prevent output.

## Security model

Only an operator with existing database access can invoke the CLI. This is intentionally **not** an authenticated customer HTTP API. The caller is responsible for authorizing the target tenant and for handling the output in accordance with customer agreements.

The command requires an explicit tenant UUID and absolute output path outside the repository, refuses overwrite and symlinks, and requests mode 0600 on POSIX systems. Do not commit the generated JSON, print its contents to logs, attach it to public tickets, or send it outside the approved channel.

File hashes evidence stored metadata, not independently verified file contents. Use the separate operator audit with hash checking when actual snapshot artifact integrity is required.

## Not included

Organization identity export, personal data/consent history, exports of monitored source URLs, complete raw snapshots, temporal/risk evidence, lineage, external providers, caches, vectors, backups, third-party LangChain consumers or retention/erasure steps. The two empty LangChain/PGVector tables in the currently audited database do not establish coverage of any external vector store.

## Production release gate

This is an operator-only candidate. Before self-service customer export, implement actual session-level authorization, durable export access auditing, customer scope, artifact retrieval, completeness proof across systems, expiry/download controls, and a separately reviewed privacy/retention contract. A passing operator manifest test is insufficient for those claims.
