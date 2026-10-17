# Legacy personal-workspace owner recovery

Status: operator exception procedure. This is not a self-service role-management feature.

## Purpose and scope

Use `sde_owner_recovery_operator.py` only to restore the initial administrator role of a legacy personal workspace created before personal-workspace creators were assigned `admin`. This is a controlled correction of historical account provisioning, not a mechanism for transferring ownership between users.

The operator tool limits recovery to an active organization containing exactly one active, undeleted, verified OAuth-only user with the legacy `user` role, a matching personal-workspace identity and creation history, no organization API keys, and a recent active SDE session. The recent login and session must each fall within the preceding 15 minutes. The tool runs outside HTTP request handling.

An eligible database record is **not** sufficient proof that the requester owns the account or is entitled to administrative access. The `--operator-attests-ownership` flag records an operator assertion; it does not perform independent identity verification. A valid session shows recent SDE authentication of the account, not authorization to recover organization ownership.

## Authorization gate (required before execute)

1. Open a tracked recovery case and establish the deployment/environment and exact organization UUID. Confirm the request was initiated through an authenticated and approved support channel; do not rely on email address ownership alone.
2. Independently verify the requesting person's control of the intended OAuth subject using an approved fresh authentication flow and the current `/api/auth/me` account/organization identity. Cross-check the organization against historical registration or other trusted ownership evidence. Treat screenshots, email display names and user-provided UUIDs as claims, not authoritative proof. Do not collect browser cookies, refresh tokens, API keys or OAuth credentials in the case record.
3. Obtain explicit approval from an authorized reviewer other than the executing operator for customer production organizations. Record the case ID, approver identity, evidence category, decision, date/time and exact subject/organization in the restricted case system. If independent approval cannot be obtained, do not execute recovery for a customer production organization.
4. Confirm there is no active ownership dispute, security incident, deletion request, or competing claimant. Stop and escalate unresolved cases. Verify recovery/backups and database connection target under the site's normal operational change controls; never use the disposable acceptance database as the live target.
5. Check the running source version and authentication model. The operator and tests must match the deployed schema. Do not apply this runbook to general membership changes or organizations with multiple users.

## Execution controls

The operator must connect only through trusted, audited, least-privileged administrative access. Never copy live database credentials into a shell history, incident ticket, chat or document. Use an approved secret-delivery mechanism and inspect the database target before any write.

First run the operator in default **read-only** mode with the verified `--organization-id` and `--expected-user-id`. Preserve only the minimal preflight result in the restricted case. Refusal means stop; do not bypass the predicate or edit SQL directly to force the state.

After the authorization gate is complete and the read-only preflight is eligible, execute with `--execute`, `--operator-attests-ownership` and the approved `--approval-reference`. Complete the action while the 15-minute session window remains valid. Do not reuse an earlier preflight as proof if account, session or deployment state changes.

Do not run this operator against production merely to test it. Local and integration validation must use disposable infrastructure and synthetic identities.

## Post-operation verification

- Verify that only the intended user's role changed from `user` to `admin`; organization and user identity are otherwise unchanged.
- Confirm exactly one `organization_owner_recovered` audit event for the case, with the expected user, organization and approval reference. A failed transaction must not be treated as successful recovery.
- Confirm through the supported authenticated application path that the recovered account has the expected permissions. Do not issue API keys or other credentials as part of owner recovery.
- Record the outcome and change metadata in the restricted case system, with minimal necessary information. Never record tokens or secrets.
- If a problem emerges, halt further operations and use a separately reviewed remediation procedure. Do not improvise role changes, delete audit events or repeat recovery blindly.

## Refusal and escalation

Stop for absent/stale/revoked sessions, an OAuth identity mismatch, missing provenance, multiple users, existing integration credentials, inactive/deleted identities, unverified ownership, missing independent approval, or any discrepancy between the case and database. Security incidents and contested ownership require a separate process.

## Scope of acceptance evidence

The 2026-10-12 Wave 11 candidate passed 11 predicate unit tests and 7 integration tests in disposable PostgreSQL, including atomic audit/role change, rollback, and stale/invalid session rejection. That proves only those tested invariants. It does not validate the real-world identity attestation process, operational RBAC, secrets management, production database configuration, or recovery from disaster. This runbook is an operational requirement, not proof that those production controls have been installed or followed.
