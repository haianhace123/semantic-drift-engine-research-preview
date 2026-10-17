from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session


_LOCK_NAMESPACE = b"sde:event-evidence:v1"


def event_evidence_lock_key(
    organization_id: uuid.UUID,
    event_id: uuid.UUID,
) -> int:
    digest = hashlib.blake2b(
        _LOCK_NAMESPACE + organization_id.bytes + event_id.bytes,
        digest_size=8,
    ).digest()
    unsigned = int.from_bytes(digest, byteorder="big", signed=False)
    return unsigned - (1 << 64) if unsigned >= (1 << 63) else unsigned


def acquire_event_evidence_lock(
    db: Session,
    *,
    organization_id: uuid.UUID,
    event_id: uuid.UUID,
) -> None:
    db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": event_evidence_lock_key(organization_id, event_id)},
    )
