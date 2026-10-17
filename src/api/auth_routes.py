"""
Production authentication API.

Features:
- Email/password signup + login
- Email verification
- Password reset
- Rotating refresh tokens
- HttpOnly authentication cookies
- Double-submit CSRF
- Google OAuth
- GitHub OAuth
- OAuth account linking
- Soft account deletion
- GDPR-style export
- Consent logging
- Rate limiting
- API key management
"""

import logging
import re
import secrets
import uuid

from datetime import timedelta
from urllib.parse import urlencode

import httpx

from oauthlib.oauth2 import (
    WebApplicationClient
)

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Response
)

from fastapi.responses import (
    RedirectResponse
)

from pydantic import BaseModel

from sqlalchemy.orm import Session

from src.core.config import settings

from src.core.database import get_db

from src.core.security import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    OAUTH_LINK_COOKIE,
    OAUTH_STATE_COOKIE,
    REFRESH_COOKIE,
    AuthError,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
    hash_password,
    now_utc,
    require_roles,
    token_hash,
    verify_password
)

from src.models import (
    ApiKey,
    ConsentLog,
    Organization,
    User,
    UserSession
)

from src.services.email_service import (
    email_verification_code_template,
    email_verification_template,
    enqueue_email,
    reset_password_template,
    welcome_template
)

from src.services.auth_session_service import (
    create_session,
    get_session_by_refresh_token,
    get_user_session,
    list_active_user_sessions,
    revoke_all_user_sessions,
    revoke_other_user_sessions,
    revoke_session,
    rotate_session,
    session_is_expired,
)

from src.services.auth_capability_service import (
    get_auth_capabilities,
)

from src.services.auth_audit_service import (
    record_auth_security_event,
)
from src.services.organization_data_inventory_service import (
    OrganizationInventoryUnavailable,
    build_consistent_organization_data_inventory,
)
from sqlalchemy.exc import SQLAlchemyError

router = APIRouter(
    prefix="/api/auth",
    tags=["auth"]
)

logger = logging.getLogger(__name__)


EMAIL_RE = re.compile(
    r"^[^\s@]+@[^\s@]+\.[^\s@]+$"
)


def generate_verification_code() -> str:
    """Generate a cryptographically secure six-digit verification code."""
    return f"{secrets.randbelow(1_000_000):06d}"


ERROR_MESSAGES = {
    "AUTH_001":
        "Invalid email or password",

    "AUTH_002":
        "Email address has not been verified",

    "AUTH_003":
        "Account is locked",

    "AUTH_004":
        "Invalid token",

    "AUTH_005":
        "Token has expired",

    "AUTH_006":
        "Email address is already registered",

    "AUTH_007":
        "Password is too weak",

    "AUTH_008":
        "Invalid email address",

    "AUTH_009":
        "Reset token is invalid or has expired",

    "AUTH_010":
        "Verification token is invalid or has expired",

    "AUTH_011":
        "OAuth provider error",

    "AUTH_012":
        "Email address was not found",

    "AUTH_013":
        "Current password is incorrect",

    "AUTH_014":
        "New password must be different from the current password",

    "AUTH_015":
        "Invalid profile data",

    "AUTH_016":
        "Session was not found",

    "AUTH_017":
        "Verification code was requested too recently",

    "AUTH_018":
        "Too many verification attempts",

    "AUTH_019":
        "Verification email could not be queued",

    "AUTH_020":
        "Too many requests. Please try again later.",

    "AUTH_021":
        "Password recovery is temporarily unavailable.",

    "AUTH_022":
        "Authentication rate limiting is temporarily unavailable.",
}


# ============================================================
# REQUEST MODELS
# ============================================================

class AuthBody(BaseModel):
    email: str = ""
    password: str = ""


class EmailBody(BaseModel):
    email: str = ""


class SignupBody(BaseModel):
    email: str = ""
    password: str = ""
    confirm_password: str = ""
    full_name: str = ""
    terms_accepted: bool = False


class ProfileBody(BaseModel):
    full_name: str = ""


class PasswordResetBody(BaseModel):
    token: str = ""
    password: str = ""
    confirm_password: str = ""

class ChangePasswordBody(BaseModel):
    current_password: str = ""
    new_password: str = ""
    confirm_password: str = ""


class RevokeSessionsBody(BaseModel):
    keep_current: bool = False

class OAuthConfirmBody(BaseModel):
    confirm: bool = False


class ApiKeyPermissions(BaseModel):
    read: bool = True
    write: bool = False


class CreateApiKeyRequest(BaseModel):
    name: str
    permissions: ApiKeyPermissions = (
        ApiKeyPermissions()
    )


# ============================================================
# ERROR HELPERS
# ============================================================

def auth_error(
    code: str,
    status: int = 400,
    details=None
) -> HTTPException:

    return HTTPException(
        status_code=status,
        detail={
            "error": {
                "code":
                    code,

                "message":
                    ERROR_MESSAGES.get(
                        code,
                        code
                    ),

                "details":
                    details
            }
        }
    )


# ============================================================
# VALIDATION
# ============================================================

def validate_email(
    email: str
) -> str:

    value = email.strip().lower()

    if not EMAIL_RE.fullmatch(
        value
    ):
        raise auth_error(
            "AUTH_008",
            422
        )

    return value


def validate_password(
    password: str
) -> None:

    if len(password) < 12:
        raise auth_error(
            "AUTH_007",
            422
        )

    if not re.search(
        r"[A-Z]",
        password
    ):
        raise auth_error(
            "AUTH_007",
            422
        )

    if not re.search(
        r"[a-z]",
        password
    ):
        raise auth_error(
            "AUTH_007",
            422
        )

    if not re.search(
        r"\d",
        password
    ):
        raise auth_error(
            "AUTH_007",
            422
        )

    if not re.search(
        r"[^A-Za-z0-9]",
        password
    ):
        raise auth_error(
            "AUTH_007",
            422
        )


def new_opaque_token() -> str:
    return secrets.token_urlsafe(
        48
    )


# ============================================================
# COOKIE HELPERS
# ============================================================

def ensure_csrf_cookie(
    response: Response
) -> str:

    token = new_opaque_token()

    response.set_cookie(
        CSRF_COOKIE,
        token,
        max_age=86400,
        httponly=False,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        domain=(
            settings.COOKIE_DOMAIN
            or None
        ),
        path="/"
    )

    return token


def _request_ip(
    request: Request
) -> str | None:

    return (
        request.client.host
        if request.client
        else None
    )


def _set_auth_cookie_values(
    response: Response,
    *,
    access: str,
    refresh: str,
    remember_me: bool = False,
) -> None:

    response.set_cookie(
        ACCESS_COOKIE,
        access,
        max_age=(
            settings.ACCESS_TOKEN_EXPIRE_MINUTES *
            60
        ),
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        domain=(
            settings.COOKIE_DOMAIN
            or None
        ),
        path="/"
    )

    refresh_cookie_kwargs = {
        "httponly": True,
        "secure": settings.COOKIE_SECURE,
        "samesite": settings.COOKIE_SAMESITE,
        "domain": (
            settings.COOKIE_DOMAIN
            or None
        ),
        "path": "/api/auth",
    }

    if remember_me:
        refresh_cookie_kwargs["max_age"] = (
            settings.REFRESH_TOKEN_EXPIRE_DAYS *
            86400
        )

    response.set_cookie(
        REFRESH_COOKIE,
        refresh,
        **refresh_cookie_kwargs,
    )

    ensure_csrf_cookie(
        response
    )


def require_active_session_issuer(
    user: User,
    db: Session,
) -> None:
    """Do not issue or rotate credentials for a suspended identity or tenant."""
    if (
        not user.is_active
        or user.deleted_at is not None
    ):
        raise auth_error("AUTH_004", 401)

    active_org = (
        db.query(Organization.id)
        .filter(
            Organization.id == user.organization_id,
            Organization.is_active.is_(True),
        )
        .first()
    )
    if active_org is None:
        raise auth_error("AUTH_004", 401)


