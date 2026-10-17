"""Resolve successor issuance authority from SDE DB, never from client assertions."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

from sqlalchemy import text

from src.models.monitored_page import MonitoredPage
from src.models.page_snapshot import PageSnapshot

_SHA = re.compile(r'^[a-f0-9]{64}$')


class IssuanceSourceUnavailable(RuntimeError):
    """Source unavailable or has no authentic latest snapshot for this tenant."""


@dataclass(frozen=True, slots=True)
class RecoverySourceAuthority:
    url: str
    snapshot_id: uuid.UUID
    snapshot_sha256: str


def load_current_recovery_source(*, authority_session_factory,
                                 organization_id: uuid.UUID,
                                 source_id: uuid.UUID) -> RecoverySourceAuthority:
    if not isinstance(organization_id, uuid.UUID) or not isinstance(source_id, uuid.UUID):
        raise TypeError('Tenant and source IDs must be UUID')
    db = authority_session_factory()
    try:
        if db.in_transaction() or db.get_bind().dialect.name != 'postgresql':
            raise IssuanceSourceUnavailable('fresh_postgres_required')
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
        source = db.query(MonitoredPage).filter(
            MonitoredPage.organization_id == organization_id,
            MonitoredPage.id == source_id,
            MonitoredPage.is_active.is_(True),
        ).one_or_none()
        if source is None:
            raise IssuanceSourceUnavailable('source_missing')
        snapshot = db.query(PageSnapshot).filter(
            PageSnapshot.organization_id == organization_id,
            PageSnapshot.monitored_page_id == source_id,
        ).order_by(PageSnapshot.crawled_at.desc(), PageSnapshot.id.desc()).first()
        if snapshot is None or not isinstance(snapshot.content_hash, str) or not _SHA.fullmatch(snapshot.content_hash):
            raise IssuanceSourceUnavailable('snapshot_missing')
        return RecoverySourceAuthority(
            url=source.url,
            snapshot_id=snapshot.id,
            snapshot_sha256=snapshot.content_hash,
        )
    finally:
        try:
            db.rollback()
        finally:
            db.close()
