"""Authenticated, tenant-scoped public web monitoring enrollment."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from src.api.auth_routes import require_csrf
from src.core.database import get_db
from src.core.security import get_current_user, require_roles
from src.models import MonitoredPage, PageSnapshot, User
from src.services.source_diagnostics_service import read_source_diagnostics
from src.services.public_source_enrollment_service import (
    SourceEnrollmentConflict,
    SourceEnrollmentError,
    SourceEnrollmentQuotaExceeded,
    enroll_public_source,
    validate_public_source,
)

router = APIRouter(prefix="/api/sources", tags=["sources"])


class FrequencyRequest(BaseModel):
    crawl_frequency: str


class EnrollmentRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    label: str | None = Field(default=None, max_length=255)
    page_type: str
    crawl_frequency: str = "daily"


def _serialize(page: MonitoredPage, *, snapshot_count: int | None = None) -> dict:
    result = {
        "id": str(page.id),
        "entity_id": str(page.entity_id),
        "entity_name": (
            page.entity.name
            if page.entity is not None
            and page.entity.organization_id == page.organization_id
            else None
        ),
        "url": page.url,
        "page_type": page.page_type,
        "crawl_frequency": page.crawl_frequency,
        "is_active": bool(page.is_active),
        "last_crawled_at": (
            page.last_crawled_at.isoformat() if page.last_crawled_at else None
        ),
        "next_crawl_at": (
            page.next_crawl_at.isoformat() if page.next_crawl_at else None
        ),
        "last_crawl_attempt_at": (
            page.last_crawl_attempt_at.isoformat() if page.last_crawl_attempt_at else None
        ),
        "last_crawl_status": page.last_crawl_status,
        "last_crawl_failure_code": page.last_crawl_failure_code,
        "consecutive_crawl_failures": page.consecutive_crawl_failures or 0,
    }
    if snapshot_count is not None:
        result["snapshot_count"] = snapshot_count
    return result


@router.post("", status_code=201)
def enroll_source(
    body: EnrollmentRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "user")),
):
    require_csrf(request)
    try:
        source = validate_public_source(
            url=body.url,
            label=body.label,
            page_type=body.page_type,
            crawl_frequency=body.crawl_frequency,
        )
        page = enroll_public_source(
            db, organization_id=current_user.organization_id, source=source,
        )
    except SourceEnrollmentConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SourceEnrollmentQuotaExceeded as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except SourceEnrollmentError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _serialize(page)


@router.get("")
def list_sources(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    query = db.query(MonitoredPage).filter(
        MonitoredPage.organization_id == current_user.organization_id
    )
    total = query.count()
    items = query.options(joinedload(MonitoredPage.entity)).order_by(
        MonitoredPage.created_at.desc(), MonitoredPage.id
    ).offset(offset).limit(limit).all()
    counts = {}
    if items:
        rows = (
            db.query(PageSnapshot.monitored_page_id, func.count(PageSnapshot.id))
            .filter(
                PageSnapshot.organization_id == current_user.organization_id,
                PageSnapshot.monitored_page_id.in_([item.id for item in items]),
            )
            .group_by(PageSnapshot.monitored_page_id)
            .all()
        )
        counts = {page_id: count for page_id, count in rows}
    return {
        "total": total, "limit": limit, "offset": offset,
        "items": [_serialize(p, snapshot_count=counts.get(p.id, 0)) for p in items],
    }


@router.get("/{page_id}/diagnostics")
def source_diagnostics(
    page_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Describe persisted evidence without interpreting absence as success."""
    result = read_source_diagnostics(
        db,
        organization_id=current_user.organization_id,
        monitored_page_id=page_id,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Source not found")
    return result


@router.post("/{page_id}/pause")
def pause_source(
    page_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "user")),
):
    require_csrf(request)
    # Serialize with scheduler claim; a claimed page cannot be paused while
    # its active worker still owns that claim.
    page = db.query(MonitoredPage).filter(
        MonitoredPage.id == page_id,
        MonitoredPage.organization_id == current_user.organization_id,
    ).with_for_update().one_or_none()
    if page is None:
        raise HTTPException(status_code=404, detail="Source not found")
    if page.crawl_claim_token is not None:
        db.rollback()
        raise HTTPException(status_code=409, detail="Source has an active crawler claim")
    page.is_active = False
    db.commit()
    db.refresh(page)
    return _serialize(page)


@router.post("/{page_id}/resume")
def resume_source(
    page_id: uuid.UUID,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "user")),
):
    require_csrf(request)
    page = db.query(MonitoredPage).filter(
        MonitoredPage.id == page_id,
        MonitoredPage.organization_id == current_user.organization_id,
    ).with_for_update().one_or_none()
    if page is None:
        raise HTTPException(status_code=404, detail="Source not found")
    if page.crawl_claim_token is not None:
        db.rollback()
        raise HTTPException(status_code=409, detail="Source has an active crawler claim")
    if not page.is_active:
        page.is_active = True
        page.next_crawl_at = None
    db.commit()
    db.refresh(page)
    return _serialize(page)


@router.post("/{page_id}/frequency")
def set_source_frequency(
    page_id: uuid.UUID,
    body: FrequencyRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "user")),
):
    require_csrf(request)
    if body.crawl_frequency not in {"hourly", "daily", "weekly"}:
        raise HTTPException(status_code=422, detail="Unsupported crawl frequency")
    page = db.query(MonitoredPage).filter(
        MonitoredPage.id == page_id,
        MonitoredPage.organization_id == current_user.organization_id,
    ).with_for_update().one_or_none()
    if page is None:
        raise HTTPException(status_code=404, detail="Source not found")
    if page.crawl_claim_token is not None:
        db.rollback()
        raise HTTPException(status_code=409, detail="Source has an active crawler claim")
    if page.crawl_frequency != body.crawl_frequency:
        page.crawl_frequency = body.crawl_frequency
        # Let the existing scheduler decide when to claim the next crawl.
        page.next_crawl_at = None
    db.commit()
    db.refresh(page)
    return _serialize(page)