def set_auth_cookies(
    response: Response,
    user: User,
    request: Request,
    db: Session,
    *,
    remember_me: bool = False,
) -> None:
    """Create a new login session and set auth cookies."""

    require_active_session_issuer(user, db)

    refresh, refresh_expires = (
        create_refresh_token(
            str(user.id),
            remember_me=remember_me,
        )
    )

    session = create_session(
        db,
        user_id=user.id,
        refresh_token=refresh,
        expires_at=refresh_expires,
        ip_address=_request_ip(
            request
        ),
        user_agent=request.headers.get(
            "user-agent"
        ),
    )

    access = create_access_token(
        str(user.id),
        session_id=session.id,
    )

    # Legacy single-session fields are intentionally no longer used.
    user.refresh_token = None
    user.refresh_token_expires = None

    _set_auth_cookie_values(
        response,
        access=access,
        refresh=refresh,
        remember_me=remember_me,
    )


def rotate_auth_cookies(
    response: Response,
    user: User,
    request: Request,
    db: Session,
    session,
    *,
    remember_me: bool = False,
) -> None:
    """Rotate one refresh-token session."""

    require_active_session_issuer(user, db)

    refresh, refresh_expires = (
        create_refresh_token(
            str(user.id),
            remember_me=remember_me,
        )
    )

    rotate_session(
        db,
        session=session,
        new_refresh_token=refresh,
        new_expires_at=refresh_expires,
        ip_address=_request_ip(
            request
        ),
        user_agent=request.headers.get(
            "user-agent"
        ),
    )

    access = create_access_token(
        str(user.id),
        session_id=session.id,
    )

    _set_auth_cookie_values(
        response,
        access=access,
        refresh=refresh,
        remember_me=remember_me,
    )


def clear_auth_cookies(
    response: Response
) -> None:

    response.delete_cookie(
        ACCESS_COOKIE,
        path="/",
        domain=(
            settings.COOKIE_DOMAIN
            or None
        )
    )

    response.delete_cookie(
        REFRESH_COOKIE,
        path="/api/auth",
        domain=(
            settings.COOKIE_DOMAIN
            or None
        )
    )

    response.delete_cookie(
        CSRF_COOKIE,
        path="/",
        domain=(
            settings.COOKIE_DOMAIN
            or None
        )
    )

    response.delete_cookie(
        OAUTH_STATE_COOKIE,
        path="/api/auth",
        domain=(
            settings.COOKIE_DOMAIN
            or None
        )
    )

    response.delete_cookie(
        OAUTH_LINK_COOKIE,
        path="/api/auth",
        domain=(
            settings.COOKIE_DOMAIN
            or None
        )
    )


# ============================================================
# CSRF
# ============================================================

def require_csrf(
    request: Request
) -> None:

    csrf_cookie = request.cookies.get(
        CSRF_COOKIE
    )

    csrf_header = request.headers.get(
        "X-CSRF-Token"
    )

    if (
        not csrf_cookie
        or not csrf_header
        or not secrets.compare_digest(
            csrf_cookie,
            csrf_header
        )
    ):
        raise auth_error(
            "AUTH_004",
            403,
            {
                "reason":
                    "CSRF validation failed"
            }
        )


# ============================================================
# RATE LIMITING
# ============================================================

def client_ip(
    request: Request
) -> str:
    # ASGI is the authority for trusted proxy metadata; route code must not
    # accept attacker-controlled forwarded headers directly.
    if request.client and request.client.host:
        return request.client.host

    # Missing peers share one bounded bucket; never bypass the limiter.
    return "unknown"


# One Redis command owns increment, expiry, and TTL. An existing key with
# missing TTL is repaired in the same atomic operation rather than persisting
# indefinitely after an interrupted legacy INCR/EXPIRE sequence.
AUTH_RATE_LIMIT_LUA = """
local count = redis.call('INCR', KEYS[1])
local ttl = redis.call('TTL', KEYS[1])
if count == 1 or ttl < 0 then
    redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
    ttl = redis.call('TTL', KEYS[1])
end
return { count, ttl }
"""


def redis_rate_limit(
    key: str,
    limit: int,
    window_seconds: int,
):
    if limit < 1 or window_seconds < 1:
        raise ValueError("Rate limit and window must be positive")

    import redis

    client = redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=3,
    )

    try:
        current, ttl = client.eval(
            AUTH_RATE_LIMIT_LUA,
            1,
            key,
            window_seconds,
        )
    finally:
        client.close()

    return (
        int(current) <= limit,
        max(int(ttl), 0),
    )


def ensure_rate_limit(
    request: Request,
    bucket: str,
    limit: int,
    window_seconds: int,
    *,
    use_ip: bool = True,
    error_code: str = "AUTH_020",
) -> None:

    try:

        suffix = (
            f":{client_ip(request)}"
            if use_ip
            else ""
        )

        key = (
            f"sde:rl:{bucket}{suffix}"
        )

        allowed, ttl = (
            redis_rate_limit(
                key,
                limit,
                window_seconds
            )
        )

        if not allowed:
            raise HTTPException(
                status_code=429,
                detail={
                    "error": {
                        "code":
                            error_code,

                        "message":
                            ERROR_MESSAGES.get(
                                error_code,
                                ERROR_MESSAGES["AUTH_020"],
    ),

                        "details": {
                            "retry_after":
                                ttl
                        }
                    }
                },
                headers={
                    "Retry-After":
                        str(ttl)
                }
            )

    except HTTPException:
        raise

    except Exception:
        # Authentication endpoints must not silently lose brute-force
        # protection during a Redis outage. This is an availability tradeoff:
        # return a controlled 503, without revealing internal details.
        logger.error("Authentication rate limiter unavailable")
        raise auth_error("AUTH_022", 503)


# ============================================================
# USER SERIALIZATION
# ============================================================

def user_public(
    user: User
) -> dict:

    return {
        "id":
            str(user.id),

        "email":
            user.email,

        "full_name":
            user.full_name,

        "role":
            user.role,

        "organization_id":
            str(user.organization_id),

        "email_verified":
            bool(user.email_verified),

        # Authenticated account UI must not offer a password-change
        # operation to an OAuth-only identity with no stored password.
        "password_login_enabled":
            bool(user.password_hash),

        "google_connected":
            bool(user.google_id),

        "github_connected":
            bool(user.github_id),

        "last_login":
            (
                user.last_login.isoformat()
                if user.last_login
                else None
            )
    }


# ============================================================
# ORGANIZATION
# ============================================================

def create_personal_org(
    db: Session,
    email: str
) -> Organization:

    slug_base = re.sub(
        r"[^a-z0-9]+",
        "-",
        email
        .split("@")[0]
        .lower()
    ).strip("-")

    if not slug_base:
        slug_base = "user"

    slug = slug_base
    counter = 1

    while db.query(
        Organization
    ).filter(
        Organization.slug ==
        slug
    ).first():

        counter += 1

        slug = (
            f"{slug_base}-{counter}"
        )

    organization = Organization(
        name=(
            f"{slug_base}'s Workspace"
        ),
        slug=slug,
        billing_email=email,
        subscription_tier="free"
    )

    db.add(
        organization
    )

    db.flush()

    return organization


# ============================================================
# OAUTH LINK
# ============================================================

def issue_oauth_link_cookie(
    response: Response,
    payload: dict
) -> None:

    import json
    import redis

    nonce = secrets.token_urlsafe(
        32
    )

    client = redis.from_url(
        settings.REDIS_URL,
        decode_responses=True
    )

    client.setex(
        f"sde:oauth-link:{nonce}",
        600,
        json.dumps(payload)
    )

    response.set_cookie(
        OAUTH_LINK_COOKIE,
        nonce,
        max_age=600,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite=settings.COOKIE_SAMESITE,
        domain=(
            settings.COOKIE_DOMAIN
            or None
        ),
        path="/api/auth"
    )


