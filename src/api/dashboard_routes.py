"""
Authenticated dashboard APIs for Semantic Drift Engine.

Every query in this module is scoped to the authenticated organization.
The dashboard is a read model over the core temporal tables; it must never
become an authorization boundary by accident.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime, time, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from src.core.database import get_db
from src.core.security import get_current_user
from src.models import (
    Entity,
    MonitoredPage,
    PageSnapshot,
    SemanticDriftEvent,
    User,
)


router = APIRouter(
    prefix="/api/dashboard",
    tags=["dashboard"],
)


_RISK_SORT = case(
    (SemanticDriftEvent.risk_level == "critical", 5),
    (SemanticDriftEvent.risk_level == "high", 4),
    (
        SemanticDriftEvent.risk_level.in_(
            ["medium", "warning"]
        ),
        3,
    ),
    (SemanticDriftEvent.risk_level == "info", 2),
    (SemanticDriftEvent.risk_level == "low", 1),
    else_=0,
)


_STATE_DIMENSIONS = {
    "Privacy": {"policy"},
    "Security": {"security"},
    "Pricing": {"pricing"},
    "Legal": {"legal", "feature"},
}


_RISK_WEIGHT = {
    "critical": 42,
    "high": 34,
    "medium": 18,
    "warning": 18,
    "info": 12,
    "low": 8,
    "unknown": 10,
}


# ---------------------------------------------------------------------------
# Validation and serialization helpers
# ---------------------------------------------------------------------------

def _organization_id(
    current_user: User,
):
    """Return the authenticated tenant identifier."""
    return current_user.organization_id


def _parse_uuid(
    value: str,
    *,
    resource: str,
) -> uuid.UUID:
    """
    Parse a public UUID path parameter without leaking database errors.
    """
    try:
        return uuid.UUID(
            str(value)
        )
    except (
        TypeError,
        ValueError,
        AttributeError,
    ) as exc:
        raise HTTPException(
            status_code=404,
            detail=f"{resource} not found",
        ) from exc


def _parse_date_boundary(
    value: Optional[str],
    *,
    end_of_day: bool = False,
) -> Optional[datetime]:
    """
    Parse a dashboard date filter.

    HTML date controls submit YYYY-MM-DD. For an end boundary that means the
    end of that calendar day, not midnight at its beginning. Full ISO-8601
    timestamps remain supported for API clients.
    """
    if not value:
        return None

    raw = value.strip()

    if not raw:
        return None

    try:
        if len(raw) == 10:
            parsed_date = (
                datetime.fromisoformat(
                    raw
                ).date()
            )

            boundary_time = (
                time.max
                if end_of_day
                else time.min
            )

            return datetime.combine(
                parsed_date,
                boundary_time,
                tzinfo=timezone.utc,
            )

        parsed = datetime.fromisoformat(
            raw.replace(
                "Z",
                "+00:00",
            )
        )

    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=(
                "Invalid ISO-8601 date: "
                f"{value}"
            ),
        ) from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    return parsed


def _normalize_risk(
    value: Optional[str],
) -> str:
    risk = str(
        value or "unknown"
    ).strip().lower()

    return risk or "unknown"


def _event_risk(
    event: SemanticDriftEvent,
) -> str:
    """
    Prefer the normalized column and fall back to legacy JSON data.
    """
    column_risk = _normalize_risk(
        event.risk_level
    )

    if column_risk != "unknown":
        return column_risk

    delta = (
        event.semantic_delta_json
        or {}
    )

    semantic_analysis = (
        delta.get(
            "semantic_analysis"
        )
        or {}
    )

    return _normalize_risk(
        semantic_analysis.get(
            "overall_risk"
        )
    )


def _event_changes(
    event: SemanticDriftEvent,
) -> list:
    delta = (
        event.semantic_delta_json
        or {}
    )

    semantic_analysis = (
        delta.get(
            "semantic_analysis"
        )
        or {}
    )

    changes = (
        semantic_analysis.get(
            "changes"
        )
        or []
    )

    if isinstance(
        changes,
        list,
    ):
        return changes

    return []


def _event_payload(
    event: SemanticDriftEvent,
    *,
    entity_id,
    domain: str,
    entity_name: str,
    url: str,
    page_type: str,
    truncate_summary: bool = True,
) -> dict:
    """
    Serialize the canonical dashboard event representation.

    The payload intentionally exposes normalized event metadata alongside
    the semantic delta so clients do not need to reverse-engineer core fields
    from the model response.
    """
    summary = (
        event.summary_text
        or ""
    )

    if (
        truncate_summary
        and len(summary) > 200
    ):
        summary = (
            f"{summary[:200]}..."
        )

    return {
        "id": str(event.id),

        "entity_id": str(
            entity_id
        ),

        "domain": domain,

        "entity_name": entity_name,

        "url": url,

        "page_type": page_type,

        "summary": summary,

        "delta": (
            event.semantic_delta_json
            or {}
        ),

        "risk_level": _event_risk(
            event
        ),

        "confidence": (
            event.confidence
        ),

        "confidence_calibration_status": "not_established",

        "impact_score": (
            event.impact_score
        ),

        "category": (
            event.category
        ),

        "change_type": (
            event.change_type
        ),

        "changes_count": (
            event.changes_count
        ),

        "is_anomaly": bool(
            event.is_anomaly
        ),

        "is_pattern_part": bool(
            event.is_pattern_part
        ),

        "analysis_status": (
            event.analysis_status
        ),

        "detected_at": (
            event.detected_at.isoformat()
            if event.detected_at
            else None
        ),
    }


def _event_join_query(
    db: Session,
    organization_id,
):
    """
    Create the canonical tenant-scoped event read query.

    Tenant ownership is enforced redundantly on the event, page, and entity.
    This is deliberate: a corrupt foreign-key relationship must not turn into
    a cross-tenant read.
    """
    return (
        db.query(
            SemanticDriftEvent,
            Entity.id.label(
                "entity_id"
            ),
            Entity.domain,
            Entity.name,
            MonitoredPage.url,
            MonitoredPage.page_type,
        )
        .join(
            MonitoredPage,
            (
                SemanticDriftEvent.monitored_page_id
                == MonitoredPage.id
            ),
        )
        .join(
            Entity,
            (
                MonitoredPage.entity_id
                == Entity.id
            ),
        )
        .filter(
            (
                SemanticDriftEvent.organization_id
                == organization_id
            ),
            (
                MonitoredPage.organization_id
                == organization_id
            ),
            (
                Entity.organization_id
                == organization_id
            ),
        )
    )


def _apply_event_filters(
    query,
    *,
    risk_level: Optional[str],
    domain: Optional[str],
    page_type: Optional[str],
    start_date: Optional[str],
    end_date: Optional[str],
):
    """
    Apply dashboard filters at the database layer.
    """
    if domain:
        query = query.filter(
            Entity.domain.ilike(
                f"%{domain.strip()}%"
            )
        )

    if page_type:
        query = query.filter(
            MonitoredPage.page_type
            == page_type.strip().lower()
        )

    if risk_level:
        query = query.filter(
            func.lower(
                SemanticDriftEvent.risk_level
            )
            == risk_level.strip().lower()
        )

    start = _parse_date_boundary(
        start_date
    )

    if start is not None:
        query = query.filter(
            SemanticDriftEvent.detected_at
            >= start
        )

    end = _parse_date_boundary(
        end_date,
        end_of_day=True,
    )

    if end is not None:
        query = query.filter(
            SemanticDriftEvent.detected_at
            <= end
        )

    if (
        start is not None
        and end is not None
        and start > end
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "start_date must be "
                "before end_date"
            ),
        )

    return query


def _apply_event_sort(
    query,
    sort_by: Optional[str],
    sort_order: str,
):
    """
    Apply a stable database sort.

    Risk ordering is semantic rather than alphabetical:
    critical > high > medium > info > low > unknown.
    """
    field = (
        sort_by
        or "detected_at"
    ).strip().lower()

    order = (
        sort_order
        or "desc"
    ).strip().lower()

    if order not in {
        "asc",
        "desc",
    }:
        raise HTTPException(
            status_code=422,
            detail=(
                "sort_order must be "
                "'asc' or 'desc'"
            ),
        )

    sort_fields = {
        "domain":
            Entity.domain,

        "risk":
            _RISK_SORT,

        "detected_at":
            SemanticDriftEvent.detected_at,
    }

    sort_field = (
        sort_fields.get(
            field
        )
    )

    if sort_field is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "sort_by must be one of: "
                "domain, risk, detected_at"
            ),
        )

    primary = (
        sort_field.asc()
        if order == "asc"
        else sort_field.desc()
    )

    return query.order_by(
        primary,
        SemanticDriftEvent.detected_at.desc(),
        SemanticDriftEvent.id.desc(),
    )


def _safe_csv_value(
    value,
) -> str:
    """
    Escape spreadsheet-formula prefixes in exported crawler content.

    Crawled text is untrusted input. Without this guard, opening an exported
    CSV in spreadsheet software could interpret a malicious cell as a formula.
    """
    text = (
        ""
        if value is None
        else str(value)
    )

    if text.startswith(
        (
            "=",
            "+",
            "-",
            "@",
            "\t",
            "\r",
        )
    ):
        return f"'{text}"

    return text


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------

@router.get("/stats")
def get_stats(
    db: Session = Depends(
        get_db
    ),
    current_user: User = Depends(
        get_current_user
    ),
):
    """
    Return organization-scoped overview statistics.
    """
    organization_id = (
        _organization_id(
            current_user
        )
    )

    total_entities = (
        db.query(
            func.count(
                Entity.id
            )
        )
        .filter(
            Entity.organization_id
            == organization_id
        )
        .scalar()
        or 0
    )

    total_pages = (
        db.query(
            func.count(
                MonitoredPage.id
            )
        )
        .filter(
            MonitoredPage.organization_id
            == organization_id
        )
        .scalar()
        or 0
    )

    total_snapshots = (
        db.query(
            func.count(
                PageSnapshot.id
            )
        )
        .filter(
            PageSnapshot.organization_id
            == organization_id
        )
        .scalar()
        or 0
    )

    total_events = (
        db.query(
            func.count(
                SemanticDriftEvent.id
            )
        )
        .filter(
            SemanticDriftEvent.organization_id
            == organization_id
        )
        .scalar()
        or 0
    )

    high_risk_events = (
        db.query(
            func.count(
                SemanticDriftEvent.id
            )
        )
        .filter(
            (
                SemanticDriftEvent.organization_id
                == organization_id
            ),
            func.lower(
                SemanticDriftEvent.risk_level
            ).in_(
                [
                    "high",
                    "critical",
                ]
            ),
        )
        .scalar()
        or 0
    )

    latest_detection = (
        db.query(
            func.max(
                SemanticDriftEvent.detected_at
            )
        )
        .filter(
            SemanticDriftEvent.organization_id
            == organization_id
        )
        .scalar()
    )

    return {
        "total_entities":
            int(total_entities),

        "total_pages":
            int(total_pages),

        "total_snapshots":
            int(total_snapshots),

        "total_events":
            int(total_events),

        "high_risk_events":
            int(high_risk_events),

        "latest_detection": (
            latest_detection.isoformat()
            if latest_detection
            else None
        ),
    }


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

@router.get("/events")
def get_events(
    db: Session = Depends(
        get_db
    ),
    current_user: User = Depends(
        get_current_user
    ),
    limit: int = Query(
        50,
        ge=1,
        le=200,
    ),
    offset: int = Query(
        0,
        ge=0,
    ),
    risk_level: Optional[str] = None,
    domain: Optional[str] = None,
    page_type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    sort_by: Optional[str] = Query(
        "detected_at",
        description=(
            "Field to sort by: "
            "domain, risk, detected_at"
        ),
    ),
    sort_order: str = Query(
        "desc",
        description=(
            "Sort order: asc or desc"
        ),
    ),
):
    """
    Return a filtered, database-paginated list of drift events.
    """
    organization_id = (
        _organization_id(
            current_user
        )
    )

    query = _event_join_query(
        db,
        organization_id,
    )

    query = _apply_event_filters(
        query,
        risk_level=risk_level,
        domain=domain,
        page_type=page_type,
        start_date=start_date,
        end_date=end_date,
    )

    total = (
        query
        .order_by(None)
        .count()
    )

    rows = (
        _apply_event_sort(
            query,
            sort_by,
            sort_order,
        )
        .offset(
            offset
        )
        .limit(
            limit
        )
        .all()
    )

    data = [
        _event_payload(
            event,
            entity_id=entity_id,
            domain=domain_name,
            entity_name=entity_name,
            url=url,
            page_type=page_type_value,
        )
        for (
            event,
            entity_id,
            domain_name,
            entity_name,
            url,
            page_type_value,
        ) in rows
    ]

    return {
        "total": total,
        "offset": offset,
        "limit": limit,
        "data": data,
    }


@router.get("/events/export")
def export_events(
    db: Session = Depends(
        get_db
    ),
    current_user: User = Depends(
        get_current_user
    ),
    risk_level: Optional[str] = None,
    domain: Optional[str] = None,
    page_type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
):
    """
    Export organization-scoped drift events as a CSV attachment.
    """
    organization_id = (
        _organization_id(
            current_user
        )
    )

    query = _apply_event_filters(
        _event_join_query(
            db,
            organization_id,
        ),
        risk_level=risk_level,
        domain=domain,
        page_type=page_type,
        start_date=start_date,
        end_date=end_date,
    )

    rows = (
        query
        .order_by(
            SemanticDriftEvent.detected_at.desc(),
            SemanticDriftEvent.id.desc(),
        )
        .all()
    )

    output = io.StringIO(
        newline=""
    )

    writer = csv.DictWriter(
        output,
        fieldnames=[
            "id",
            "domain",
            "entity_name",
            "page_type",
            "summary",
            "risk",
            "changes_count",
            "detected_at",
            "url",
        ],
    )

    writer.writeheader()

    for (
        event,
        _entity_id,
        domain_name,
        entity_name,
        url,
        page_type_value,
    ) in rows:

        writer.writerow(
            {
                "id":
                    str(event.id),

                "domain":
                    _safe_csv_value(
                        domain_name
                    ),

                "entity_name":
                    _safe_csv_value(
                        entity_name
                    ),

                "page_type":
                    _safe_csv_value(
                        page_type_value
                    ),

                "summary":
                    _safe_csv_value(
                        event.summary_text
                    ),

                "risk":
                    _safe_csv_value(
                        _event_risk(
                            event
                        )
                    ),

                "changes_count": (
                    event.changes_count
                    if event.changes_count
                    is not None
                    else len(
                        _event_changes(
                            event
                        )
                    )
                ),

                "detected_at": (
                    event.detected_at.isoformat()
                    if event.detected_at
                    else ""
                ),

                "url":
                    _safe_csv_value(
                        url
                    ),
            }
        )

    filename = (
        "semantic_drift_events_"
        f"{datetime.now(timezone.utc).strftime('%Y%m%d')}"
        ".csv"
    )

    return Response(
        content=output.getvalue(),
        media_type=(
            "text/csv; charset=utf-8"
        ),
        headers={
            "Content-Disposition":
                (
                    "attachment; "
                    f'filename="{filename}"'
                ),

            "Cache-Control":
                "no-store",
        },
    )


@router.get("/events/{event_id}")
def get_event_detail(
    event_id: str,
    db: Session = Depends(
        get_db
    ),
    current_user: User = Depends(
        get_current_user
    ),
):
    """
    Return one drift event if it belongs to the current organization.
    """
    organization_id = (
        _organization_id(
            current_user
        )
    )

    parsed_event_id = (
        _parse_uuid(
            event_id,
            resource="Event",
        )
    )

    row = (
        _event_join_query(
            db,
            organization_id,
        )
        .filter(
            SemanticDriftEvent.id
            == parsed_event_id
        )
        .first()
    )

    if row is None:
        raise HTTPException(
            status_code=404,
            detail="Event not found",
        )

    (
        event,
        entity_id,
        domain_name,
        entity_name,
        url,
        page_type_value,
    ) = row

    return _event_payload(
        event,
        entity_id=entity_id,
        domain=domain_name,
        entity_name=entity_name,
        url=url,
        page_type=page_type_value,
        truncate_summary=False,
    )


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

@router.get("/timeline")
def get_timeline(
    db: Session = Depends(
        get_db
    ),
    current_user: User = Depends(
        get_current_user
    ),
    days: int = Query(
        30,
        ge=1,
        le=730,
    ),
):
    """
    Return organization-scoped daily event counts.
    """
    organization_id = (
        _organization_id(
            current_user
        )
    )

    now = datetime.now(
        timezone.utc
    )

    cutoff = (
        now
        - timedelta(
            days=days
        )
    )

    rows = (
        db.query(
            func.date(
                SemanticDriftEvent.detected_at
            ).label(
                "date"
            ),
            func.count(
                SemanticDriftEvent.id
            ).label(
                "count"
            ),
        )
        .filter(
            (
                SemanticDriftEvent.organization_id
                == organization_id
            ),
            (
                SemanticDriftEvent.detected_at
                >= cutoff
            ),
        )
        .group_by(
            func.date(
                SemanticDriftEvent.detected_at
            )
        )
        .order_by(
            func.date(
                SemanticDriftEvent.detected_at
            )
        )
        .all()
    )

    date_counts = {
        str(row.date):
            int(row.count)
        for row in rows
    }

    data = []

    current = (
        cutoff.date()
    )

    end = (
        now.date()
    )

    while current <= end:
        date_string = str(
            current
        )

        data.append(
            {
                "date":
                    date_string,

                "count":
                    date_counts.get(
                        date_string,
                        0,
                    ),
            }
        )

        current += timedelta(
            days=1
        )

    return {
        "data": data
    }
# ---------------------------------------------------------------------------
# Entities
# ---------------------------------------------------------------------------

@router.get("/entities")
def get_entities(
    db: Session = Depends(
        get_db
    ),
    current_user: User = Depends(
        get_current_user
    ),
    search: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = Query(
        100,
        ge=1,
        le=500,
    ),
    offset: int = Query(
        0,
        ge=0,
    ),
):
    """
    Return monitored entities owned by the authenticated organization.
    """
    organization_id = (
        _organization_id(
            current_user
        )
    )

    page_count_subquery = (
        db.query(
            MonitoredPage.entity_id.label(
                "entity_id"
            ),
            func.count(
                MonitoredPage.id
            ).label(
                "page_count"
            ),
        )
        .filter(
            MonitoredPage.organization_id
            == organization_id
        )
        .group_by(
            MonitoredPage.entity_id
        )
        .subquery()
    )

    query = (
        db.query(
            Entity,
            func.coalesce(
                page_count_subquery.c.page_count,
                0,
            ).label(
                "page_count"
            ),
        )
        .outerjoin(
            page_count_subquery,
            (
                page_count_subquery.c.entity_id
                == Entity.id
            ),
        )
        .filter(
            Entity.organization_id
            == organization_id
        )
    )

    if search:
        search_value = (
            search.strip()
        )

        if search_value:
            pattern = (
                f"%{search_value}%"
            )

            query = query.filter(
                or_(
                    Entity.name.ilike(
                        pattern
                    ),
                    Entity.domain.ilike(
                        pattern
                    ),
                )
            )

    if category:
        category_value = (
            category.strip()
        )

        if category_value:
            query = query.filter(
                func.lower(
                    Entity.category
                )
                == category_value.lower()
            )

    total = (
        query
        .order_by(None)
        .count()
    )

    rows = (
        query
        .order_by(
            Entity.name.asc(),
            Entity.domain.asc(),
            Entity.id.asc(),
        )
        .offset(
            offset
        )
        .limit(
            limit
        )
        .all()
    )

    return {
        "total":
            total,

        "offset":
            offset,

        "limit":
            limit,

        "data": [
            {
                "id":
                    str(entity.id),

                "domain":
                    entity.domain,

                "name":
                    entity.name,

                "category":
                    entity.category,

                "page_count":
                    int(
                        page_count or 0
                    ),

                "created_at": (
                    entity.created_at.isoformat()
                    if entity.created_at
                    else None
                ),
            }
            for (
                entity,
                page_count,
            ) in rows
        ],
    }


@router.get("/entities/{entity_id}/stats")
def get_entity_stats(
    entity_id: str,
    db: Session = Depends(
        get_db
    ),
    current_user: User = Depends(
        get_current_user
    ),
):
    """
    Return tenant-scoped aggregate metrics for one entity.
    """
    organization_id = (
        _organization_id(
            current_user
        )
    )

    parsed_entity_id = (
        _parse_uuid(
            entity_id,
            resource="Entity",
        )
    )

    entity = (
        db.query(
            Entity
        )
        .filter(
            Entity.id
            == parsed_entity_id,
            Entity.organization_id
            == organization_id,
        )
        .first()
    )

    if entity is None:
        raise HTTPException(
            status_code=404,
            detail="Entity not found",
        )

    page_ids_query = (
        db.query(
            MonitoredPage.id
        )
        .filter(
            MonitoredPage.organization_id
            == organization_id,
            MonitoredPage.entity_id
            == entity.id,
        )
    )

    page_count = (
        page_ids_query.count()
    )

    page_ids_subquery = (
        page_ids_query.subquery()
    )

    snapshot_count = (
        db.query(
            func.count(
                PageSnapshot.id
            )
        )
        .filter(
            PageSnapshot.organization_id
            == organization_id,
            PageSnapshot.monitored_page_id.in_(
                page_ids_subquery
            ),
        )
        .scalar()
        or 0
    )

    event_count = (
        db.query(
            func.count(
                SemanticDriftEvent.id
            )
        )
        .filter(
            SemanticDriftEvent.organization_id
            == organization_id,
            SemanticDriftEvent.monitored_page_id.in_(
                page_ids_subquery
            ),
        )
        .scalar()
        or 0
    )

    high_risk_events = (
        db.query(
            func.count(
                SemanticDriftEvent.id
            )
        )
        .filter(
            SemanticDriftEvent.organization_id
            == organization_id,
            SemanticDriftEvent.monitored_page_id.in_(
                page_ids_subquery
            ),
            func.lower(
                SemanticDriftEvent.risk_level
            ).in_(
                [
                    "high",
                    "critical",
                ]
            ),
        )
        .scalar()
        or 0
    )

    latest_detection = (
        db.query(
            func.max(
                SemanticDriftEvent.detected_at
            )
        )
        .filter(
            SemanticDriftEvent.organization_id
            == organization_id,
            SemanticDriftEvent.monitored_page_id.in_(
                page_ids_subquery
            ),
        )
        .scalar()
    )

    return {
        "id":
            str(entity.id),

        "domain":
            entity.domain,

        "name":
            entity.name,

        "category":
            entity.category,

        "page_count":
            int(page_count),

        "snapshot_count":
            int(snapshot_count),

        "event_count":
            int(event_count),

        "high_risk_events":
            int(high_risk_events),

        "latest_detection": (
            latest_detection.isoformat()
            if latest_detection
            else None
        ),
    }


@router.get("/entities/{entity_id}")
def get_entity_detail(
    entity_id: str,
    db: Session = Depends(
        get_db
    ),
    current_user: User = Depends(
        get_current_user
    ),
):
    """
    Return one entity and its monitored pages.

    Pages are explicitly organization-scoped even though the entity itself has
    already been authorized.
    """
    organization_id = (
        _organization_id(
            current_user
        )
    )

    parsed_entity_id = (
        _parse_uuid(
            entity_id,
            resource="Entity",
        )
    )

    entity = (
        db.query(
            Entity
        )
        .filter(
            Entity.id
            == parsed_entity_id,
            Entity.organization_id
            == organization_id,
        )
        .first()
    )

    if entity is None:
        raise HTTPException(
            status_code=404,
            detail="Entity not found",
        )

    pages = (
        db.query(
            MonitoredPage
        )
        .filter(
            MonitoredPage.organization_id
            == organization_id,
            MonitoredPage.entity_id
            == entity.id,
        )
        .order_by(
            MonitoredPage.page_type.asc(),
            MonitoredPage.url.asc(),
        )
        .all()
    )

    page_ids = [
        page.id
        for page in pages
    ]

    event_counts = {}

    if page_ids:
        rows = (
            db.query(
                SemanticDriftEvent.monitored_page_id,
                func.count(
                    SemanticDriftEvent.id
                ),
            )
            .filter(
                SemanticDriftEvent.organization_id
                == organization_id,
                SemanticDriftEvent.monitored_page_id.in_(
                    page_ids
                ),
            )
            .group_by(
                SemanticDriftEvent.monitored_page_id
            )
            .all()
        )

        event_counts = {
            page_id:
                int(count)
            for (
                page_id,
                count,
            ) in rows
        }

    return {
        "id":
            str(entity.id),

        "domain":
            entity.domain,

        "name":
            entity.name,

        "category":
            entity.category,

        "created_at": (
            entity.created_at.isoformat()
            if entity.created_at
            else None
        ),

        "pages": [
            {
                "id":
                    str(page.id),

                "url":
                    page.url,

                "page_type":
                    page.page_type,

                "crawl_frequency":
                    page.crawl_frequency,

                "is_active":
                    bool(
                        page.is_active
                    ),

                "event_count":
                    event_counts.get(
                        page.id,
                        0,
                    ),

                "created_at": (
                    page.created_at.isoformat()
                    if page.created_at
                    else None
                ),
            }
            for page in pages
        ],
    }


# ---------------------------------------------------------------------------
# Entity signals
# ---------------------------------------------------------------------------

@router.get("/entities/{entity_id}/events")
def get_entity_events(
    entity_id: str,
    db: Session = Depends(
        get_db
    ),
    current_user: User = Depends(
        get_current_user
    ),
    limit: int = Query(
        50,
        ge=1,
        le=200,
    ),
    offset: int = Query(
        0,
        ge=0,
    ),
):
    """
    Return drift events for a single authorized entity.
    """
    organization_id = (
        _organization_id(
            current_user
        )
    )

    parsed_entity_id = (
        _parse_uuid(
            entity_id,
            resource="Entity",
        )
    )

    entity_exists = (
        db.query(
            Entity.id
        )
        .filter(
            Entity.id
            == parsed_entity_id,
            Entity.organization_id
            == organization_id,
        )
        .first()
    )

    if entity_exists is None:
        raise HTTPException(
            status_code=404,
            detail="Entity not found",
        )

    query = (
        _event_join_query(
            db,
            organization_id,
        )
        .filter(
            Entity.id
            == parsed_entity_id
        )
    )

    total = (
        query
        .order_by(None)
        .count()
    )

    rows = (
        query
        .order_by(
            SemanticDriftEvent.detected_at.desc(),
            SemanticDriftEvent.id.desc(),
        )
        .offset(
            offset
        )
        .limit(
            limit
        )
        .all()
    )

    return {
        "total":
            total,

        "offset":
            offset,

        "limit":
            limit,

        "data": [
            _event_payload(
                event,
                entity_id=row_entity_id,
                domain=domain_name,
                entity_name=entity_name,
                url=url,
                page_type=page_type_value,
            )
            for (
                event,
                row_entity_id,
                domain_name,
                entity_name,
                url,
                page_type_value,
            ) in rows
        ],
    }


# ---------------------------------------------------------------------------
# Temporal digest
# ---------------------------------------------------------------------------

@router.get("/digest")
def get_digest(
    db: Session = Depends(
        get_db
    ),
    current_user: User = Depends(
        get_current_user
    ),
    hours: int = Query(
        24,
        ge=1,
        le=24 * 30,
    ),
):
    """
    Return a compact current-state digest for the authenticated tenant.

    This endpoint is intentionally deterministic. It does not manufacture an
    LLM narrative; it exposes the evidence needed by the frontend and future
    explanation services.
    """
    organization_id = (
        _organization_id(
            current_user
        )
    )

    now = datetime.now(
        timezone.utc
    )

    cutoff = (
        now
        - timedelta(
            hours=hours
        )
    )

    rows = (
        _event_join_query(
            db,
            organization_id,
        )
        .filter(
            SemanticDriftEvent.detected_at
            >= cutoff
        )
        .order_by(
            SemanticDriftEvent.detected_at.desc(),
            SemanticDriftEvent.id.desc(),
        )
        .limit(
            500
        )
        .all()
    )

    if not rows:
        return {
            "window_hours":
                hours,

            "generated_at":
                now.isoformat(),

            "event_count":
                0,

            "entity_count":
                0,

            "high_risk_count":
                0,

            "anomaly_count":
                0,

            "pattern_event_count":
                0,

            "top_entities":
                [],

            "category_distribution":
                {},

            "risk_distribution":
                {},

            "latest_events":
                [],
        }

    entity_state = {}
    category_distribution = {}
    risk_distribution = {}

    high_risk_count = 0
    anomaly_count = 0
    pattern_event_count = 0

    serialized_events = []

    for (
        event,
        entity_id,
        domain_name,
        entity_name,
        url,
        page_type_value,
    ) in rows:

        risk = (
            _event_risk(
                event
            )
        )

        category = (
            event.category
            or "uncategorized"
        )

        category_distribution[
            category
        ] = (
            category_distribution.get(
                category,
                0,
            )
            + 1
        )

        risk_distribution[
            risk
        ] = (
            risk_distribution.get(
                risk,
                0,
            )
            + 1
        )

        if risk in {
            "high",
            "critical",
        }:
            high_risk_count += 1

        if event.is_anomaly:
            anomaly_count += 1

        if event.is_pattern_part:
            pattern_event_count += 1

        key = str(
            entity_id
        )

        if key not in entity_state:
            entity_state[key] = {
                "id":
                    key,

                "name":
                    entity_name,

                "domain":
                    domain_name,

                "event_count":
                    0,

                "risk_score":
                    0,

                "latest_detection":
                    None,
            }

        entity_item = (
            entity_state[key]
        )

        entity_item[
            "event_count"
        ] += 1

        risk_weight = (
            _RISK_WEIGHT.get(
                risk,
                10,
            )
        )

        entity_item[
            "risk_score"
        ] += risk_weight

        if (
            entity_item[
                "latest_detection"
            ]
            is None
            or (
                event.detected_at
                and event.detected_at.isoformat()
                > entity_item[
                    "latest_detection"
                ]
            )
        ):
            entity_item[
                "latest_detection"
            ] = (
                event.detected_at.isoformat()
                if event.detected_at
                else None
            )

        if (
            len(
                serialized_events
            )
            < 20
        ):
            serialized_events.append(
                _event_payload(
                    event,
                    entity_id=entity_id,
                    domain=domain_name,
                    entity_name=entity_name,
                    url=url,
                    page_type=page_type_value,
                )
            )

    top_entities = (
        sorted(
            entity_state.values(),
            key=lambda item: (
                item[
                    "risk_score"
                ],
                item[
                    "event_count"
                ],
                item[
                    "latest_detection"
                ]
                or "",
            ),
            reverse=True,
        )[:10]
    )

    return {
        "window_hours":
            hours,

        "generated_at":
            now.isoformat(),

        "event_count":
            len(rows),

        "entity_count":
            len(entity_state),

        "high_risk_count":
            high_risk_count,

        "anomaly_count":
            anomaly_count,

        "pattern_event_count":
            pattern_event_count,

        "top_entities":
            top_entities,

        "category_distribution":
            category_distribution,

        "risk_distribution":
            risk_distribution,

        "latest_events":
            serialized_events,
    }