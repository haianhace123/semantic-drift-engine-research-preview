"""Persistent authentication session management."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from src.core.security import now_utc, token_hash
from src.models import UserSession


def create_session(
    db: Session,
    *,
    user_id,
    refresh_token: str,
    expires_at: datetime,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> UserSession:
    """Create a persistent refresh-token session."""

    session = UserSession(
        user_id=user_id,
        refresh_token_hash=token_hash(refresh_token),
        expires_at=expires_at,
        last_used_at=now_utc(),
        ip_address=ip_address,
        user_agent=user_agent,
        is_revoked=False,
    )

    db.add(session)
    db.flush()

    return session


def get_session_by_refresh_token(
    db: Session,
    refresh_token: str,
) -> Optional[UserSession]:
    """Find the active session belonging to a refresh token."""

    digest = token_hash(refresh_token)

    return (
        db.query(UserSession)
        .filter(
            UserSession.refresh_token_hash == digest,
            UserSession.is_revoked.is_(False),
            UserSession.revoked_at.is_(None),
        )
        .first()
    )


def session_is_expired(
    session: UserSession,
) -> bool:
    """Return True when the persistent session has expired."""

    return session.expires_at <= now_utc()


def rotate_session(
    db: Session,
    *,
    session: UserSession,
    new_refresh_token: str,
    new_expires_at: datetime,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> UserSession:
    """Rotate the refresh token for an existing session."""

    session.refresh_token_hash = token_hash(
        new_refresh_token
    )

    session.expires_at = new_expires_at
    session.last_used_at = now_utc()

    if ip_address:
        session.ip_address = ip_address

    if user_agent:
        session.user_agent = user_agent

    db.flush()

    return session


def revoke_session(
    db: Session,
    session: UserSession,
) -> None:
    """Revoke one authentication session."""

    if session.is_revoked:
        return

    session.is_revoked = True
    session.revoked_at = now_utc()

    db.flush()


def revoke_session_by_refresh_token(
    db: Session,
    refresh_token: str,
) -> bool:
    """Revoke the session associated with a refresh token."""

    session = get_session_by_refresh_token(
        db,
        refresh_token,
    )

    if not session:
        return False

    revoke_session(
        db,
        session,
    )

    return True


def revoke_all_user_sessions(
    db: Session,
    user_id,
) -> int:
    """Revoke every active session belonging to a user."""

    sessions = (
        db.query(UserSession)
        .filter(
            UserSession.user_id == user_id,
            UserSession.is_revoked.is_(False),
        )
        .all()
    )

    if not sessions:
        return 0

    revoked_at = now_utc()

    for session in sessions:
        session.is_revoked = True
        session.revoked_at = revoked_at

    db.flush()

    return len(sessions)


def touch_session(
    db: Session,
    session: UserSession,
    *,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """Update session activity metadata."""

    session.last_used_at = now_utc()

    if ip_address:
        session.ip_address = ip_address

    if user_agent:
        session.user_agent = user_agent

    db.flush()

def list_active_user_sessions(
    db: Session,
    user_id,
) -> list[UserSession]:
    """
    Return active, non-expired sessions for one user.

    Ordering is newest activity first so the Settings UI can
    present the most recently used devices at the top.
    """

    return (
        db.query(UserSession)
        .filter(
            UserSession.user_id == user_id,
            UserSession.is_revoked.is_(False),
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now_utc(),
        )
        .order_by(
            UserSession.last_used_at.desc(),
            UserSession.created_at.desc(),
        )
        .all()
    )


def get_user_session(
    db: Session,
    *,
    user_id,
    session_id,
) -> Optional[UserSession]:
    """
    Resolve one active, non-expired session while enforcing
    user ownership.
    """

    return (
        db.query(UserSession)
        .filter(
            UserSession.id == session_id,
            UserSession.user_id == user_id,
            UserSession.is_revoked.is_(False),
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now_utc(),
        )
        .first()
    )


def revoke_other_user_sessions(
    db: Session,
    *,
    user_id,
    keep_session_id,
) -> int:
    """
    Revoke every active session except the explicitly retained one.
    """

    sessions = (
        db.query(UserSession)
        .filter(
            UserSession.user_id == user_id,
            UserSession.is_revoked.is_(False),
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now_utc(),
            UserSession.id != keep_session_id,
        )
        .all()
    )

    if not sessions:
        return 0

    revoked_at = now_utc()

    for session in sessions:
        session.is_revoked = True
        session.revoked_at = revoked_at

    db.flush()

    return len(sessions)