# ============================================================
# CSRF COOKIE
# ============================================================

@router.get("/csrf")
def csrf(
    request: Request,
    response: Response
):

    token = request.cookies.get(
        CSRF_COOKIE
    )

    if not token:
        token = ensure_csrf_cookie(
            response
        )

    return {
        "ok":
            True,

        "csrf_token":
            token
    }


# ============================================================
# SIGNUP
# ============================================================

@router.post("/signup")
def signup(
    request: Request,
    response: Response,
    body: SignupBody,
    db: Session = Depends(get_db)
):

    require_csrf(
        request
    )

    ensure_rate_limit(
        request,
        "signup",
        10,
        3600
    )

    email = validate_email(
        body.email
    )

    validate_password(
        body.password
    )

    if (
        body.password !=
        body.confirm_password
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code":
                        "AUTH_007",

                    "message":
                        "Password confirmation does not match",

                    "details":
                        None
                }
            }
        )

    if not body.terms_accepted:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code":
                        "AUTH_015",

                    "message":
                        "You must agree to the Terms of Service and Privacy Policy",

                    "details":
                        None
                }
            }
        )

    existing = db.query(
        User
    ).filter(
        User.email == email
    ).first()

    if (
        existing
        and existing.deleted_at
        is None
    ):
        raise auth_error(
            "AUTH_006",
            409
        )

    if (
        existing
        and existing.deleted_at
        is not None
    ):

        existing.deleted_at = None
        existing.is_active = True
        existing.password_hash = hash_password(body.password)
        existing.full_name = body.full_name.strip()
        existing.email_verified = False
        existing.google_id = None
        existing.github_id = None
        existing.login_attempts = 0
        existing.locked_until = None
        existing.refresh_token = None
        existing.refresh_token_expires = None

        user = existing

    else:

        organization = (
            create_personal_org(
                db,
                email
            )
        )

        user = User(
            organization_id=
                organization.id,

            email=
                email,

            password_hash=
                hash_password(
                    body.password
                ),

            full_name=
                body.full_name.strip(),

            role=
                "admin",

            is_active=
                True,

            email_verified=
                False
        )

        db.add(
            user
        )

        db.flush()

    verification_code = generate_verification_code()

    # Store only the SHA-256 digest of the verification code.
    user.verification_code_hash = token_hash(
        verification_code
    )
    user.verification_code_expires = (
        now_utc() + timedelta(minutes=15)
    )

    # Keep the legacy link token in place for backward compatibility with
    # previously issued verification emails. New signups use the 6-digit code.
    verification_raw = new_opaque_token()
    user.verification_token = token_hash(verification_raw)
    user.verification_token_expires = now_utc() + timedelta(hours=24)

    consent = ConsentLog(
        user_id=
            user.id,

        consent_type=
            "terms",

        granted=
            True,

        policy_version=
            settings.TERMS_VERSION,

        ip_address=
            client_ip(request),

        user_agent=
            request.headers.get(
                "user-agent"
            )
    )

    db.add(
        consent
    )

    db.commit()

    verify_url = (
        f"{settings.APP_BASE_URL.rstrip('/')}"
        f"/api/auth/verify-email"
        f"?token={verification_raw}"
    )

    # Signup is already committed. On queue failure, report that the
    # account exists and can use resend-verification; never claim a
    # verification message was queued when it was not.
    try:
        enqueue_email(
            to=user.email,
            subject="Your Temporal Intelligence verification code",
            html=email_verification_code_template(
                user.full_name,
                verification_code
            )
        )
    except Exception as exc:
        logger.warning("Signup verification enqueue failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "AUTH_019",
                    "message": (
                        "Account created, but verification email could not "
                        "be queued. Use Resend code to try again."
                    ),
                    "details": {"account_created": True},
                }
            },
        )

    # Welcome email is optional; it must not mask successful
    # verification-code admission or fail account creation.
    try:
        enqueue_email(
            to=user.email,
            subject="Welcome to Temporal Intelligence",
            html=welcome_template(user.full_name),
        )
    except Exception as exc:
        logger.warning("Signup welcome email enqueue failed: %s", type(exc).__name__)

    ensure_csrf_cookie(
        response
    )

    return {
        "message":
            "Account created. Verification email queued; check your inbox.",

        "email":
            user.email
    }


# ============================================================
# LOGIN
# ============================================================

class LoginBody(AuthBody):
    remember_me: bool = False


@router.post("/login")
def login(
    request: Request,
    response: Response,
    body: LoginBody,
    db: Session = Depends(get_db)
):

    require_csrf(
        request
    )

    # Global endpoint protection by client IP.
    ensure_rate_limit(
        request,
        "login",
        30,
        3600
    )

    email = validate_email(
        body.email
    )

    user = db.query(
        User
    ).filter(
        User.email == email,
        User.deleted_at.is_(None)
    ).first()

    # Do not create an audit row for an unknown email.
    # This avoids storing arbitrary attacker-supplied
    # email addresses in the security audit table.
    if not user:
        raise auth_error(
            "AUTH_001",
            401
        )

    # Existing account lock remains authoritative.
    if (
        user.locked_until
        and user.locked_until >
        now_utc()
    ):
        raise auth_error(
            "AUTH_003",
            423
        )

    if (
        not user.password_hash
        or not verify_password(
            body.password,
            user.password_hash
        )
    ):
        # Defense-in-depth limiter for failed
        # authentication against this account.
        ensure_rate_limit(
            request,
            (
                "login-failed:"
                f"{user.id}"
            ),
            5,
            900,
            use_ip=False,
        )

        user.login_attempts = (
            user.login_attempts or 0
        ) + 1

        # Record the failed attempt in the same
        # transaction as login_attempts.
        record_auth_security_event(
            db,
            event_type="login_failed",
            organization_id=(
                user.organization_id
            ),
            user_id=user.id,
            request=request,
            metadata={
                "reason":
                    "invalid_credentials",

                "attempt":
                    user.login_attempts,
            },
        )

        if (
            user.login_attempts >= 5
        ):
            user.locked_until = (
                now_utc()
                + timedelta(
                    minutes=15
                )
            )

            # The counter is reset because the
            # account lock now becomes the source
            # of truth for login protection.
            user.login_attempts = 0

            record_auth_security_event(
                db,
                event_type="account_locked",
                organization_id=(
                    user.organization_id
                ),
                user_id=user.id,
                request=request,
                metadata={
                    "reason":
                        "failed_login_threshold",

                    "lock_minutes":
                        15,
                },
            )

            db.commit()

            raise auth_error(
                "AUTH_003",
                423
            )

        db.commit()

        raise auth_error(
            "AUTH_001",
            401
        )

    if not user.email_verified:
        raise auth_error(
            "AUTH_002",
            403
        )

    user.login_attempts = 0
    user.locked_until = None
    user.last_login = now_utc()

    set_auth_cookies(
        response,
        user,
        request,
        db,
        remember_me=body.remember_me,
    )

    # Login success and persistent session creation
    # are committed together.
    record_auth_security_event(
        db,
        event_type="login_success",
        organization_id=(
            user.organization_id
        ),
        user_id=user.id,
        request=request,
        metadata={
            "method":
                "password",
        },
    )

    db.commit()

    return {
        "user":
            user_public(user)
    }


# ============================================================
# REFRESH
# ============================================================

