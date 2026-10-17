# Tenant provenance: model registry and consent authority

The organization data inventory originally missed the
`lineage_invalidation_execution_verifications` table because its ORM model was
imported by individual lineage services but was not exported from the canonical
`src.models` package. This table has an explicit `organization_id` and belongs
in direct tenant counts. The model is now registered there; no database schema
change or migration is needed.

The inventory now includes FK-verified tenant counts for `consent_logs`,
`user_sessions`, `lineage_invalidation_decision_path_revisions`, and
`lineage_observation_dependencies`, plus both sides of each temporal lineage
correlation. Any cross-tenant correlation is reported separately. Historical
null-`user_id` consent records are counted globally and cannot be attributed
retroactively without evidence. None are changed or deleted by this batch.

The unused `POST /api/auth/consent` endpoint previously accepted caller-chosen
policy versions, source IP, and browser user-agent while persisting
`user_id=NULL`. It has been retired: this claim of consent was not bound to
an identifiable data subject. Signup remains the identity-bound consent
writer using the server-side `settings.TERMS_VERSION`. External clients using
the retired endpoint will now receive 404; release notes and API consumers
must be checked before deployment. The service must not fabricate or migrate
anonymous historical consent into user-scoped consent.

`worker_operational_states` remains global, not a tenant resource. Historical
LangChain/PGVector tables are not automatically SDE-owned even if present in
the authority database; investigate original provenance before assigning or
deleting them. `erasure_ready` remains false until database, snapshot volume,
Redis, backups, processors and external recipients have a complete lifecycle.
