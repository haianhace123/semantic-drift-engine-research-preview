"""Operator-only recovery for a legacy OAuth personal-workspace owner.

Scope: one attested existing organization/user identity. This is NOT an HTTP
endpoint and must only run under the SDE deployment operator's trusted access.

Default mode is read-only. Execute requires a separate external authorization,
verified user UUID from a current OAuth session, and an approval reference.

The audit fact and role change are committed atomically. No API keys are
created; subsequent API-key issuance must use authenticated SDE endpoints.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import text
from src.core.database import SessionLocal
from src.models.organization import Organization
from src.models.user import User
from src.models.api_key import ApiKey
from src.models.user_session import UserSession
from src.models.auth_security_event import AuthSecurityEvent


AUDIT_TYPE = "organization_owner_recovered"
RECENT_AUTH_WINDOW = timedelta(minutes=15)


class RecoveryRefused(RuntimeError):
    pass


def check_owner(
    org: Organization,
    users: list[User],
    api_keys: list[ApiKey],
    expected_user_id: uuid.UUID,
    sessions: list[UserSession],
    now: datetime,
) -> tuple[User, dict[str, object]]:
    """Fail-closed predicate for the *legacy single-owner OAuth* case only."""
    if len(users) != 1:
        raise RecoveryRefused("expected_exactly_one_user_in_organization")

    user = users[0]
    if user.id != expected_user_id or user.organization_id != org.id:
        raise RecoveryRefused("operator_attested_user_identity_mismatch")
    if org.is_active is not True or user.is_active is not True:
        raise RecoveryRefused("organization_or_user_inactive")
    if user.deleted_at is not None or user.role != "user":
        raise RecoveryRefused("user_not_eligible_for_owner_recovery")
    if not user.email_verified or user.last_login is None:
        raise RecoveryRefused("verified_authenticated_identity_required")
    # Last-login alone is not evidence of a *current* authenticated session.
    # Fail closed on naive/future timestamps instead of accepting stale claims.
    if (
        user.last_login.tzinfo is None
        or user.last_login.utcoffset() is None
        or not now - RECENT_AUTH_WINDOW <= user.last_login <= now
    ):
        raise RecoveryRefused("recent_oauth_login_required")
    valid_session = any(
        session.user_id == user.id
        and session.is_revoked is False
        and session.revoked_at is None
        and isinstance(session.created_at, datetime)
        and session.created_at.tzinfo is not None
        and session.created_at.utcoffset() is not None
        and now - RECENT_AUTH_WINDOW <= session.created_at <= now
        and isinstance(session.expires_at, datetime)
        and session.expires_at.tzinfo is not None
        and session.expires_at.utcoffset() is not None
        and session.expires_at > now
        for session in sessions
    )
    if not valid_session:
        raise RecoveryRefused("recent_active_session_required")
    if not (user.google_id or user.github_id) or user.password_hash:
        raise RecoveryRefused("legacy_oauth_only_scope_required")
    if api_keys:
        raise RecoveryRefused("unexpected_existing_integration_credentials")

    email = user.email.strip().lower()
    base = re.sub(r"[^a-z0-9]+", "-", email.split("@")[0]).strip("-") or "user"
    is_personal_slug = (
        org.slug == base or
        re.fullmatch(re.escape(base) + r"-(?:[2-9]|[1-9][0-9]+)", org.slug) is not None
    )
    if (org.billing_email.strip().lower() != email or
            org.name != f"{base}'s Workspace" or not is_personal_slug):
        raise RecoveryRefused("legacy_personal_workspace_identity_mismatch")

    if not isinstance(org.created_at, datetime) or not isinstance(user.created_at, datetime):
        raise RecoveryRefused("creation_provenance_unavailable")
    creation_delta_s = abs((user.created_at - org.created_at).total_seconds())
    if creation_delta_s > 300:
        raise RecoveryRefused("user_organization_creation_provenance_mismatch")

    return user, {
        "organization_id": str(org.id),
        "user_id": str(user.id),
        "previous_role": user.role,
        "candidate_role": "admin",
        "oauth_provider_linked": True,
        "verified_login_observed": True,
        "recent_active_session_observed": True,
        "personal_workspace_provenance": True,
        "creation_gap_seconds": round(creation_delta_s, 3),
        "existing_api_keys": 0,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-id", required=True, type=uuid.UUID)
    parser.add_argument("--expected-user-id", required=True, type=uuid.UUID,
                        help="Copy from a recent authenticated /api/auth/me browser response")
    parser.add_argument("--execute", action="store_true", help="Perform one atomic recovery")
    parser.add_argument("--approval-reference", default="",
                        help="Externally reviewed operator authorization/case ID")
    parser.add_argument("--operator-attests-ownership", action="store_true",
                        help="Operator verified external ownership and current OAuth session")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.execute:
        if not args.operator_attests_ownership:
            raise RecoveryRefused("explicit_operator_ownership_attestation_required")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{5,119}",
                            args.approval_reference):
            raise RecoveryRefused("external_approval_reference_required")

    with SessionLocal() as db:
        if args.execute:
            db.execute(text("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE"))
        else:
            db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))

        query = db.query(Organization).filter(
            Organization.id == args.organization_id
        )
        if args.execute:
            query = query.with_for_update()
        org = query.one_or_none()
        if org is None:
            raise RecoveryRefused("organization_not_found")

        query = db.query(User).filter(User.organization_id == org.id).order_by(User.id)
        if args.execute:
            query = query.with_for_update()
        users = query.all()
        api_keys = db.query(ApiKey).filter(ApiKey.organization_id == org.id).all()
        now = datetime.now(timezone.utc)
        session_query = db.query(UserSession).filter(
            UserSession.user_id == args.expected_user_id,
            UserSession.created_at >= now - RECENT_AUTH_WINDOW,
            UserSession.created_at <= now,
            UserSession.expires_at > now,
            UserSession.is_revoked.is_(False),
            UserSession.revoked_at.is_(None),
        )
        if args.execute:
            session_query = session_query.with_for_update()
        sessions = session_query.all()
        user, preflight = check_owner(
            org, users, api_keys, args.expected_user_id, sessions, now
        )

        if not args.execute:
            print(json.dumps({"mode": "read_only_preflight", "eligible": True,
                              "preflight": preflight, "database_writes": False}, indent=2))
            return 0

        # Deliberately use a dedicated immutable operator audit fact. The
        # installed schema must accept this type or the full transaction fails.
        user.role = "admin"
        db.add(AuthSecurityEvent(
            organization_id=org.id,
            user_id=user.id,
            event_type=AUDIT_TYPE,
            metadata_json={
                "approval_reference": args.approval_reference,
                "authority": "deployment_operator_attested_oauth_owner",
                "previous_role": "user",
                "new_role": "admin",
                "procedure": "legacy_oauth_personal_workspace_owner_recovery_v1",
            },
        ))
        db.flush()
        db.commit()
        print(json.dumps({"mode": "execute", "result": "recovered_and_audited",
                          "organization_id": str(org.id), "user_id": str(user.id),
                          "previous_role": "user", "new_role": "admin",
                          "audit_event_type": AUDIT_TYPE}, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RecoveryRefused as exc:
        print(json.dumps({"result": "refused", "reason": str(exc)}), file=sys.stderr)
        sys.exit(2)
    except Exception:
        # Do not emit database URLs, connection metadata, or personal data.
        print(json.dumps({"result": "failed", "reason": "transaction_or_runtime_error"}),
              file=sys.stderr)
        sys.exit(1)