@router.post("/refresh")
def refresh(
    request: Request,
    response: Response,
    db: Session = Depends(get_db)
):

    require_csrf(
        request
    )

    raw = request.cookies.get(
        REFRESH_COOKIE
    )

    if not raw:
        raise auth_error(
            "AUTH_004",
            401
        )

    try:
        payload = decode_token(
            raw,
            "refresh"
        )

    except AuthError as exc:
        clear_auth_cookies(
            response
        )

        raise auth_error(
            exc.code,
            exc.status_code
        ) from exc

    session = get_session_by_refresh_token(
        db,
        raw,
    )

    if not session:
        clear_auth_cookies(
            response
        )

        raise auth_error(
            "AUTH_004",
            401
        )

    if session_is_expired(
        session
    ):
        revoke_session(
            db,
            session
        )

        db.commit()

        clear_auth_cookies(
            response
        )

        raise auth_error(
            "AUTH_005",
            401
        )

    if (
        str(session.user_id)
        != str(payload["sub"])
    ):
        revoke_session(
            db,
            session
        )

        db.commit()

        clear_auth_cookies(
            response
        )

        raise auth_error(
            "AUTH_004",
            401
        )

    user = db.query(
        User
    ).filter(
        User.id == session.user_id,
        User.deleted_at.is_(None)
    ).first()

    if (
        not user
        or not user.is_active
    ):
        revoke_session(
            db,
            session
        )

        db.commit()

        clear_auth_cookies(
            response
        )

        raise auth_error(
            "AUTH_004",
            401
        )

    rotate_auth_cookies(
        response,
        user,
        request,
        db,
        session,
        remember_me=bool(
            payload.get(
                "remember_me",
                False,
            )
        ),
    )

    db.commit()

    return {
        "user":
            user_public(user)
    }


# ============================================================
# LOGOUT
# ============================================================

@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db)
):

    require_csrf(
        request
    )

    raw = request.cookies.get(
        REFRESH_COOKIE
    )

    if raw:
        session = get_session_by_refresh_token(
            db,
            raw,
        )

        if session:
            revoke_session(
                db,
                session
            )

            db.commit()

    clear_auth_cookies(
        response
    )

    return {
        "message":
            "Signed out successfully"
    }


# ============================================================
# VERIFY EMAIL CODE
# ============================================================

class VerifyEmailCodeBody(BaseModel):
    email: str = ""
    code: str = ""

@router.post("/verify-otp")
@router.post("/verify-email-code")
def verify_email_code(
    request: Request,
    body: VerifyEmailCodeBody,
    db: Session = Depends(get_db)
):
    """Verify a newly registered account with the emailed six-digit code."""

    require_csrf(
        request
    )

    email = validate_email(
        body.email
    )

    code = body.code.strip()

    ensure_rate_limit(
        request,
        f"verify-otp:{email}",
        5,
        900,
        use_ip=False,
        error_code="AUTH_018",
    )

    if not re.fullmatch(
        r"\d{6}",
        code
    ):
        raise auth_error(
            "AUTH_010",
            422
        )

    user = db.query(
        User
    ).filter(
        User.email == email,
        User.deleted_at.is_(None)
    ).first()

    if (
        not user
        or user.email_verified
    ):
        raise auth_error(
            "AUTH_010",
            400
        )

    if (
        not user.verification_code_hash
        or not user.verification_code_expires
        or user.verification_code_expires
        <= now_utc()
        or not secrets.compare_digest(
            user.verification_code_hash,
            token_hash(
                code
            )
        )
    ):
        raise auth_error(
            "AUTH_010",
            400
        )

    user.email_verified = True

    user.verification_code_hash = None
    user.verification_code_expires = None

    user.verification_token = None
    user.verification_token_expires = None

    record_auth_security_event(
        db,
        event_type="email_verified",
        organization_id=(
            user.organization_id
        ),
        user_id=user.id,
        request=request,
        metadata={
            "method":
                "otp",
        },
    )

    db.commit()

    return {
        "message":
            "Email verified successfully."
    }

# ============================================================
# RESEND EMAIL VERIFICATION
# ============================================================

@router.post("/resend-verification")
def resend_verification(
    request: Request,
    body: EmailBody,
    db: Session = Depends(get_db),
):
    """
    Reissue a six-digit verification code.

    Response intentionally does not reveal whether the account exists
    or is already verified.
    """

    require_csrf(
        request
    )

    email = validate_email(
        body.email
    )

    ensure_rate_limit(
        request,
        "resend-verification",
        10,
        3600,
    )

    ensure_rate_limit(
        request,
        (
            "resend-verification-cooldown:"
            f"{email}"
        ),
        1,
        60,
        use_ip=False,
        error_code="AUTH_017",
    )

    ensure_rate_limit(
        request,
        f"resend-verification:{email}",
        3,
        3600,
        use_ip=False,
    )

    user = (
        db.query(User)
        .filter(
            User.email == email,
            User.deleted_at.is_(None),
        )
        .first()
    )

    generic_message = (
        "If the account requires verification, we will attempt to send "
        "a new code. If none arrives, please try again later."
    )

    if (
        not user
        or user.email_verified
        or not user.is_active
    ):
        return {
            "message":
                generic_message
        }

    verification_code = (
        generate_verification_code()
    )

    user.verification_code_hash = (
        token_hash(
            verification_code
        )
    )

    user.verification_code_expires = (
        now_utc()
        + timedelta(
            minutes=15
        )
    )

    # Preserve link-verification compatibility
    # while issuing a completely new token.
    verification_raw = (
        new_opaque_token()
    )

    user.verification_token = (
        token_hash(
            verification_raw
        )
    )

    user.verification_token_expires = (
        now_utc()
        + timedelta(
            hours=24
        )
    )

    try:
        enqueue_email(
            to=user.email,
            subject=(
                "Your Temporal Intelligence "
                "verification code"
            ),
            html=(
                email_verification_code_template(
                    user.full_name,
                    verification_code,
                )
            ),
        )

    except Exception as exc:
        db.rollback()
        # Returning an error only for eligible accounts would disclose
        # account existence when the email queue is unavailable.
        logger.warning("Resend verification enqueue failed: %s", type(exc).__name__)
        return {"message": generic_message}

    record_auth_security_event(
        db,
        event_type="verification_resent",
        organization_id=(
            user.organization_id
        ),
        user_id=user.id,
        request=request,
        metadata={
            "method":
                "email",
        },
    )

    db.commit()

    return {
        "message":
            generic_message,
    }

# ============================================================
# VERIFY EMAIL
# ============================================================

@router.get("/verify-email/{token}")
@router.get("/verify-email")
def verify_email(
    request: Request,
    token: str,
    db: Session = Depends(get_db),
):

    digest = token_hash(
        token
    )

    user = db.query(
        User
    ).filter(
        User.verification_token ==
        digest
    ).first()

    if (
        not user
        or not user.verification_token_expires
        or user.verification_token_expires <=
        now_utc()
    ):

        raise auth_error(
            "AUTH_010",
            400
        )

    user.email_verified = True

    user.verification_token = None
    user.verification_token_expires = None

    user.verification_code_hash = None
    user.verification_code_expires = None

    record_auth_security_event(
        db,
        event_type="email_verified",
        organization_id=(
            user.organization_id
        ),
        user_id=user.id,
        request=request,
        metadata={
            "method":
                "link",
        },
    )

    db.commit()

    return RedirectResponse(
        url="/login.html?verified=1",
        status_code=303
    )


# ============================================================
# PUBLIC AUTH CAPABILITIES
# ============================================================

@router.get("/capabilities")
def auth_capabilities(
    db: Session = Depends(get_db),
):
    # Capability state is global deployment state, not account information.
    try:
        return get_auth_capabilities(db, settings=settings)
    except Exception:
        logger.error("Auth capability read unavailable")
        return {
            "email_recovery": False,
            "google": False,
            "github": False,
        }


# ============================================================
# FORGOT PASSWORD
# ============================================================

