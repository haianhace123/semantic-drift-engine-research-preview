# Organization data inventory v1 (P0 tenant boundary)

Status: **locally tested, requires HTTP integration and latency acceptance**, not a customer data export, a deletion mechanism, or legal/compliance evidence.

## Production risk addressed

`DELETE /api/auth/me` deactivates an identity, not an organization. The current ORM has 32 tables with direct `organization_id`; 6 additional indirect/global tables require separate ownership analysis. A PostgreSQL cascade cannot safely erase raw snapshot artifacts, provider copies, downstream RAG data, backups, or immutable evidence. A misleading full-export promise is worse than a clearly partial inventory.

## Endpoint

`GET /api/auth/organization/data-inventory`

- Existing `require_roles("admin")` authority and an additional active-admin check.
- Organization ID derives from the authenticated user; there is no client-supplied tenant selector.
- Reports **counts only** for 32 known direct-tenant tables, one artifact path presence count, nullable tenant columns, unresolved indirect/global tables and explicit exclusions. Rows whose organization ID is NULL are not assigned to a tenant and are not claimed as covered.
- Validates that the organization exists and that its snapshots do not reference monitored pages belonging to another tenant. Any mismatch or ORM coverage drift returns 503, without partially disclosing rows or paths.
- Does not return document contents, path values, email, API key metadata, snapshots, vectors, or backups.
- Does not commit, write, delete, create archives, access raw files, call providers, or change schema.
- Sends `Cache-Control: no-store`.

## Limits and gates

This is **not** complete tenant inventory: indirect ownership, file existence/ownership, backup expiration, transfers to external providers and downstream copies remain unknown. On a new direct tenant table, update the source authority and allowlist together; a table imported into ORM metadata that is not on the allowlist blocks responses. A new model that is not imported may be undetected, so the full repository source inventory must be repeated at each schema change.

Before deployment, run unit acceptance against local `.venv-sde`, existing authentication and tenant isolation tests, a database-backed two-tenant test with the current schema and a controlled response-time check. Do not deploy the route to shared production before those gates. The route is admin-only but 32 counts may be expensive on large datasets; if measurements show unacceptable latency, move inventory generation to a bounded asynchronous, auditable job rather than weakening correctness.

Future batches: verify 6 indirect/global table ownership; verify artifacts and object storage; build authorized export archive with provenance and download constraints; operator-reviewed erasure workflow with backup and provider proofs. Do **not** implement tenant erasure with `DELETE FROM organizations`.


## P0 transactional hardening (2026-10-15)

- Endpoint opens its own PostgreSQL `REPEATABLE READ, READ ONLY` transaction;
  it does not change isolation on the auth dependency's already-used Session.
- The logged-in identity is rechecked as an active administrator of the same
  organization inside that read-only snapshot, before executing counts.
- A PostgreSQL information_schema comparison inside that same snapshot blocks
  unregistered tenant tables, including those absent from ORM imports.
- `statement_timeout=1500ms`, `lock_timeout=750ms` and an application-level
  8-second aggregate time budget fail closed with 503. This is a bounded
  diagnostic; a large tenant may need an asynchronous export job later.
- Snapshot/path values are never returned. Database connection errors are
  sanitized. ORM and real DB had previously diverged by migration 049; the
  operator has now applied that migration and verified 32/32 tables.
- Remaining release gates: two authenticated tenant HTTP integration tests,
  query latency/lock behavior on development PostgreSQL and an explicit rollout.
- Do not mistake this partial count report for export, erasure or proof of
  organization-wide retention compliance.
- The second transaction also rechecks `organizations.is_active`; disabling a tenant
  between the initial token check and the inventory snapshot fails closed.

## Shared tenant ownership contract (2026-10-15)

The operator-only lifecycle audit and this bounded admin endpoint now import
the same `src.services.tenant_data_ownership_authority` definitions for the
four approved indirect FK paths, both ends of temporal correlations, and the
conservative non-direct table registry. The operator audit keeps its existing
result schema and snapshot-volume checks; it uses a repeatable-read, read-only
PostgreSQL transaction.

The admin API validates the shared FK contract but intentionally does **not**
return indirect counts or global unattributed-consent totals. Such data can
leak cross-tenant information without an independently reviewed exposure
policy and query cost budget. Existing v1 response and 8s time budget remain
unchanged. Cross-tenant correlation records remain an operator investigation
issue, not proof of complete organization export/erasure readiness.

`worker_operational_states` is global, not tenant owned. Raw snapshot paths,
retention and external provider copies remain outside the admin inventory.

## Canonical ORM model registration

Both the admin Inventory API and operator lifecycle audit now load reviewed ORM
metadata from `tenant_data_ownership_authority.registered_ownership_metadata()`.
The `lineage_successor_recovery_certificates` model is explicitly registered;
`src.models` package exports alone do not guarantee complete table coverage.
This does not change the scope of the customer-facing API or authorize export
or erasure. Any new tenant-related ORM table must trigger authority review.
