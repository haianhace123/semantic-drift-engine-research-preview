"""
Production authentication and authorization helpers.
"""

import hashlib
import secrets
import uuid

from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt

from fastapi import (
    Depends,
    HTTPException,
    Request,
    Security
)

from fastapi.security import (
    APIKeyHeader,
    HTTPAuthorizationCredentials,
    HTTPBearer,
)

from sqlalchemy.orm import Session

from src.core.config import settings
from src.core.database import get_db
from src.models import (
    ApiKey,
    Organization,
    User,
    UserSession,
)


bearer = HTTPBearer(
    auto_error=False
)

api_key_header = APIKeyHeader(
    name=settings.API_KEY_HEADER,
    auto_error=False,
)

ACCESS_COOKIE = (
    f"{settings.AUTH_COOKIE_PREFIX}"
    "access"
)

REFRESH_COOKIE = (
    f"{settings.AUTH_COOKIE_PREFIX}"
    "refresh"
)

CSRF_COOKIE = (
    f"{settings.AUTH_COOKIE_PREFIX}"
    "csrf"
)

OAUTH_STATE_COOKIE = (
    f"{settings.AUTH_COOKIE_PREFIX}"
    "oauth_state"
)

OAUTH_LINK_COOKIE = (
    f"{settings.AUTH_COOKIE_PREFIX}"
    "oauth_link"
)