@router.post("/forgot-password")
def forgot_password(
    request: Request,
    body: EmailBody,
    db: Session = Depends(get_db)
):

    require_csrf(
        request
    )

    # Do not create reset tokens or queue undeliverable messages when
    # the email worker is absent or the provider is unconfigured.
    if not get_auth_capabilities(db, settings=settings)["email_recovery"]:
        raise auth_error("AUTH_021", 503)

    normalized = validate_email(
        body.email
    )

    ensure_rate_limit(
        request,
        f"reset:{normalized}",
        3,
        3600,
        use_ip=False
    )

    user = db.query(
        User
    ).filter(
        User.email == normalized,
        User.deleted_at.is_(None)
    ).first()

    # Do not reveal account existence.
    if user:

        raw = new_opaque_token()

        user.reset_token = (
            token_hash(
                raw
            )
        )

        user.reset_token_expires = (
            now_utc() +
            timedelta(
                hours=1
            )
        )

        reset_url = (
            f"{settings.APP_BASE_URL.rstrip('/')}"
            f"/reset-password.html"
            f"?token={raw}"
        )

        try:

            enqueue_email(
                to=user.email,
                subject=
                    "Reset your Temporal Intelligence password",
                html=
                    reset_password_template(
                        user.full_name,
                        reset_url
                    )
            )

        except Exception as exc:
            # A failed enqueue must not replace a still-usable reset
            # token with one the user never received.
            db.rollback()
            logger.warning("Password reset email enqueue failed: %s", type(exc).__name__)
        else:
            db.commit()

    return {
        "message": (
            "If an account exists, password reset instructions may arrive. "
            "If no email arrives, please try again later."
        )
    }


# ============================================================
# RESET PASSWORD
# ============================================================

@router.post("/reset-password")
def reset_password(
    request: Request,
    body: PasswordResetBody,
    db: Session = Depends(get_db)
):

    require_csrf(
        request
    )

    validate_password(
        body.password
    )

    if (
        body.password !=
        body.confirm_password
    ):

        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code":
                        "AUTH_007",

                    "message":
                        "Password confirmation does not match",

                    "details":
                        None
                }
            }
        )

    digest = token_hash(
        body.token
    )

    user = db.query(
        User
    ).filter(
        User.reset_token ==
        digest,

        User.deleted_at.is_(None)
    ).first()

    if (
        not user
        or not user.reset_token_expires
        or user.reset_token_expires <=
        now_utc()
    ):

        raise auth_error(
            "AUTH_009",
            400
        )

    user.password_hash = (
        hash_password(
            body.password
        )
    )

    user.reset_token = None
    user.reset_token_expires = None

    # Force every device/session to re-authenticate.
    revoke_all_user_sessions(
        db,
        user.id,
    )

    # Keep legacy columns cleared during the migration period.
    user.refresh_token = None
    user.refresh_token_expires = None

    user.login_attempts = 0
    user.locked_until = None

    db.commit()

    return {
        "message":
            "Password updated. You can sign in again."
    }

# ============================================================
# SESSION MANAGEMENT
# ============================================================

def session_public(
    session: UserSession,
    *,
    current_session_id=None,
) -> dict:
    """
    Serialize an authentication session without exposing
    refresh-token hashes or other authentication secrets.
    """

    return {
        "id":
            str(session.id),

        "created_at":
            (
                session.created_at.isoformat()
                if session.created_at
                else None
            ),

        "last_used_at":
            (
                session.last_used_at.isoformat()
                if session.last_used_at
                else None
            ),

        "expires_at":
            (
                session.expires_at.isoformat()
                if session.expires_at
                else None
            ),

        "ip_address":
            session.ip_address,

        "user_agent":
            session.user_agent,

        "is_current":
            (
                current_session_id is not None
                and str(session.id)
                == str(current_session_id)
            ),
    }


def current_persistent_session(
    request: Request,
    db: Session,
    user: User,
):
    """
    Resolve the current persistent session from the refresh
    cookie.

    Access-token authentication alone is not enough to identify
    which device/session is currently being used, therefore the
    refresh cookie is used only for session identification.

    Failure to resolve the current session is intentionally
    non-fatal for GET /sessions.
    """

    raw_refresh_token = request.cookies.get(
        REFRESH_COOKIE
    )

    if not raw_refresh_token:
        return None

    session = get_session_by_refresh_token(
        db,
        raw_refresh_token,
    )

    if not session:
        return None

    if (
        str(session.user_id)
        != str(user.id)
    ):
        return None

    if session_is_expired(
        session
    ):
        return None

    return session


@router.get("/sessions")
def list_sessions(
    request: Request,
    user: User =
        Depends(get_current_user),
    db: Session =
        Depends(get_db)
):
    """
    Return active, non-expired sessions belonging to the
    authenticated user.

    Refresh-token hashes are never returned.
    """

    current_session = (
        current_persistent_session(
            request,
            db,
            user,
        )
    )

    sessions = list_active_user_sessions(
        db,
        user.id,
    )

    return {
        "sessions": [
            session_public(
                session,
                current_session_id=(
                    current_session.id
                    if current_session
                    else None
                ),
            )
            for session in sessions
        ],
        "count":
            len(sessions),
    }


@router.delete("/sessions/{session_id}")
def delete_session(
    session_id: uuid.UUID,
    request: Request,
    response: Response,
    user: User =
        Depends(get_current_user),
    db: Session =
        Depends(get_db)
):
    """
    Revoke one session owned by the authenticated user.

    A user can never revoke another tenant/user's session because
    the lookup is scoped by both session ID and user ID.
    """

    require_csrf(
        request
    )

    session = (
        db.query(UserSession)
        .filter(
            UserSession.id == session_id,
            UserSession.user_id == user.id,
            UserSession.is_revoked.is_(False),
            UserSession.revoked_at.is_(None),
        )
        .first()
    )

    if not session:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code":
                        "AUTH_016",

                    "message":
                        "Session was not found",

                    "details":
                        None,
                }
            },
        )

    current_session = (
        current_persistent_session(
            request,
            db,
            user,
        )
    )

    is_current = (
        current_session is not None
        and str(current_session.id)
        == str(session.id)
    )

    revoke_session(
        db,
        session,
    )

    record_auth_security_event(
        db,
        event_type="session_revoked",
        organization_id=(
            user.organization_id
        ),
        user_id=user.id,
        request=request,
        metadata={
            "session_id":
                str(session.id),

            "current_session":
                is_current,
        },
    )

    db.commit()

    if is_current:
        clear_auth_cookies(
            response
        )

    return {
        "message":
            (
                "Current session revoked. "
                "You have been signed out."
                if is_current
                else "Session revoked successfully."
            ),

        "session_id":
            str(session.id),

        "current_session_revoked":
            is_current,
    }


@router.post("/sessions/revoke-all")
def revoke_all_sessions(
    request: Request,
    response: Response,
    user: User =
        Depends(get_current_user),
    db: Session =
        Depends(get_db)
):
    """
    Revoke every active authentication session belonging to
    the authenticated user.

    This operation signs the user out on every device, including
    the current device.
    """

    require_csrf(
        request
    )

    revoked_count = (
        revoke_all_user_sessions(
            db,
            user.id,
        )
    )

    # Clear legacy refresh-token columns while the application
    # remains compatible with the old authentication schema.
    user.refresh_token = None
    user.refresh_token_expires = None

    record_auth_security_event(
        db,
        event_type="sessions_revoked_all",
        organization_id=(
            user.organization_id
        ),
        user_id=user.id,
        request=request,
        metadata={
            "revoked_count":
                revoked_count,
        },
    )

    db.commit()

    clear_auth_cookies(
        response
    )

    return {
        "message":
            "All active sessions have been revoked.",

        "revoked_count":
            revoked_count,
    }

# ============================================================
# CURRENT USER
# ============================================================

@router.get("/me")
def get_me(
    user: User =
        Depends(get_current_user)
):

    return user_public(
        user
    )


@router.put("/me")
def update_me(
    request: Request,
    body: ProfileBody,
    user: User =
        Depends(get_current_user),
    db: Session =
        Depends(get_db)
):

    require_csrf(
        request
    )

    name = body.full_name.strip()

    if len(name) > 255:

        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code":
                        "AUTH_015",

                    "message":
                        "Name is too long",

                    "details":
                        None
                }
            }
        )

    user.full_name = name

    db.commit()

    return user_public(
        user
    )

# ============================================================
# CHANGE PASSWORD
# ============================================================

