"""Read-only, tenant-scoped evidence for the latest monitored source comparison.

The latest two persisted observations may not be the detector's latest *usable*
file-backed pair. In particular, absent evidence must never be reported as a
successful or failed semantic analysis. This read path does not trigger work.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from src.models import MonitoredPage, PageSnapshot, SemanticDriftEvent


def read_source_diagnostics(
    db: Session, *, organization_id: uuid.UUID, monitored_page_id: uuid.UUID,
) -> dict | None:
    page = (
        db.query(MonitoredPage.id)
        .filter(
            MonitoredPage.id == monitored_page_id,
            MonitoredPage.organization_id == organization_id,
        )
        .one_or_none()
    )
    if page is None:
        return None

    snapshots = (
        db.query(PageSnapshot)
        .filter(
            PageSnapshot.monitored_page_id == monitored_page_id,
            PageSnapshot.organization_id == organization_id,
        )
        .order_by(PageSnapshot.crawled_at.desc(), PageSnapshot.id.desc())
        .limit(2)
        .all()
    )
    latest = snapshots[0] if snapshots else None
    result = {
        "source_id": str(monitored_page_id),
        "latest_snapshot": (
            {
                "id": str(latest.id),
                "crawled_at": latest.crawled_at.isoformat() if latest.crawled_at else None,
                "embedding_status": latest.embedding_status,
            }
            if latest is not None else None
        ),
        "latest_comparison": None,
    }
    if len(snapshots) < 2:
        return result

    new, old = snapshots
    changed = old.content_hash != new.content_hash
    comparison = {
        "old_snapshot_id": str(old.id),
        "new_snapshot_id": str(new.id),
        "content_changed": changed,
        "event_id": None,
        "analysis_status": None,
        "vector_analysis_status": None,
    }
    if changed:
        event = (
            db.query(SemanticDriftEvent)
            .filter(
                SemanticDriftEvent.organization_id == organization_id,
                SemanticDriftEvent.monitored_page_id == monitored_page_id,
                SemanticDriftEvent.old_snapshot_id == old.id,
                SemanticDriftEvent.new_snapshot_id == new.id,
            )
            .one_or_none()
        )
        if event is not None:
            comparison["event_id"] = str(event.id)
            comparison["analysis_status"] = event.analysis_status
            comparison["vector_analysis_status"] = event.vector_analysis_status
    result["latest_comparison"] = comparison
    return result
