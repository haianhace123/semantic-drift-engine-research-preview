# Deployment operations data boundary

Status: source-level mitigation candidate. Not a complete tenant isolation certification.

The `User.role == "admin"` identity is an **organization administrator**, not a
platform operator. In the current architecture, `WorkerOperationalState`
contains deployment-global process identifiers and unstructured exception
messages. These records cannot be owned by an individual organization.

`GET /api/operations/workers` may continue returning shared worker-kind
availability and aggregate counters for compatibility, but must never
serialize individual instance IDs, timestamps, or raw error strings to an
organization administrator. The `instances` field remains an empty list to
avoid an immediate response-schema break. Counts do not describe individual
tenant resource health; their meaning must be made clear in the customer UI.

Platform operators need a separate identity/authorization authority before
detailed operational diagnostics can be exposed over HTTP. Avoid adding an
operator-role flag to organization users. Internal read services retain the
unredacted view for controlled, non-tenant use.

Limitations and follow-up:
- This change does not prevent worker exception strings from being persisted
  in the global operational state table. Address ingestion/log redaction
  separately, with a measurable incident-diagnostics contract.
- `/api/operations/capabilities` also exposes deployment-wide configuration
  status. It requires an explicit product-level decision about which fields
  tenants may see before commercial release.
- The endpoint currently exposes global aggregate availability. Decide
  whether the intended product is a tenant-specific health report or a
  shared-infrastructure status page before adding more details.
- Add isolated PostgreSQL/HTTP acceptance for two tenant admins once these
  product contracts are resolved. Unit regression is not a substitute.
