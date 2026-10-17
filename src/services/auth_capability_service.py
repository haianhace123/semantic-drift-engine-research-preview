"""Public, account-independent auth capability snapshot.

An email queue accepting a job is not evidence of delivered email. Password
recovery is advertised only while explicitly enabled, configured and backed
by a fresh, healthy email worker. Provider delivery needs separate acceptance.
"""

from __future__ import annotations

from email.utils import parseaddr

from sqlalchemy.orm import Session

from src.core.config import settings as default_settings
from src.services.worker_operational_read_service import (
    get_worker_operational_overview,
)


def get_auth_capabilities(
    db: Session,
    *,
    settings=default_settings,
) -> dict[str, bool]:
    google = bool(settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET)
    github = bool(settings.GITHUB_CLIENT_ID and settings.GITHUB_CLIENT_SECRET)
    ready = False

    # Explicit deployment intent is required even when stale DB rows exist.
    # Do not inspect or return secrets to the browser.
    import os

    expected = os.getenv("SDE_EMAIL_WORKER_EXPECTED", "false").strip().lower()
    sender = parseaddr(settings.EMAIL_FROM)[1].strip().lower()
    sender_configured = bool(
        sender
        and not sender.endswith("@example.com")
        and not sender.endswith("@invalid.test")
    )
    if expected == "true" and settings.RESEND_API_KEY and sender_configured:
        overview = get_worker_operational_overview(db)
        ready = any(
            item.worker_kind == "email"
            and item.expected
            and item.status == "healthy"
            for item in overview.workers
        )

    return {
        "email_recovery": bool(ready),
        "google": google,
        "github": github,
    }
