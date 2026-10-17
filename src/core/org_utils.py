"""
Organization resolution helpers for tenant-scoped background work.

Background jobs must never silently fall back to a shared/default
organization. A crawler must receive an explicit organization identifier
or be launched with CRAWLER_ORGANIZATION_ID configured.
"""

from __future__ import annotations

import os
import uuid

from sqlalchemy.orm import Session

from src.models import Organization


CRAWLER_ORGANIZATION_ENV = (
    "CRAWLER_ORGANIZATION_ID"
)


def resolve_organization_id(
    value: str | uuid.UUID | None = None,
) -> uuid.UUID:
    """
    Resolve the tenant identifier used by a crawler.

    Resolution order:
      1. explicit caller-provided organization ID;
      2. CRAWLER_ORGANIZATION_ID environment variable.

    There is intentionally no implicit "default" organization fallback.
    Silent tenant selection is unsafe in a multi-tenant ingestion path.
    """
    candidate = (
        value
        if value is not None
        else os.getenv(
            CRAWLER_ORGANIZATION_ENV,
            "",
        )
    )

    if isinstance(
        candidate,
        uuid.UUID,
    ):
        return candidate

    normalized = str(
        candidate
        or ""
    ).strip()

    if not normalized:
        raise ValueError(
            "Crawler organization is required. "
            "Pass organization_id explicitly or set "
            f"{CRAWLER_ORGANIZATION_ENV}."
        )

    try:
        return uuid.UUID(
            normalized
        )
    except ValueError as exc:
        raise ValueError(
            "Crawler organization ID must be a valid UUID."
        ) from exc


def require_active_organization(
    db: Session,
    organization_id: uuid.UUID,
) -> Organization:
    """
    Return an active organization or fail before ingestion begins.
    """
    organization = (
        db.query(
            Organization
        )
        .filter(
            Organization.id
            == organization_id,
            Organization.is_active.is_(
                True
            ),
        )
        .first()
    )

    if organization is None:
        raise ValueError(
            "Crawler organization does not exist "
            "or is inactive."
        )

    return organization