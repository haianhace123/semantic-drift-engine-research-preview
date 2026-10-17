# Account deactivation and organization data lifecycle

## Current implementation contract

`DELETE /api/auth/me` deactivates **one authenticated identity**. It is not a
request to erase the organization, its monitoring data or downstream knowledge.
A successful request revokes refresh sessions and browser authentication
cookies, removes provider bindings, clears password/recovery/verification
credentials, redacts the identity email and full name and disables login.
The user record remains soft-deleted so referenced audit history is not lost.
A deactivation security audit event is committed in the same transaction as
session revocation and identity deactivation. This transaction must fail in
full if writing the audit event fails.

`GET /api/auth/me/export` currently exports the authenticated identity and
its consent records. It is **not** an organization data export or portable
snapshot / semantic-evidence export. The UI describes that narrow scope.

## Last-administrator protection

An active administrator cannot deactivate while no other active,
non-deleted administrator exists in the same organization. The request
returns HTTP 409 before changing identity or sessions. An organization row
lock serializes competing administrator departures; any transfer or tenant
closure must follow an independently authorized operator workflow.
This policy prevents ownerless API keys but means a single-owner workspace
cannot self-deactivate until a reviewed transfer/closure mechanism exists.
The UI must show this limitation; a rejected request is not a successful
privacy erasure. A privileged operator must not bypass this gate by direct SQL.

## Data not erased by account deactivation

- Organization rows, including billing contact and account ownership metadata.
- Organization-scoped API keys. They have no per-user owner in the current
  schema; revoking all of them on a user action could break other operators.
- Monitored pages, snapshot metadata, raw snapshot files and vectors.
- Semantic drift events, risk evidence, temporal lineage and execution history.
- Consent and security event records and historical session/device metadata.
- Backups, exports and any material already transmitted to configured AI or
  email providers are outside the deletion endpoint.

This separation is deliberate, but it is **not** proof of compliance with any
privacy law or customer contract. The generic Terms/Privacy pages remain
unsuitable for commercial onboarding without an accountable operator and a
reviewed data processing agreement.

## Production work still required

1. Define transfer and closure workflows, ownership consent and authorization
   for tenant erasure requests, including immutable audit obligations.
2. Inventory tenant-linked records and artifact paths with foreign-key and
   lineage constraints. Verify that every file maps to an authorized tenant.
3. Implement a durable erasure workflow with dry-run impact review,
   idempotent execution, resumability, backups and downstream deletion proofs.
4. Establish organization data retention policies, export APIs, external
   provider terms and third-party data transfer controls.
5. Test user/organization isolation, in-flight workers, retries, duplicate
   operations and backup expiration against isolated PostgreSQL and storage.

Never execute direct SQL cascades or remove shared snapshot directories to
simulate tenant erasure. No organization data is modified by this batch.