@router.put("/change-password")
def change_password(
    request: Request,
    response: Response,
    body: ChangePasswordBody,
    user: User = Depends(
        get_current_user
    ),
    db: Session = Depends(
        get_db
    ),
):
    """
    Change the authenticated user's password.

    Security policy:
    - current password must be verified
    - new password must satisfy the production password policy
    - new password cannot equal the existing password
    - all persisted refresh sessions are revoked
    - browser authentication cookies are cleared
    - user must authenticate again
    """

    require_csrf(
        request
    )

    ensure_rate_limit(
        request,
        f"change-password:{user.id}",
        5,
        3600,
        use_ip=False,
    )

    if (
        not user.password_hash
        or not verify_password(
            body.current_password,
            user.password_hash,
        )
    ):
        raise auth_error(
            "AUTH_013",
            401,
        )

    validate_password(
        body.new_password
    )

    if (
        body.new_password
        != body.confirm_password
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "AUTH_007",
                    "message": (
                        "Password confirmation "
                        "does not match"
                    ),
                    "details": None,
                }
            },
        )

    if verify_password(
        body.new_password,
        user.password_hash,
    ):
        raise auth_error(
            "AUTH_014",
            422,
        )

    user.password_hash = (
        hash_password(
            body.new_password
        )
    )

    user.login_attempts = 0
    user.locked_until = None

    revoked_count = (
        revoke_all_user_sessions(
            db,
            user.id,
        )
    )

    # Legacy fields remain explicitly cleared.
    user.refresh_token = None
    user.refresh_token_expires = None

    record_auth_security_event(
        db,
        event_type="password_changed",
        organization_id=(
            user.organization_id
        ),
        user_id=user.id,
        request=request,
        metadata={
            "sessions_revoked":
                revoked_count,
        },
    )

    db.commit()

    clear_auth_cookies(
        response
    )

    return {
        "message": (
            "Password changed successfully. "
            "Please sign in again."
        ),
        "reauthenticate": True,
    }

# ============================================================
# GDPR EXPORT
# ============================================================

@router.get("/me/export")
def export_me(
    user: User =
        Depends(get_current_user)
):

    return {
        "user":
            user_public(user),

        "consents": [
            {
                "type":
                    item.consent_type,

                "granted":
                    item.granted,

                "policy_version":
                    item.policy_version,

                "created_at":
                    (
                        item.created_at.isoformat()
                        if item.created_at
                        else None
                    )
            }

            for item
            in user.consent_logs
        ]
    }


# ============================================================
# ORGANIZATION DATA INVENTORY (READ-ONLY, PARTIAL)
# ============================================================

@router.get("/organization/data-inventory")
def get_organization_data_inventory(
    response: Response,
    user: User = Depends(require_roles("admin")),
    db: Session = Depends(get_db),
):
    # Defense in depth. Tenant ID is never selected by the caller.
    if (user.role != "admin" or not user.is_active or
            user.deleted_at is not None or user.organization_id is None):
        raise HTTPException(status_code=403, detail="Active administrator required.")

    response.headers["Cache-Control"] = "no-store"
    try:
        return build_consistent_organization_data_inventory(db, user.id, user.organization_id)
    except (OrganizationInventoryUnavailable, SQLAlchemyError) as exc:
        # Never expose schema, foreign tenant IDs, SQL or raw paths.
        logger.warning("Organization inventory unavailable; refusing partial result")
        raise HTTPException(
            status_code=503,
            detail="Organization inventory unavailable; no export was performed.",
        ) from exc


# ============================================================
# DELETE ACCOUNT
# ============================================================

@router.delete("/me")
def delete_me(
    request: Request,
    response: Response,
    user: User =
        Depends(get_current_user),
    db: Session =
        Depends(get_db)
):

    require_csrf(
        request
    )

    # Serialize owner departures on the organization row. Otherwise two
    # administrators can both observe the other as active and leave an
    # organization with no human able to revoke its API credentials.
    if user.role == "admin":
        org = (
            db.query(Organization)
            .filter(Organization.id == user.organization_id)
            .with_for_update()
            .one_or_none()
        )
        if org is None:
            raise HTTPException(
                status_code=409,
                detail="Organization ownership cannot be verified.",
            )

        remaining_admin = (
            db.query(User)
            .filter(
                User.organization_id == user.organization_id,
                User.id != user.id,
                User.role == "admin",
                User.is_active.is_(True),
                User.deleted_at.is_(None),
            )
            .first()
        )
        if remaining_admin is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "Cannot deactivate the last active organization "
                    "administrator. Transfer administration or request "
                    "an operator-assisted organization closure first."
                ),
            )

    user.is_active = False
    user.deleted_at = now_utc()

    user.email = (
        f"deleted-{user.id}"
        "@deleted.invalid"
    )

    # This endpoint deactivates an identity, not its organization.
    # Minimize retained account data and invalidate outstanding recovery
    # or verification credentials as part of the same transaction.
    user.full_name = ""
    user.last_login = None
    user.email_verified = False
    user.login_attempts = 0
    user.locked_until = None

    user.password_hash = None
    user.google_id = None
    user.github_id = None
    user.verification_token = None
    user.verification_token_expires = None
    user.verification_code_hash = None
    user.verification_code_expires = None
    user.reset_token = None
    user.reset_token_expires = None

    revoke_all_user_sessions(
        db,
        user.id,
    )

    user.refresh_token = None
    user.refresh_token_expires = None

    record_auth_security_event(
        db,
        event_type="account_deactivated",
        organization_id=user.organization_id,
        user_id=user.id,
        request=request,
        metadata={
            "scope": "identity_only",
            "organization_records_preserved": True,
        },
    )

    db.commit()

    clear_auth_cookies(
        response
    )

    return {
        "message": (
            "Account access deactivated and all sessions revoked. "
            "Organization records and API credentials remain."
        )
    }


# ============================================================
# CONSENT
# ============================================================

# Consent is recorded transactionally with an authenticated identity during
# signup. The former public /consent writer accepted arbitrary policy versions
# and created unattributed records; it is intentionally not exposed.


# ============================================================
# GOOGLE OAUTH
# ============================================================

@router.get("/google")
def google_login():
    """
    Start the Google OAuth 2.0 authorization flow.

    The OAuth state is stored in an HTTP-only cookie on the
    actual RedirectResponse returned to the browser. This is
    required so the callback can validate the request and
    prevent CSRF/login-CSRF attacks.
    """

    if (
        not settings.GOOGLE_CLIENT_ID
        or not settings.GOOGLE_CLIENT_SECRET
    ):
        raise auth_error(
            "AUTH_011",
            503
        )

    client = WebApplicationClient(
        settings.GOOGLE_CLIENT_ID
    )

    state = secrets.token_urlsafe(32)

    redirect_uri = (
        settings.GOOGLE_REDIRECT_URI
        or (
            f"{settings.APP_BASE_URL.rstrip('/')}"
            "/api/auth/google/callback"
        )
    )

    authorize_url = client.prepare_request_uri(
        "https://accounts.google.com/o/oauth2/v2/auth",
        redirect_uri=redirect_uri,
        scope=[
            "openid",
            "email",
            "profile",
        ],
        state=state,
        access_type="offline",
        prompt="select_account",
    )

    response = RedirectResponse(
        authorize_url,
        status_code=302,
    )

    response.set_cookie(
        OAUTH_STATE_COOKIE,
        state,
        max_age=600,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        domain=(
            settings.COOKIE_DOMAIN
            or None
        ),
        path="/api/auth",
    )

    return response