class AuthError(Exception):
    """Internal authentication error."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 401,
        details=None
    ):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details
        super().__init__(message)


def now_utc() -> datetime:
    return datetime.now(
        timezone.utc
    )


def hash_password(
    password: str
) -> str:
    """
    Hash password using bcrypt
    cost factor 12.
    """

    return bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt(
            rounds=12
        )
    ).decode("utf-8")


def verify_password(
    password: str,
    password_hash: str
) -> bool:
    try:
        return bcrypt.checkpw(
            password.encode("utf-8"),
            password_hash.encode("utf-8")
        )
    except (
        ValueError,
        TypeError
    ):
        return False


def token_hash(
    token: str
) -> str:
    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


def create_access_token(
    user_id: str,
    *,
    session_id
) -> str:

    now = now_utc()

    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "type": "access",
        "iat": now,
        "exp": (
            now +
            timedelta(
                minutes=
                settings.ACCESS_TOKEN_EXPIRE_MINUTES
            )
        ),
    }

    return jwt.encode(
        payload,
        settings.SECRET_KEY,
        algorithm="HS256"
    )


def create_refresh_token(
    user_id: str,
    *,
    remember_me: bool = False,
) -> tuple[str, datetime]:

    now = now_utc()

    expires = (
        now +
        timedelta(
            days=
            settings.REFRESH_TOKEN_EXPIRE_DAYS
        )
    )

    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "jti": secrets.token_urlsafe(24),
        "remember_me": bool(remember_me),
        "iat": now,
        "exp": expires,
    }

    return (
        jwt.encode(
            payload,
            settings.SECRET_KEY,
            algorithm="HS256"
        ),
        expires
    )


def decode_token(
    token: str,
    expected_type: str
) -> dict:

    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=["HS256"]
        )

    except jwt.ExpiredSignatureError as exc:
        raise AuthError(
            "AUTH_005",
            "Token has expired",
            401
        ) from exc

    except jwt.PyJWTError as exc:
        raise AuthError(
            "AUTH_004",
            "Invalid token",
            401
        ) from exc

    if (
        payload.get("type") !=
        expected_type
        or not payload.get("sub")
    ):
        raise AuthError(
            "AUTH_004",
            "Invalid token",
            401
        )

    return payload


def verify_token(
    token: str
) -> Optional[str]:

    try:
        payload = decode_token(
            token,
            "access"
        )
        return payload.get("sub")

    except AuthError:
        return None


def extract_access_token(
    request: Request,
    credentials:
        Optional[
            HTTPAuthorizationCredentials
        ]
) -> Optional[str]:

    cookie_token = (
        request.cookies.get(
            ACCESS_COOKIE
        )
    )

    if cookie_token:
        return cookie_token

    if credentials:
        return credentials.credentials

    return None


def authenticate_access_token(
    token: str,
    db: Session,
) -> User:
    """
    Authenticate an access token against both the user account
    and its persistent session.
    """

    payload = decode_token(
        token,
        "access"
    )

    user_id = payload["sub"]
    session_id = payload.get("sid")

    try:
        user_uuid = uuid.UUID(str(user_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise AuthError("AUTH_004", "Invalid token", 401) from exc

    if not session_id:
        raise AuthError(
            "AUTH_004",
            "Invalid token",
            401
        )

    try:
        session_uuid = uuid.UUID(
            str(session_id)
        )
    except (
        TypeError,
        ValueError,
        AttributeError,
    ) as exc:
        raise AuthError(
            "AUTH_004",
            "Invalid token",
            401
        ) from exc

    user = db.query(
        User
    ).join(
        Organization,
        User.organization_id == Organization.id,
    ).filter(
        User.id == user_uuid,
        User.is_active.is_(True),
        User.deleted_at.is_(None),
        Organization.is_active.is_(True),
    ).first()

    if not user:
        raise AuthError(
            "AUTH_004",
            "Invalid token",
            401
        )

    session = db.query(
        UserSession
    ).filter(
        UserSession.id == session_uuid,
        UserSession.user_id == user.id,
        UserSession.is_revoked.is_(False),
        UserSession.revoked_at.is_(None),
        UserSession.expires_at > now_utc(),
    ).first()

    if not session:
        raise AuthError(
            "AUTH_004",
            "Invalid token",
            401
        )

    return user


def get_current_user(
    request: Request,
    credentials:
        Optional[
            HTTPAuthorizationCredentials
        ] = Security(bearer),
    db: Session = Depends(get_db)
) -> User:

    token = extract_access_token(
        request,
        credentials
    )

    if not token:
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "AUTH_004",
                    "message": "Invalid token",
                    "details": None
                }
            }
        )

    try:
        return authenticate_access_token(
            token,
            db,
        )

    except AuthError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details
                }
            }
        ) from exc


def get_current_organization(
    user: User =
    Depends(get_current_user)
) -> str:

    return str(
        user.organization_id
    )


def require_roles(
    *roles: str
):

    def dependency(
        user: User =
        Depends(get_current_user)
    ) -> User:

        if user.role not in roles:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": {
                        "code": "AUTH_013",
                        "message":
                            "You do not have permission to perform this action",
                        "details": None
                    }
                }
            )

        return user

    return dependency


def verify_api_key(
    api_key: str,
    db: Session,
) -> Optional[ApiKey]:
    """
    Validate an API key.

    Only active and unexpired API keys are accepted.
    """
    if not api_key:
        return None

    key_hash = token_hash(
        api_key
    )

    key = (
        db.query(
            ApiKey
        )
        .join(
            Organization,
            ApiKey.organization_id == Organization.id,
        )
        .filter(
            ApiKey.key_hash
            == key_hash,
            ApiKey.is_active.is_(
                True
            ),
            Organization.is_active.is_(True),
        )
        .first()
    )

    if not key:
        return None

    if (
        key.expires_at
        and key.expires_at
        <= now_utc()
    ):
        return None

    return key


def authenticate_api_key(
    api_key: Optional[str] = Security(
        api_key_header
    ),
    db: Session = Depends(
        get_db
    ),
) -> ApiKey:
    """
    Authenticate a request using X-API-Key.
    """
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "API_KEY_002",
                    "message":
                        "API key is required",
                    "details": None,
                }
            },
        )

    key = verify_api_key(
        api_key,
        db,
    )

    if not key:
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "API_KEY_003",
                    "message":
                        "API key is invalid, expired, or revoked",
                    "details": None,
                }
            },
        )

    key.last_used_at = now_utc()

    db.commit()

    return key


def require_api_key_permission(
    permission: str,
):
    """
    Require a permission on an authenticated API key.
    """

    def dependency(
        api_key: ApiKey = Depends(
            authenticate_api_key
        ),
    ) -> ApiKey:
        permissions = (
            api_key.permissions
            or {}
        )

        if not permissions.get(
            permission,
            False,
        ):
            raise HTTPException(
                status_code=403,
                detail={
                    "error": {
                        "code":
                            "API_KEY_004",
                        "message":
                            "API key does not have "
                            f"'{permission}' permission",
                        "details": None,
                    }
                },
            )

        return api_key

    return dependency