@router.get("/google/callback")
async def google_callback(
    code: str = Query(...),
    state: str = Query(...),
    request: Request = None,
    db: Session = Depends(get_db),
):
    """
    Complete the Google OAuth 2.0 authorization-code flow.

    The callback performs four security-critical steps:
    1. Validate the OAuth state.
    2. Exchange the authorization code for Google tokens.
    3. Verify the Google ID token.
    4. Create/login the local user and establish the application session.
    """

    import google.oauth2.id_token
    from google.auth.transport import requests as google_requests

    # --------------------------------------------------------
    # 1. Validate OAuth state
    # --------------------------------------------------------

    expected_state = request.cookies.get(
        OAUTH_STATE_COOKIE
    )

    if not expected_state:
        print(
            "[OAuth][Google] Missing OAuth state cookie."
        )

        raise auth_error(
            "AUTH_011",
            400,
            details={
                "reason": "missing_oauth_state_cookie"
            },
        )

    if not secrets.compare_digest(
        expected_state,
        state,
    ):
        print(
            "[OAuth][Google] OAuth state mismatch."
        )

        raise auth_error(
            "AUTH_011",
            400,
            details={
                "reason": "oauth_state_mismatch"
            },
        )

    # --------------------------------------------------------
    # 2. Exchange authorization code for Google tokens
    # --------------------------------------------------------

    token_url = (
        "https://oauth2.googleapis.com/token"
    )

    redirect_uri = (
        settings.GOOGLE_REDIRECT_URI
        or (
            f"{settings.APP_BASE_URL.rstrip('/')}"
            "/api/auth/google/callback"
        )
    )

    print(
        "[OAuth][Google] Token exchange:"
        f" redirect_uri={redirect_uri}"
    )

    async with httpx.AsyncClient(
        timeout=15.0
    ) as client:

        token_response = await client.post(
            token_url,
            data={
                "code": code,
                "client_id":
                    settings.GOOGLE_CLIENT_ID,
                "client_secret":
                    settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri":
                    redirect_uri,
                "grant_type":
                    "authorization_code",
            },
        )

    if token_response.status_code >= 400:
        try:
            google_error = (
                token_response.json()
            )
        except Exception:
            google_error = {
                "raw": token_response.text
            }

        # IMPORTANT:
        # Log the exact Google error so we can diagnose
        # invalid_client / invalid_grant /
        # redirect_uri_mismatch instead of hiding it.
        print(
            "[OAuth][Google] Token exchange failed:"
            f" status={token_response.status_code}"
            f" body={google_error}"
        )

        raise auth_error(
            "AUTH_011",
            400,
            details={
                "reason":
                    "google_token_exchange_failed",
                "provider_error":
                    google_error,
            },
        )

    try:
        token_data = (
            token_response.json()
        )

    except Exception as exc:
        print(
            "[OAuth][Google] Invalid token response:",
            token_response.text,
        )

        raise auth_error(
            "AUTH_011",
            400,
            details={
                "reason":
                    "invalid_google_token_response"
            },
        ) from exc

    # --------------------------------------------------------
    # 3. Verify Google ID token
    # --------------------------------------------------------

    id_token_value = token_data.get(
        "id_token"
    )

    if not id_token_value:
        print(
            "[OAuth][Google] Missing id_token."
        )

        raise auth_error(
            "AUTH_011",
            400,
            details={
                "reason":
                    "missing_google_id_token"
            },
        )

    try:
        claims = (
            google.oauth2.id_token
            .verify_oauth2_token(
                id_token_value,
                google_requests.Request(),
                settings.GOOGLE_CLIENT_ID,
            )
        )

    except Exception as exc:
        print(
            "[OAuth][Google] ID token verification failed:",
            repr(exc),
        )

        raise auth_error(
            "AUTH_011",
            400,
            details={
                "reason":
                    "google_id_token_verification_failed"
            },
        ) from exc

    # --------------------------------------------------------
    # 4. Validate Google identity
    # --------------------------------------------------------

    email = validate_email(
        claims.get(
            "email",
            "",
        )
    )

    if not claims.get(
        "email_verified"
    ):
        print(
            "[OAuth][Google] Google email is not verified:",
            email,
        )

        raise auth_error(
            "AUTH_011",
            400,
            details={
                "reason":
                    "google_email_not_verified"
            },
        )

    # --------------------------------------------------------
    # 5. Finish application OAuth flow
    # --------------------------------------------------------

    return await finish_oauth(
        provider="google",
        provider_id=str(
            claims["sub"]
        ),
        email=email,
        full_name=(
            claims.get("name")
            or email.split("@")[0]
        ),
        request=request,
        db=db,
    )


# ============================================================
# GITHUB OAUTH
# ============================================================

@router.get("/github")
def github_login():
    """
    Start the GitHub OAuth 2.0 authorization flow.

    The OAuth state is attached to the RedirectResponse
    actually returned to the browser.
    """

    if (
        not settings.GITHUB_CLIENT_ID
        or not settings.GITHUB_CLIENT_SECRET
    ):
        raise auth_error(
            "AUTH_011",
            503
        )

    state = secrets.token_urlsafe(32)

    redirect_uri = (
        settings.GITHUB_REDIRECT_URI
        or (
            f"{settings.APP_BASE_URL.rstrip('/')}"
            "/api/auth/github/callback"
        )
    )

    query = urlencode({
        "client_id":
            settings.GITHUB_CLIENT_ID,

        "redirect_uri":
            redirect_uri,

        "scope":
            "read:user user:email",

        "state":
            state,
    })

    response = RedirectResponse(
        (
            "https://github.com/login/oauth/authorize?"
            f"{query}"
        ),
        status_code=302,
    )

    response.set_cookie(
        OAUTH_STATE_COOKIE,
        state,
        max_age=600,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        domain=(
            settings.COOKIE_DOMAIN
            or None
        ),
        path="/api/auth",
    )

    return response


@router.get("/github/callback")
async def github_callback(
    code: str = Query(...),
    state: str = Query(...),
    request: Request = None,
    db: Session =
        Depends(get_db)
):

    expected_state = (
        request.cookies.get(
            OAUTH_STATE_COOKIE
        )
    )

    if (
        not expected_state
        or not secrets.compare_digest(
            expected_state,
            state
        )
    ):

        raise auth_error(
            "AUTH_011",
            400
        )

    redirect_uri = (
        settings.GITHUB_REDIRECT_URI
        or
        (
            f"{settings.APP_BASE_URL.rstrip('/')}"
            "/api/auth/github/callback"
        )
    )

    async with httpx.AsyncClient(
        timeout=15.0
    ) as client:

        token_response = (
            await client.post(
                "https://github.com/login/oauth/access_token",
                data={
                    "client_id":
                        settings.GITHUB_CLIENT_ID,

                    "client_secret":
                        settings.GITHUB_CLIENT_SECRET,

                    "code":
                        code,

                    "redirect_uri":
                        redirect_uri
                },
                headers={
                    "Accept":
                        "application/json"
                }
            )
        )

        if (
            token_response.status_code >= 400
        ):

            raise auth_error(
                "AUTH_011",
                400
            )

        token_data = (
            token_response.json()
        )

        access_token = (
            token_data.get(
                "access_token"
            )
        )

        if not access_token:
            raise auth_error(
                "AUTH_011",
                400
            )

        headers = {
            "Authorization":
                f"Bearer {access_token}",

            "Accept":
                "application/vnd.github+json",

            "X-GitHub-Api-Version":
                "2022-11-28"
        }

        user_response = (
            await client.get(
                "https://api.github.com/user",
                headers=headers
            )
        )

        email_response = (
            await client.get(
                "https://api.github.com/user/emails",
                headers=headers
            )
        )

    if (
        user_response.status_code >= 400
        or
        email_response.status_code >= 400
    ):

        raise auth_error(
            "AUTH_011",
            400
        )

    profile = (
        user_response.json()
    )

    emails = (
        email_response.json()
    )

    verified = [
        item
        for item
        in emails
        if item.get("verified")
    ]

    primary = next(
        (
            item
            for item
            in verified
            if item.get("primary")
        ),
        verified[0]
        if verified
        else None
    )

    if not primary:
        raise auth_error(
            "AUTH_011",
            400
        )

    return await finish_oauth(
        provider="github",
        provider_id=
            str(
                profile["id"]
            ),
        email=
            validate_email(
                primary["email"]
            ),
        full_name=
            profile.get(
                "name"
            )
            or
            profile.get(
                "login"
            )
            or
            primary["email"].split(
                "@"
            )[0],
        request=
            request,
        db=
            db
    )


# ============================================================
# OAUTH FINISH / ACCOUNT LINK
# ============================================================

async def finish_oauth(
    provider: str,
    provider_id: str,
    email: str,
    full_name: str,
    request: Request,
    db: Session
):

    id_field = (
        User.google_id
        if provider == "google"
        else User.github_id
    )

    user = db.query(
        User
    ).filter(
        id_field == provider_id,
        User.deleted_at.is_(None)
    ).first()

    if user:

        user.email_verified = True
        user.last_login = now_utc()

        response = RedirectResponse(
            "/dashboard/index.html",
            status_code=303
        )

        set_auth_cookies(
            response,
            user,
            request,
            db,
        )

        response.delete_cookie(
            OAUTH_STATE_COOKIE,
            path="/api/auth",
            domain=(
                settings.COOKIE_DOMAIN
                or None
            )
        )

        db.commit()

        return response

    existing = db.query(
        User
    ).filter(
        User.email == email,
        User.deleted_at.is_(None)
    ).first()

    if existing:

        response = RedirectResponse(
            "/oauth-link.html",
            status_code=303
        )

        issue_oauth_link_cookie(
            response,
            {
                "provider":
                    provider,

                "provider_id":
                    provider_id,

                "user_id":
                    str(existing.id)
            }
        )

        response.delete_cookie(
            OAUTH_STATE_COOKIE,
            path="/api/auth",
            domain=(
                settings.COOKIE_DOMAIN
                or None
            )
        )

        return response

    organization = (
        create_personal_org(
            db,
            email
        )
    )

    user = User(
        organization_id=
            organization.id,

        email=
            email,

        password_hash=
            None,

        full_name=
            full_name,

        role=
            "admin",

        is_active=
            True,

        email_verified=
            True,

        google_id=
            (
                provider_id
                if provider == "google"
                else None
            ),

        github_id=
            (
                provider_id
                if provider == "github"
                else None
            ),

        last_login=
            now_utc()
    )

    db.add(
        user
    )

    db.flush()

    response = RedirectResponse(
        "/dashboard/index.html",
        status_code=303
    )

    set_auth_cookies(
        response,
        user,
        request,
        db,
    )

    response.delete_cookie(
        OAUTH_STATE_COOKIE,
        path="/api/auth",
        domain=(
            settings.COOKIE_DOMAIN
            or None
        )
    )

    db.commit()

    return response


@router.get(
    "/oauth/link-status"
)
def oauth_link_status(
    request: Request,
    db: Session =
        Depends(get_db)
):

    import json
    import redis

    nonce = (
        request.cookies.get(
            OAUTH_LINK_COOKIE
        )
    )

    if not nonce:
        raise auth_error(
            "AUTH_011",
            400
        )

    client = redis.from_url(
        settings.REDIS_URL,
        decode_responses=True
    )

    raw = client.get(
        f"sde:oauth-link:{nonce}"
    )

    if not raw:
        raise auth_error(
            "AUTH_011",
            400
        )

    data = json.loads(
        raw
    )

    user = db.query(
        User
    ).filter(
        User.id ==
            data["user_id"],

        User.deleted_at.is_(None)
    ).first()

    if not user:
        raise auth_error(
            "AUTH_011",
            400
        )

    return {
        "provider":
            data["provider"],

        "email":
            user.email
    }


@router.post(
    "/oauth/link/confirm"
)
def oauth_link_confirm(
    request: Request,
    response: Response,
    body: OAuthConfirmBody,
    db: Session =
        Depends(get_db)
):

    import json
    import redis

    require_csrf(
        request
    )

    if not body.confirm:

        clear_auth_cookies(
            response
        )

        return {
            "message":
                "Linking cancelled"
        }

    nonce = (
        request.cookies.get(
            OAUTH_LINK_COOKIE
        )
    )

    if not nonce:
        raise auth_error(
            "AUTH_011",
            400
        )

    client = redis.from_url(
        settings.REDIS_URL,
        decode_responses=True
    )

    key = (
        f"sde:oauth-link:{nonce}"
    )

    raw = client.get(
        key
    )

    if not raw:
        raise auth_error(
            "AUTH_011",
            400
        )

    data = json.loads(
        raw
    )

    user = db.query(
        User
    ).filter(
        User.id ==
            data["user_id"],

        User.deleted_at.is_(None)
    ).first()

    if not user:
        raise auth_error(
            "AUTH_011",
            400
        )

    if (
        data["provider"]
        ==
        "google"
    ):
        user.google_id = (
            data["provider_id"]
        )

    else:
        user.github_id = (
            data["provider_id"]
        )

    user.email_verified = True
    user.last_login = now_utc()

    set_auth_cookies(
        response,
        user,
        request,
        db,
    )

    client.delete(
        key
    )

    response.delete_cookie(
        OAUTH_LINK_COOKIE,
        path="/api/auth",
        domain=(
            settings.COOKIE_DOMAIN
            or None
        )
    )

    db.commit()

    return {
        "message":
            "OAuth account linked",

        "user":
            user_public(user)
    }


# ============================================================
# API KEYS
# ============================================================


@router.post("/api-keys")
def create_api_key(
    request: Request,
    body: CreateApiKeyRequest,
    user: User = Depends(
        require_roles("admin")
    ),
    db: Session = Depends(
        get_db
    ),
):
    require_csrf(
        request
    )

    name = (
        body.name
        .strip()
    )

    if not name:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "API_KEY_001",
                    "message":
                        "API key name is required",
                    "details": None,
                }
            },
        )

    if len(name) > 255:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "API_KEY_001",
                    "message":
                        "API key name is too long",
                    "details": None,
                }
            },
        )

    raw_key = (
        f"sk-{secrets.token_urlsafe(32)}"
    )

    key_hash = token_hash(
        raw_key
    )

    api_key = ApiKey(
        organization_id=(
            user.organization_id
        ),
        name=name,
        key_hash=key_hash,
        permissions=(
            body.permissions.model_dump()
        ),
        is_active=True,
    )

    db.add(
        api_key
    )

    db.commit()

    db.refresh(
        api_key
    )

    return {
        "id": str(
            api_key.id
        ),
        "name": api_key.name,

        # Raw API key is intentionally returned
        # exactly once and is never persisted.
        "key": raw_key,

        "permissions":
            api_key.permissions,
    }


@router.get("/api-keys")
def list_api_keys(
    user: User = Depends(
        require_roles("admin")
    ),
    db: Session = Depends(
        get_db
    ),
):
    keys = (
        db.query(
            ApiKey
        )
        .filter(
            ApiKey.organization_id
            == user.organization_id,
            ApiKey.is_active.is_(
                True
            ),
        )
        .all()
    )

    return {
        "data": [
            {
                "id": str(
                    key.id
                ),
                "name":
                    key.name,
                "permissions":
                    key.permissions,
                "last_used_at":
                    key.last_used_at,
                "expires_at":
                    key.expires_at,
                "created_at":
                    key.created_at,
            }
            for key in keys
        ]
    }


@router.delete(
    "/api-keys/{key_id}"
)
def revoke_api_key(
    key_id: str,
    request: Request,
    user: User = Depends(
        require_roles("admin")
    ),
    db: Session = Depends(
        get_db
    ),
):
    require_csrf(
        request
    )

    try:
        parsed_key_id = uuid.UUID(
            key_id
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=404,
            detail="API key not found",
        ) from exc

    api_key = (
        db.query(
            ApiKey
        )
        .filter(
            ApiKey.id
            == parsed_key_id,
            ApiKey.organization_id
            == user.organization_id,
        )
        .first()
    )

    if not api_key:
        raise HTTPException(
            status_code=404,
            detail="API key not found",
        )

    api_key.is_active = False

    db.commit()

    return {
        "message":
            "API key revoked"
    }