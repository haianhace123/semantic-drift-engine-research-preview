"""
Temporal intelligence API routes.

This module exposes time-oriented read models and discovery primitives for
Semantic Drift Engine. Every database operation is scoped to the authenticated
organization. Temporal state is derived from immutable snapshots and drift
events; the API never trusts client-supplied tenant identifiers.
"""

from __future__ import annotations

import secrets
import uuid

from collections import Counter
from datetime import datetime, time, timedelta, timezone
from typing import Optional

from src.services.embedding_service import (
    SnapshotContentError,
    load_snapshot_content,
)

from src.services.temporal_replay_service import (
    TemporalReplayValidationError,
    replay_organization_at,
)

from src.services.what_you_missed_evidence_service import (
    WhatYouMissedEvidenceValidationError,
)

from src.services.what_you_missed_service import (
    get_what_you_missed,
)
from src.services.persisted_pattern_qualification_service import (
    qualified_pattern_ids,
)
from src.services.alert_policy_revision_service import (
    bump_alert_policy_revision,
)

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
)

from pydantic import (
    BaseModel,
    Field,
)

from sqlalchemy import (
    and_,
    func,
    or_,
)

from sqlalchemy.orm import Session

from src.core.database import get_db
from src.core.security import (
    CSRF_COOKIE,
    get_current_user,
)

from src.models import (
    Alert,
    Anomaly,
    Entity,
    LineageObservationRecord,
    MonitoredPage,
    PageSnapshot,
    Pattern,
    SemanticDriftEvent,
    TemporalLineageCorrelationRecord,
    User,
    Watchlist,
)
from src.api.lineage_control_plane_responses import (
    TemporalLineageInvalidationResolutionResponse,
    temporal_lineage_invalidation_resolution_response,
)

from src.services.temporal_lineage_invalidation_resolution_service import (
    load_temporal_lineage_invalidation_resolution,
)
from src.services.temporal_lineage_invalidation_service import (
    TemporalLineageInvalidationEventNotFoundError,
)


router = APIRouter(
    prefix="/api/temporal",
    tags=["temporal"],
)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class TemporalComparisonRequest(
    BaseModel
):
    date_a: str
    date_b: str

    entity_ids: Optional[
        list[str]
    ] = None


class WatchlistCreateRequest(
    BaseModel
):
    name: str = Field(
        min_length=1,
        max_length=255,
    )

    description: Optional[str] = Field(
        default=None,
        max_length=2000,
    )

    entity_ids: list[str] = Field(
        default_factory=list,
    )

    page_ids: list[str] = Field(
        default_factory=list,
    )

    categories: list[str] = Field(
        default_factory=list,
    )

    alert_on_high_risk: bool = True
    alert_on_medium_risk: bool = False
    alert_on_pattern: bool = True
    alert_on_anomaly: bool = True


class WatchlistUpdateRequest(
    BaseModel
):
    name: Optional[str] = Field(
        default=None,
        max_length=255,
    )

    description: Optional[str] = Field(
        default=None,
        max_length=2000,
    )

    entity_ids: Optional[list[str]] = None
    page_ids: Optional[list[str]] = None
    categories: Optional[list[str]] = None

    alert_on_high_risk: Optional[
        bool
    ] = None

    alert_on_medium_risk: Optional[
        bool
    ] = None

    alert_on_pattern: Optional[
        bool
    ] = None

    alert_on_anomaly: Optional[
        bool
    ] = None

    is_active: Optional[
        bool
    ] = None


# ---------------------------------------------------------------------------
# General helpers
# ---------------------------------------------------------------------------

def _organization_id(
    current_user: User,
):
    """
    Return the tenant identifier from the authenticated principal.

    Tenant IDs are never accepted from request payloads or query parameters.
    """
    return current_user.organization_id


def _parse_uuid(
    value: str,
    *,
    resource: str,
) -> uuid.UUID:
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
            detail=(
                f"{resource} not found"
            ),
        ) from exc


def _parse_datetime(
    value: str,
    *,
    field_name: str,
    end_of_day: bool = False,
) -> datetime:
    """
    Parse an ISO-8601 timestamp.

    Date-only values are interpreted in UTC. An end boundary covers the whole
    submitted calendar day rather than stopping at midnight.
    """
    raw = str(
        value or ""
    ).strip()

    if not raw:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{field_name} is required"
            ),
        )

    try:
        if len(raw) == 10:
            date_value = (
                datetime.fromisoformat(
                    raw
                ).date()
            )

            return datetime.combine(
                date_value,
                (
                    time.max
                    if end_of_day
                    else time.min
                ),
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
                f"{field_name} must be "
                "a valid ISO-8601 date or timestamp"
            ),
        ) from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(
            tzinfo=timezone.utc
        )

    return parsed


def _serialize_datetime(
    value: Optional[datetime],
) -> Optional[str]:
    if value is None:
        return None

    return value.isoformat()


def _normalize_risk(
    value,
) -> str:
    normalized = str(
        value or "unknown"
    ).strip().lower()

    if not normalized:
        return "unknown"

    return normalized


def _event_risk(
    event: SemanticDriftEvent,
) -> str:
    """
    Prefer the normalized database column and support legacy events that stored
    risk only inside semantic_delta_json.
    """
    column_value = (
        _normalize_risk(
            event.risk_level
        )
    )

    if column_value != "unknown":
        return column_value

    semantic_analysis = (
        (
            event.semantic_delta_json
            or {}
        ).get(
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
    semantic_analysis = (
        (
            event.semantic_delta_json
            or {}
        ).get(
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

    if not isinstance(
        changes,
        list,
    ):
        return []

    return changes


def _require_csrf(
    request: Request,
) -> None:
    """
    Enforce double-submit CSRF for cookie-authenticated mutations.

    This mirrors the authentication routes without importing auth_routes and
    creating an API-module dependency cycle.
    """
    csrf_cookie = (
        request.cookies.get(
            CSRF_COOKIE
        )
    )

    csrf_header = (
        request.headers.get(
            "X-CSRF-Token"
        )
    )

    if (
        not csrf_cookie
        or not csrf_header
        or not secrets.compare_digest(
            csrf_cookie,
            csrf_header,
        )
    ):
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code":
                        "AUTH_004",

                    "message":
                        "CSRF validation failed",

                    "details":
                        None,
                }
            },
        )


def _validate_entity_ids(
    db: Session,
    organization_id,
    entity_ids: list[str],
) -> list[str]:
    """
    Validate an entity ID set and return canonical string UUIDs.

    A caller cannot create a watchlist or comparison scoped to another tenant
    by submitting foreign entity UUIDs.
    """
    if not entity_ids:
        return []

    parsed_ids = []

    for entity_id in entity_ids:
        try:
            parsed = uuid.UUID(
                str(entity_id)
            )

        except (
            TypeError,
            ValueError,
            AttributeError,
        ) as exc:
            raise HTTPException(
                status_code=422,
                detail=(
                    "entity_ids contains "
                    "an invalid UUID"
                ),
            ) from exc

        parsed_ids.append(
            parsed
        )

    unique_ids = list(
        dict.fromkeys(
            parsed_ids
        )
    )

    rows = (
        db.query(
            Entity.id
        )
        .filter(
            Entity.organization_id
            == organization_id,
            Entity.id.in_(
                unique_ids
            ),
        )
        .all()
    )

    owned_ids = {
        row.id
        for row in rows
    }

    if len(owned_ids) != len(
        unique_ids
    ):
        raise HTTPException(
            status_code=404,
            detail=(
                "One or more entities "
                "were not found"
            ),
        )

    return [
        str(entity_id)
        for entity_id in unique_ids
    ]


def _validate_page_ids(
    db: Session,
    organization_id,
    page_ids: list[str],
) -> list[str]:
    if not page_ids:
        return []

    parsed_ids = []

    for page_id in page_ids:
        try:
            parsed = uuid.UUID(
                str(page_id)
            )

        except (
            TypeError,
            ValueError,
            AttributeError,
        ) as exc:
            raise HTTPException(
                status_code=422,
                detail=(
                    "page_ids contains "
                    "an invalid UUID"
                ),
            ) from exc

        parsed_ids.append(
            parsed
        )

    unique_ids = list(
        dict.fromkeys(
            parsed_ids
        )
    )

    rows = (
        db.query(
            MonitoredPage.id
        )
        .filter(
            MonitoredPage.organization_id
            == organization_id,
            MonitoredPage.id.in_(
                unique_ids
            ),
        )
        .all()
    )

    owned_ids = {
        row.id
        for row in rows
    }

    if len(owned_ids) != len(
        unique_ids
    ):
        raise HTTPException(
            status_code=404,
            detail=(
                "One or more monitored pages "
                "were not found"
            ),
        )

    return [
        str(page_id)
        for page_id in unique_ids
    ]

def _normalize_watchlist_categories(
    categories: list[str],
) -> list[str]:
    return list(
        dict.fromkeys(
            category.strip()
            for category in categories
            if (
                isinstance(
                    category,
                    str,
                )
                and category.strip()
            )
        )
    )

# ---------------------------------------------------------------------------
# Shared tenant-scoped queries
# ---------------------------------------------------------------------------

def _event_query(
    db: Session,
    organization_id,
):
    """
    Canonical temporal event query.

    The organization predicate is repeated at event, page, and entity level so
    malformed historical foreign-key relationships cannot expose tenant data.
    """
    return (
        db.query(
            SemanticDriftEvent,
            MonitoredPage,
            Entity,
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
            SemanticDriftEvent.organization_id
            == organization_id,
            MonitoredPage.organization_id
            == organization_id,
            Entity.organization_id
            == organization_id,
        )
    )


def _snapshot_query(
    db: Session,
    organization_id,
):
    return (
        db.query(
            PageSnapshot,
            MonitoredPage,
            Entity,
        )
        .join(
            MonitoredPage,
            (
                PageSnapshot.monitored_page_id
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
            PageSnapshot.organization_id
            == organization_id,
            MonitoredPage.organization_id
            == organization_id,
            Entity.organization_id
            == organization_id,
        )
    )


def _lineage_by_snapshot(
    db: Session,
    organization_id,
    snapshot_ids,
) -> dict[str, tuple[dict, ...]]:
    unique_snapshot_ids = tuple(
        dict.fromkeys(
            snapshot_id
            for snapshot_id in snapshot_ids
            if snapshot_id is not None
        )
    )

    if not unique_snapshot_ids:
        return {}

    rows = (
        db.query(
            TemporalLineageCorrelationRecord.page_snapshot_id,
            LineageObservationRecord.source_external_id,
            LineageObservationRecord.asset_external_id,
            LineageObservationRecord.revision_external_id,
        )
        .join(
            PageSnapshot,
            and_(
                TemporalLineageCorrelationRecord.page_snapshot_id
                == PageSnapshot.id,
                PageSnapshot.organization_id
                == organization_id,
            ),
        )
        .join(
            LineageObservationRecord,
            and_(
                TemporalLineageCorrelationRecord.lineage_observation_id
                == LineageObservationRecord.id,
                LineageObservationRecord.organization_id
                == organization_id,
            ),
        )
        .filter(
            TemporalLineageCorrelationRecord.page_snapshot_id.in_(
                unique_snapshot_ids
            )
        )
        .order_by(
            TemporalLineageCorrelationRecord.page_snapshot_id.asc(),
            LineageObservationRecord.source_external_id.asc(),
            LineageObservationRecord.asset_external_id.asc(),
            LineageObservationRecord.revision_external_id.asc(),
            LineageObservationRecord.id.asc(),
        )
        .all()
    )

    lineage_by_snapshot: dict[str, list[dict]] = {}
    seen_by_snapshot: dict[str, set[tuple[str, str, str]]] = {}

    for (
        page_snapshot_id,
        source_external_id,
        asset_external_id,
        revision_external_id,
    ) in rows:
        snapshot_key = str(page_snapshot_id)
        identity_key = (
            source_external_id,
            asset_external_id,
            revision_external_id,
        )

        seen = seen_by_snapshot.setdefault(
            snapshot_key,
            set(),
        )

        if identity_key in seen:
            continue

        seen.add(identity_key)

        lineage_by_snapshot.setdefault(
            snapshot_key,
            [],
        ).append(
            {
                "source_external_id":
                    source_external_id,
                "asset_external_id":
                    asset_external_id,
                "revision_external_id":
                    revision_external_id,
            }
        )

    return {
        snapshot_id: tuple(roots)
        for snapshot_id, roots in lineage_by_snapshot.items()
    }


def _serialize_event(
    event: SemanticDriftEvent,
    page: MonitoredPage,
    entity: Entity,
    *,
    lineage_roots: tuple[dict, ...] = (),
) -> dict:
    return {
        "id":
            str(event.id),

        "organization_id":
            str(
                event.organization_id
            ),

        "entity_id":
            str(entity.id),

        "entity_name":
            entity.name,

        "domain":
            entity.domain,

        "category":
            event.category,

        "url":
            page.url,

        "page_type":
            page.page_type,

        "monitored_page_id":
            str(page.id),

        "old_snapshot_id":
            str(
                event.old_snapshot_id
            ),

        "new_snapshot_id":
            str(
                event.new_snapshot_id
            ),

        "lineage": {
            "status":
                (
                    "available"
                    if lineage_roots
                    else "unavailable"
                ),
            "roots":
                list(
                    lineage_roots
                ),
        },

        "summary":
            event.summary_text
            or "",

        "risk_level":
            _event_risk(
                event
            ),

        "confidence":
            event.confidence,

        "confidence_calibration_status":
            "not_established",

        "impact_score":
            event.impact_score,

        "change_type":
            event.change_type,

        "changes_count":
            event.changes_count,

        "is_anomaly":
            bool(
                event.is_anomaly
            ),

        "is_pattern_part":
            bool(
                event.is_pattern_part
            ),

        "analysis_status":
            event.analysis_status,

        "delta":
            event.semantic_delta_json
            or {},

        "detected_at":
            _serialize_datetime(
                event.detected_at
            ),

        "first_seen_at":
            _serialize_datetime(
                event.first_seen_at
            ),

        "last_seen_at":
            _serialize_datetime(
                event.last_seen_at
            ),
    }


def _serialize_snapshot(
    snapshot: PageSnapshot,
    page: MonitoredPage,
    entity: Entity,
) -> dict:
    return {
        "id":
            str(snapshot.id),

        "entity_id":
            str(entity.id),

        "entity_name":
            entity.name,

        "domain":
            entity.domain,

        "monitored_page_id":
            str(page.id),

        "url":
            page.url,

        "page_type":
            page.page_type,

        "content_hash":
            snapshot.content_hash,

        "content_size":
            snapshot.content_size,

        "raw_content_path":
            snapshot.raw_content_path,

        "crawled_at":
            _serialize_datetime(
                snapshot.crawled_at
            ),

        "created_at":
            _serialize_datetime(
                snapshot.created_at
            ),
    }


def _serialize_snapshot_evidence(
    snapshot: PageSnapshot,
) -> dict:
    return {
        "snapshot_id":
            str(snapshot.id),

        "monitored_page_id":
            str(
                snapshot.monitored_page_id
            ),

        "content_hash":
            snapshot.content_hash,

        "content_size":
            snapshot.content_size,

        "crawled_at":
            _serialize_datetime(
                snapshot.crawled_at
            ),

        "created_at":
            _serialize_datetime(
                snapshot.created_at
            ),

        "content":
            load_snapshot_content(
                snapshot
            ),
    }


# ---------------------------------------------------------------------------
# Temporal event stream
# ---------------------------------------------------------------------------

@router.get("/events")
def get_temporal_events(
    db: Session = Depends(
        get_db
    ),
    current_user: User = Depends(
        get_current_user
    ),
    start: Optional[str] = None,
    end: Optional[str] = None,
    entity_ids: Optional[
        list[str]
    ] = Query(
        default=None
    ),
    limit: int = Query(
        200,
        ge=1,
        le=500,
    ),
    offset: int = Query(
        0,
        ge=0,
    ),
    order: str = Query("asc", pattern="^(asc|desc)$"),
):
    """
    Return the canonical temporal event stream.

    Unlike the dashboard list endpoint, this route exists specifically for
    replay and temporal visualization. Events are ordered chronologically.
    """
    organization_id = (
        _organization_id(
            current_user
        )
    )

    query = _event_query(
        db,
        organization_id,
    )

    if start:
        start_time = _parse_datetime(
            start,
            field_name="start",
        )

        query = query.filter(
            SemanticDriftEvent.detected_at
            >= start_time
        )

    if end:
        end_time = _parse_datetime(
            end,
            field_name="end",
            end_of_day=True,
        )

        query = query.filter(
            SemanticDriftEvent.detected_at
            <= end_time
        )

    if (
        start
        and end
        and start_time > end_time
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "start must be before end"
            ),
        )

    if entity_ids:
        validated_ids = (
            _validate_entity_ids(
                db,
                organization_id,
                entity_ids,
            )
        )

        query = query.filter(
            Entity.id.in_(
                validated_ids
            )
        )

    total = (
        query
        .order_by(None)
        .count()
    )

    # Latest-first pagination is explicit and opt-in; legacy clients keep
    # ascending chronology. Stable UUID tie-break avoids page ambiguity.
    detected_order = (
        SemanticDriftEvent.detected_at.desc()
        if order == "desc"
        else SemanticDriftEvent.detected_at.asc()
    )
    id_order = (
        SemanticDriftEvent.id.desc()
        if order == "desc"
        else SemanticDriftEvent.id.asc()
    )
    rows = (
        query
        .order_by(detected_order, id_order)
        .offset(
            offset
        )
        .limit(
            limit
        )
        .all()
    )
    lineage_by_snapshot = (
        _lineage_by_snapshot(
            db,
            organization_id,
            (
                event.new_snapshot_id
                for (
                    event,
                    _page,
                    _entity,
                ) in rows
            ),
        )
    )
    return {
        "total":
            total,

        "offset":
            offset,

        "limit":
            limit,

        "data": [
            _serialize_event(
                event,
                page,
                entity,
                lineage_roots=(
                    lineage_by_snapshot.get(
                        str(
                            event.new_snapshot_id
                        ),
                        (),
                    )
                ),
            )
            for (
                event,
                page,
                entity,
            ) in rows
        ],
    }


@router.get("/events/{event_id}")
def get_temporal_event_by_id(
    event_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Resolve one persisted event, regardless of stream pagination.

    Only the existing tenant-scoped event/page/entity join may resolve the
    identity. Lineage is also read under the authenticated organization.
    A foreign or absent event receives the same 404 response.
    """
    organization_id = _organization_id(current_user)
    row = (
        _event_query(db, organization_id)
        .filter(SemanticDriftEvent.id == event_id)
        .one_or_none()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Temporal event not found.")

    event, page, entity = row
    lineage_by_snapshot = _lineage_by_snapshot(
        db,
        organization_id,
        (event.new_snapshot_id,),
    )
    return _serialize_event(
        event,
        page,
        entity,
        lineage_roots=lineage_by_snapshot.get(str(event.new_snapshot_id), ()),
    )


@router.get(
    "/events/{event_id}/integrity",
    response_model=TemporalLineageInvalidationResolutionResponse,
)
def get_temporal_event_integrity(
    event_id: uuid.UUID,
    db: Session = Depends(
        get_db
    ),
    current_user: User = Depends(
        get_current_user
    ),
) -> TemporalLineageInvalidationResolutionResponse:
    """
    Return persisted integrity resolution for one temporal event.

    This is the session-authenticated browser projection of the same durable
    resolution exposed through the public API-key lineage surface. Tenant scope
    comes exclusively from the authenticated principal.
    """
    organization_id = _organization_id(
        current_user
    )

    try:
        resolution = (
            load_temporal_lineage_invalidation_resolution(
                db,
                organization_id=organization_id,
                event_id=event_id,
            )
        )
    except TemporalLineageInvalidationEventNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={
                "error": {
                    "code": "LINEAGE_008",
                    "message": "Temporal drift event not found.",
                    "details": None,
                }
            },
        ) from exc

    return temporal_lineage_invalidation_resolution_response(
        resolution
    )


@router.get(
    "/events/{event_id}/evidence"
)
def get_temporal_event_evidence(
    event_id: uuid.UUID,
    db: Session = Depends(
        get_db
    ),
    current_user: User = Depends(
        get_current_user
    ),
):
    """
    Return the persisted evidence pair for one temporal event.

    Snapshot identity comes exclusively from the persisted event. The client
    cannot choose or combine arbitrary snapshots, and storage paths are never
    exposed through this projection.
    """
    organization_id = (
        _organization_id(
            current_user
        )
    )

    event = (
        db.query(
            SemanticDriftEvent
        )
        .filter(
            SemanticDriftEvent.id
            == event_id,
            SemanticDriftEvent.organization_id
            == organization_id,
        )
        .first()
    )

    if event is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "Temporal drift event not found."
            ),
        )

    snapshots = (
        db.query(
            PageSnapshot
        )
        .filter(
            PageSnapshot.organization_id
            == organization_id,
            PageSnapshot.id.in_(
                [
                    event.old_snapshot_id,
                    event.new_snapshot_id,
                ]
            ),
        )
        .all()
    )

    snapshots_by_id = {
        snapshot.id: snapshot
        for snapshot in snapshots
    }

    old_snapshot = (
        snapshots_by_id.get(
            event.old_snapshot_id
        )
    )

    new_snapshot = (
        snapshots_by_id.get(
            event.new_snapshot_id
        )
    )

    if (
        old_snapshot is None
        or new_snapshot is None
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Persisted temporal event evidence "
                "is incomplete."
            ),
        )

    if (
        old_snapshot.monitored_page_id
        != event.monitored_page_id
        or new_snapshot.monitored_page_id
        != event.monitored_page_id
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                "Persisted temporal event evidence "
                "does not match the monitored page."
            ),
        )

    try:
        before = (
            _serialize_snapshot_evidence(
                old_snapshot
            )
        )

        after = (
            _serialize_snapshot_evidence(
                new_snapshot
            )
        )
    except SnapshotContentError as exc:
        raise HTTPException(
            status_code=409,
            detail=(
                "Persisted temporal event content "
                "is unavailable."
            ),
        ) from exc

    return {
        "event_id":
            str(event.id),

        "monitored_page_id":
            str(
                event.monitored_page_id
            ),

        "analysis_status":
            event.analysis_status,

        "summary":
            event.summary_text
            or "",

        "delta":
            event.semantic_delta_json
            or {},

        "before":
            before,

        "after":
            after,
    }


# ---------------------------------------------------------------------------
# Snapshot time travel
# ---------------------------------------------------------------------------

@router.get("/snapshot")
def get_snapshot_at_time(
    at: str,
    db: Session = Depends(
        get_db
    ),
    current_user: User = Depends(
        get_current_user
    ),
    entity_id: Optional[str] = None,
    page_id: Optional[str] = None,
):
    """
    Resolve the latest known snapshot at or before a temporal cursor.

    When neither entity_id nor page_id is supplied, the endpoint returns one
    current snapshot per monitored page and therefore acts as a tenant-wide
    knowledge-state checkpoint.
    """
    organization_id = (
        _organization_id(
            current_user
        )
    )

    cursor = _parse_datetime(
        at,
        field_name="at",
        end_of_day=True,
    )

    query = (
        _snapshot_query(
            db,
            organization_id,
        )
        .filter(
            PageSnapshot.crawled_at
            <= cursor
        )
    )

    if entity_id:
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

        query = query.filter(
            Entity.id
            == parsed_entity_id
        )

    if page_id:
        parsed_page_id = (
            _parse_uuid(
                page_id,
                resource="Monitored page",
            )
        )

        page_exists = (
            db.query(
                MonitoredPage.id
            )
            .filter(
                MonitoredPage.id
                == parsed_page_id,
                MonitoredPage.organization_id
                == organization_id,
            )
            .first()
        )

        if page_exists is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    "Monitored page not found"
                ),
            )

        query = query.filter(
            MonitoredPage.id
            == parsed_page_id
        )

    rows = (
        query
        .order_by(
            MonitoredPage.id.asc(),
            PageSnapshot.crawled_at.desc(),
            PageSnapshot.id.desc(),
        )
        .all()
    )

    latest_by_page = {}

    for (
        snapshot,
        page,
        entity,
    ) in rows:

        page_key = str(
            page.id
        )

        if (
            page_key
            in latest_by_page
        ):
            continue

        latest_by_page[
            page_key
        ] = (
            _serialize_snapshot(
                snapshot,
                page,
                entity,
            )
        )

    data = list(
        latest_by_page.values()
    )

    if page_id:
        return {
            "at":
                cursor.isoformat(),

            "snapshot": (
                data[0]
                if data
                else None
            ),
        }

    return {
        "at":
            cursor.isoformat(),

        "snapshot_count":
            len(data),

        "data":
            data,
    }

# ---------------------------------------------------------------------------
# What You Missed
# ---------------------------------------------------------------------------

@router.get("/what-you-missed")
def get_what_you_missed_api(
    since: str,
    until: str,
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
    ),
    db: Session = Depends(
        get_db
    ),
    current_user: User = Depends(
        get_current_user
    ),
):
    """
    Explain the most important persisted changes the authenticated tenant
    missed during the temporal window ``(since, until]``.

    The read model is deterministic and tenant-scoped. Persisted Risk v2 is
    the primary importance signal when available; pattern and anomaly evidence
    may provide bounded uplift. Raw snapshot content, embeddings, and storage
    paths are never exposed by this endpoint.
    """

    organization_id = (
        _organization_id(
            current_user
        )
    )

    since_value = _parse_datetime(
        since,
        field_name="since",
        end_of_day=False,
    )

    until_value = _parse_datetime(
        until,
        field_name="until",
        end_of_day=True,
    )

    try:
        explanation = get_what_you_missed(
            db,
            organization_id=organization_id,
            since=since_value,
            until=until_value,
            limit=limit,
        )

    except WhatYouMissedEvidenceValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    return {
        "organization_id":
            str(
                explanation.organization_id
            ),

        "since":
            _serialize_datetime(
                explanation.since
            ),

        "until":
            _serialize_datetime(
                explanation.until
            ),

        "event_count":
            explanation.event_count,

        "explained_event_count":
            explanation.explained_event_count,

        "items": [
            {
                "event_id":
                    str(item.event_id),

                "entity_id":
                    (
                        str(item.entity_id)
                        if item.entity_id
                        is not None
                        else None
                    ),

                "monitored_page_id":
                    str(
                        item.monitored_page_id
                    ),

                "entity_name":
                    item.entity_name,

                "page_url":
                    item.page_url,

                "page_type":
                    item.page_type,

                "occurred_at":
                    _serialize_datetime(
                        item.occurred_at
                    ),

                "importance_score":
                    item.importance_score,

                "importance_level":
                    item.importance_level,

                "headline":
                    item.headline,

                "summary":
                    item.summary,

                "risk_score":
                    item.risk_score,

                "semantic_magnitude":
                    item.semantic_magnitude,

                "impact_score":
                    item.impact_score,

                "pattern_ids": [
                    str(pattern_id)
                    for pattern_id
                    in item.pattern_ids
                ],

                "anomaly_ids": [
                    str(anomaly_id)
                    for anomaly_id
                    in item.anomaly_ids
                ],

                "topology_shift_ids": [
                    str(topology_shift_id)
                    for topology_shift_id
                    in item.topology_shift_ids
                ],

                "reasons": [
                    {
                        "code":
                            reason.code,

                        "weight":
                            reason.weight,

                        "text":
                            reason.text,
                    }
                    for reason
                    in item.reasons
                ],
            }
            for item
            in explanation.items
        ],
    }

# ---------------------------------------------------------------------------
# Temporal replay
# ---------------------------------------------------------------------------

@router.get("/replay")
def get_temporal_replay(
    at: str,
    db: Session = Depends(
        get_db
    ),
    current_user: User = Depends(
        get_current_user
    ),
):
    """
    Reconstruct the persisted tenant knowledge state at a temporal cursor.

    Unlike the legacy snapshot endpoint, replay includes semantic events and
    persisted derived state such as graph metrics, topology shifts, and
    versioned Risk v2 assessments.

    The service applies both effective-time and knowledge-time boundaries so
    backfilled derived evidence cannot leak into an earlier replay.
    """

    organization_id = (
        _organization_id(
            current_user
        )
    )

    cursor = _parse_datetime(
        at,
        field_name="at",
        end_of_day=True,
    )

    try:
        replay = replay_organization_at(
            db,
            organization_id=organization_id,
            cursor=cursor,
        )
    except TemporalReplayValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc

    graph = None

    if replay.graph is not None:
        graph = {
            "observed_at":
                _serialize_datetime(
                    replay.graph.observed_at
                ),

            "created_at":
                _serialize_datetime(
                    replay.graph.created_at
                ),

            "semantic_drift_event_id": (
                str(
                    replay.graph.semantic_drift_event_id
                )
                if replay.graph.semantic_drift_event_id
                is not None
                else None
            ),

            "entity_count":
                replay.graph.entity_count,

            "active_edge_count":
                replay.graph.active_edge_count,

            "entities": [
                {
                    "entity_id":
                        str(entity.entity_id),

                    "in_degree":
                        entity.in_degree,

                    "out_degree":
                        entity.out_degree,

                    "degree":
                        entity.degree,

                    "degree_centrality":
                        entity.degree_centrality,

                    "downstream_reach":
                        entity.downstream_reach,

                    "downstream_reach_ratio":
                        entity.downstream_reach_ratio,
                }
                for entity in replay.graph.entities
            ],
        }

    topology = None

    if replay.topology is not None:
        topology = {
            "observed_at":
                _serialize_datetime(
                    replay.topology.observed_at
                ),

            "created_at":
                _serialize_datetime(
                    replay.topology.created_at
                ),

            "semantic_drift_event_id": (
                str(
                    replay.topology.semantic_drift_event_id
                )
                if replay.topology.semantic_drift_event_id
                is not None
                else None
            ),

            "previous_observed_at":
                _serialize_datetime(
                    replay.topology.previous_observed_at
                ),

            "entities": [
                {
                    "entity_id":
                        str(entity.entity_id),

                    "classification":
                        entity.classification,

                    "severity":
                        entity.severity,

                    "shift_score":
                        entity.shift_score,

                    "degree_delta":
                        entity.degree_delta,

                    "degree_centrality_delta":
                        entity.degree_centrality_delta,

                    "downstream_reach_delta":
                        entity.downstream_reach_delta,

                    "downstream_reach_ratio_delta":
                        entity.downstream_reach_ratio_delta,
                }
                for entity in replay.topology.entities
            ],
        }

    return {
        "at":
            _serialize_datetime(
                replay.cursor
            ),

        "organization_id":
            str(
                replay.organization_id
            ),

        "snapshot_count":
            len(
                replay.snapshots
            ),

        "event_count":
            len(
                replay.events
            ),

        "risk_assessment_count":
            len(
                replay.risk_assessments
            ),

        "snapshots": [
            {
                "snapshot_id":
                    str(
                        snapshot.snapshot_id
                    ),

                "monitored_page_id":
                    str(
                        snapshot.monitored_page_id
                    ),

                "entity_id":
                    str(
                        snapshot.entity_id
                    ),

                "content_hash":
                    snapshot.content_hash,

                "crawled_at":
                    _serialize_datetime(
                        snapshot.crawled_at
                    ),

                "created_at":
                    _serialize_datetime(
                        snapshot.created_at
                    ),
            }
            for snapshot in replay.snapshots
        ],

        "events": [
            {
                "event_id":
                    str(
                        event.event_id
                    ),

                "monitored_page_id":
                    str(
                        event.monitored_page_id
                    ),

                "entity_id":
                    str(
                        event.entity_id
                    ),

                "old_snapshot_id":
                    str(
                        event.old_snapshot_id
                    ),

                "new_snapshot_id":
                    str(
                        event.new_snapshot_id
                    ),

                "detected_at":
                    _serialize_datetime(
                        event.detected_at
                    ),

                "risk_level":
                    event.risk_level,

                "semantic_magnitude":
                    event.semantic_magnitude,
            }
            for event in replay.events
        ],

        "graph":
            graph,

        "topology":
            topology,

        "risk_assessments": [
            {
                "assessment_id":
                    str(
                        assessment.assessment_id
                    ),

                "semantic_drift_event_id": (
                    str(
                        assessment.semantic_drift_event_id
                    )
                    if assessment.semantic_drift_event_id
                    is not None
                    else None
                ),

                "risk_version":
                    assessment.risk_version,

                "assessed_at":
                    _serialize_datetime(
                        assessment.assessed_at
                    ),

                "created_at":
                    _serialize_datetime(
                        assessment.created_at
                    ),

                "score":
                    assessment.score,

                "topology_score":
                    assessment.topology_score,

                "topology_contribution":
                    assessment.topology_contribution,

                "has_topology_signal":
                    assessment.has_topology_signal,

                "has_previous_topology_state":
                    assessment.has_previous_topology_state,
            }
            for assessment
            in replay.risk_assessments
        ],
    }

# ---------------------------------------------------------------------------
# Temporal comparison
# ---------------------------------------------------------------------------

@router.post("/compare")
def temporal_compare(
    request: TemporalComparisonRequest,
    db: Session = Depends(
        get_db
    ),
    current_user: User = Depends(
        get_current_user
    ),
):
    """
    Compare accumulated semantic state at two temporal boundaries.

    entity_ids refer to Entity IDs, not MonitoredPage IDs. The original route
    mixed those concepts and therefore produced logically incorrect filters.
    """
    organization_id = (
        _organization_id(
            current_user
        )
    )

    date_a = _parse_datetime(
        request.date_a,
        field_name="date_a",
        end_of_day=True,
    )

    date_b = _parse_datetime(
        request.date_b,
        field_name="date_b",
        end_of_day=True,
    )

    entity_ids = []

    if request.entity_ids:
        entity_ids = (
            _validate_entity_ids(
                db,
                organization_id,
                request.entity_ids,
            )
        )

    query_a = (
        _event_query(
            db,
            organization_id,
        )
        .filter(
            SemanticDriftEvent.detected_at
            <= date_a
        )
    )

    query_b = (
        _event_query(
            db,
            organization_id,
        )
        .filter(
            SemanticDriftEvent.detected_at
            <= date_b
        )
    )

    if entity_ids:
        query_a = query_a.filter(
            Entity.id.in_(
                entity_ids
            )
        )

        query_b = query_b.filter(
            Entity.id.in_(
                entity_ids
            )
        )

    rows_a = query_a.all()
    rows_b = query_b.all()

    entities_a = {
        str(entity.id)
        for (
            _event,
            _page,
            entity,
        ) in rows_a
    }

    entities_b = {
        str(entity.id)
        for (
            _event,
            _page,
            entity,
        ) in rows_b
    }

    high_risk_a = sum(
        1
        for (
            event,
            _page,
            _entity,
        ) in rows_a
        if _event_risk(event)
        in {
            "critical",
            "high",
        }
    )

    high_risk_b = sum(
        1
        for (
            event,
            _page,
            _entity,
        ) in rows_b
        if _event_risk(event)
        in {
            "critical",
            "high",
        }
    )

    return {
        "date_a":
            date_a.isoformat(),

        "date_b":
            date_b.isoformat(),

        "entities_a":
            sorted(
                entities_a
            ),

        "entities_b":
            sorted(
                entities_b
            ),

        "events_a_count":
            len(rows_a),

        "events_b_count":
            len(rows_b),

        "new_entities":
            sorted(
                entities_b
                - entities_a
            ),

        "removed_entities":
            sorted(
                entities_a
                - entities_b
            ),

        "high_risk_a":
            high_risk_a,

        "high_risk_b":
            high_risk_b,

        "event_delta":
            len(rows_b)
            - len(rows_a),

        "high_risk_delta":
            high_risk_b
            - high_risk_a,
    }
# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

@router.get("/patterns")
def get_patterns(
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
    pattern_type: Optional[str] = None,
    active_only: bool = True,
):
    """
    Return persisted patterns for the authenticated organization.
    """
    organization_id = (
        _organization_id(
            current_user
        )
    )

    query = (
        db.query(
            Pattern
        )
        .filter(
            Pattern.organization_id
            == organization_id
        )
    )

    if active_only:
        query = query.filter(
            Pattern.is_active.is_(
                True
            ),
            Pattern.is_archived.is_(
                False
            ),
        )

    if pattern_type:
        normalized_type = (
            pattern_type
            .strip()
            .lower()
        )

        if normalized_type:
            query = query.filter(
                func.lower(
                    Pattern.pattern_type
                )
                == normalized_type
            )

    total = (
        query
        .order_by(None)
        .count()
    )

    patterns = (
        query
        .order_by(
            Pattern.significance_score.desc(),
            Pattern.detected_at.desc(),
            Pattern.id.desc(),
        )
        .offset(
            offset
        )
        .limit(
            limit
        )
        .all()
    )

    # Qualify only the fetched page. This must not change persisted lifecycle,
    # total, ordering or offset: callers can inspect unqualified history.
    qualified_ids = qualified_pattern_ids(
        db,
        organization_id=organization_id,
        patterns=patterns,
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
                    str(pattern.id),

                "name":
                    pattern.name,

                "description":
                    pattern.description,

                "pattern_type":
                    pattern.pattern_type,

                "event_ids":
                    pattern.event_ids
                    or [],

                "entities":
                    pattern.entities
                    or [],

                "categories":
                    pattern.categories
                    or [],

                "event_count":
                    pattern.event_count
                    or 0,

                "entity_count":
                    pattern.entity_count
                    or 0,

                "significance_score":
                    pattern.significance_score
                    or 0.0,

                "time_span_days":
                    pattern.time_span_days
                    or 0,

                "first_event_at":
                    _serialize_datetime(
                        pattern.first_event_at
                    ),

                "last_event_at":
                    _serialize_datetime(
                        pattern.last_event_at
                    ),

                "summary":
                    pattern.summary,

                "detected_at":
                    _serialize_datetime(
                        pattern.detected_at
                    ),

                "risk_evidence_qualified": (
                    pattern.id in qualified_ids
                    if pattern.pattern_type == "risk_escalation"
                    else None
                ),

                "is_active":
                    bool(
                        pattern.is_active
                    ),

                "is_archived":
                    bool(
                        pattern.is_archived
                    ),
            }
            for pattern in patterns
        ],
    }


@router.post("/patterns/detect")
def detect_patterns(
    request: Request,
    db: Session = Depends(
        get_db
    ),
    current_user: User = Depends(
        get_current_user
    ),
    lookback_days: int = Query(
        30,
        ge=1,
        le=365,
    ),
):
    """
    Run deterministic pattern discovery over recent tenant events.

    This endpoint currently returns candidate patterns without persisting them.
    It deliberately avoids pretending that heuristic discovery is a trained
    production pattern engine.
    """
    _require_csrf(
        request
    )

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
            days=lookback_days
        )
    )

    rows = (
        _event_query(
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

    if len(rows) < 3:
        return {
            "patterns":
                [],

            "message":
                (
                    "Not enough events "
                    "to detect patterns"
                ),

            "lookback_days":
                lookback_days,

            "event_count":
                len(rows),
        }

    candidates = []

    # ----------------------------------------------------------
    # High-risk temporal cluster
    # ----------------------------------------------------------

    seven_day_cutoff = (
        now
        - timedelta(
            days=7
        )
    )

    recent_high_risk = [
        (
            event,
            page,
            entity,
        )
        for (
            event,
            page,
            entity,
        ) in rows
        if (
            event.detected_at
            and event.detected_at
            >= seven_day_cutoff
            and _event_risk(
                event
            ) in {
                "critical",
                "high",
            }
        )
    ]

    if len(
        recent_high_risk
    ) >= 3:

        entity_names = sorted(
            {
                entity.name
                for (
                    _event,
                    _page,
                    entity,
                ) in recent_high_risk
            }
        )

        event_ids = [
            str(event.id)
            for (
                event,
                _page,
                _entity,
            ) in recent_high_risk
        ]

        significance = min(
            0.99,
            0.68
            + len(
                recent_high_risk
            ) * 0.04,
        )

        candidates.append(
            {
                "name":
                    (
                        "High-Risk Temporal "
                        "Concentration"
                    ),

                "description":
                    (
                        f"{len(recent_high_risk)} "
                        "high-risk semantic changes "
                        "were observed in the last "
                        "7 days."
                    ),

                "pattern_type":
                    "temporal",

                "event_count":
                    len(
                        recent_high_risk
                    ),

                "entity_count":
                    len(
                        entity_names
                    ),

                "event_ids":
                    event_ids,

                "entities":
                    entity_names,

                "significance_score":
                    round(
                        significance,
                        3,
                    ),

                "time_span_days":
                    7,
            }
        )

    # ----------------------------------------------------------
    # Entity activity clusters
    # ----------------------------------------------------------

    events_by_entity = {}

    for (
        event,
        page,
        entity,
    ) in rows:

        key = str(
            entity.id
        )

        if key not in events_by_entity:
            events_by_entity[
                key
            ] = {
                "entity":
                    entity,

                "events":
                    [],
            }

        events_by_entity[
            key
        ][
            "events"
        ].append(
            event
        )

    for item in (
        events_by_entity.values()
    ):
        entity = item[
            "entity"
        ]

        entity_events = item[
            "events"
        ]

        count = len(
            entity_events
        )

        if count < 3:
            continue

        ordered_events = sorted(
            entity_events,
            key=lambda event: (
                event.detected_at
                or datetime.min.replace(
                    tzinfo=timezone.utc
                )
            ),
        )

        first_event_at = (
            ordered_events[
                0
            ].detected_at
        )

        last_event_at = (
            ordered_events[
                -1
            ].detected_at
        )

        if (
            first_event_at
            and last_event_at
        ):
            time_span_days = max(
                1,
                (
                    last_event_at
                    - first_event_at
                ).days
                + 1,
            )
        else:
            time_span_days = (
                lookback_days
            )

        candidates.append(
            {
                "name":
                    (
                        f"Elevated Activity: "
                        f"{entity.name}"
                    ),

                "description":
                    (
                        f"{count} semantic changes "
                        "were detected for "
                        f"{entity.name} during the "
                        "selected temporal window."
                    ),

                "pattern_type":
                    "categorical",

                "event_count":
                    count,

                "entity_count":
                    1,

                "event_ids": [
                    str(event.id)
                    for event
                    in entity_events
                ],

                "entities": [
                    entity.name
                ],

                "significance_score":
                    round(
                        min(
                            0.95,
                            0.48
                            + count
                            * 0.045,
                        ),
                        3,
                    ),

                "time_span_days":
                    time_span_days,
            }
        )

    # ----------------------------------------------------------
    # Cross-entity semantic/category clusters
    # ----------------------------------------------------------

    category_groups = {}

    for (
        event,
        _page,
        entity,
    ) in rows:

        category = str(
            event.category
            or ""
        ).strip().lower()

        if not category:
            continue

        if (
            category
            not in category_groups
        ):
            category_groups[
                category
            ] = []

        category_groups[
            category
        ].append(
            (
                event,
                entity,
            )
        )

    for (
        category,
        category_rows,
    ) in (
        category_groups.items()
    ):

        entity_names = {
            entity.name
            for (
                _event,
                entity,
            ) in category_rows
        }

        if (
            len(category_rows) < 4
            or len(
                entity_names
            ) < 2
        ):
            continue

        ordered = sorted(
            category_rows,
            key=lambda item: (
                item[
                    0
                ].detected_at
                or datetime.min.replace(
                    tzinfo=timezone.utc
                )
            ),
        )

        first_at = (
            ordered[
                0
            ][
                0
            ].detected_at
        )

        last_at = (
            ordered[
                -1
            ][
                0
            ].detected_at
        )

        span_days = (
            max(
                1,
                (
                    last_at
                    - first_at
                ).days
                + 1,
            )
            if (
                first_at
                and last_at
            )
            else lookback_days
        )

        candidates.append(
            {
                "name":
                    (
                        f"Cross-Entity "
                        f"{category.title()} "
                        "Movement"
                    ),

                "description":
                    (
                        f"{len(category_rows)} "
                        f"{category} changes "
                        "were observed across "
                        f"{len(entity_names)} "
                        "entities."
                    ),

                "pattern_type":
                    "semantic",

                "event_count":
                    len(
                        category_rows
                    ),

                "entity_count":
                    len(
                        entity_names
                    ),

                "event_ids": [
                    str(
                        event.id
                    )
                    for (
                        event,
                        _entity,
                    ) in category_rows
                ],

                "entities":
                    sorted(
                        entity_names
                    ),

                "categories": [
                    category
                ],

                "significance_score":
                    round(
                        min(
                            0.98,
                            0.55
                            + len(
                                category_rows
                            )
                            * 0.025
                            + len(
                                entity_names
                            )
                            * 0.04,
                        ),
                        3,
                    ),

                "time_span_days":
                    span_days,
            }
        )

    candidates.sort(
        key=lambda pattern: (
            pattern[
                "significance_score"
            ],
            pattern[
                "event_count"
            ],
        ),
        reverse=True,
    )

    return {
        "patterns":
            candidates[:50],

        "message":
            (
                f"Detected "
                f"{len(candidates)} "
                "candidate patterns"
            ),

        "lookback_days":
            lookback_days,

        "event_count":
            len(rows),

        "persisted":
            False,
    }


# ---------------------------------------------------------------------------
# Anomalies
# ---------------------------------------------------------------------------

@router.get("/anomalies")
def get_anomalies(
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
    is_confirmed: Optional[
        bool
    ] = None,
    include_dismissed: bool = False,
):
    """
    Return persisted anomaly records for the current organization.
    """
    organization_id = (
        _organization_id(
            current_user
        )
    )

    query = (
        db.query(
            Anomaly,
            SemanticDriftEvent,
            MonitoredPage,
            Entity,
        )
        .outerjoin(
            SemanticDriftEvent,
            and_(
                Anomaly.event_id
                == SemanticDriftEvent.id,
                SemanticDriftEvent.organization_id
                == organization_id,
            ),
        )
        .outerjoin(
            MonitoredPage,
            and_(
                SemanticDriftEvent.monitored_page_id
                == MonitoredPage.id,
                MonitoredPage.organization_id
                == organization_id,
            ),
        )
        .outerjoin(
            Entity,
            and_(
                MonitoredPage.entity_id
                == Entity.id,
                Entity.organization_id
                == organization_id,
            ),
        )
        .filter(
            Anomaly.organization_id
            == organization_id,
        )
    )

    if not include_dismissed:
        query = query.filter(
            Anomaly.is_dismissed.is_(
                False
            )
        )

    if (
        is_confirmed
        is not None
    ):
        query = query.filter(
            Anomaly.is_confirmed
            == is_confirmed
        )

    total = (
        query
        .order_by(None)
        .count()
    )

    rows = (
        query
        .order_by(
            Anomaly.significance.desc(),
            Anomaly.detected_at.desc(),
            Anomaly.id.desc(),
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
                    str(anomaly.id),

                "event_id":
                    str(event.id),

                "entity_id":
                    str(entity.id),

                "entity_name":
                    entity.name,

                "domain":
                    entity.domain,

                "monitored_page_id":
                    str(page.id),

                "url":
                    page.url,

                "anomaly_type":
                    anomaly.anomaly_type,

                "description":
                    anomaly.description,

                "expected_value":
                    anomaly.expected_value,

                "actual_value":
                    anomaly.actual_value,

                "deviation_score":
                    anomaly.deviation_score,

                "significance":
                    anomaly.significance,

                "baseline_days":
                    anomaly.baseline_days,

                "detection_method":
                    anomaly.detection_method,

                "detected_at":
                    _serialize_datetime(
                        anomaly.detected_at
                    ),

                "reviewed_at":
                    _serialize_datetime(
                        anomaly.reviewed_at
                    ),

                "is_confirmed":
                    bool(
                        anomaly.is_confirmed
                    ),

                "is_dismissed":
                    bool(
                        anomaly.is_dismissed
                    ),
            }
            for (
                anomaly,
                event,
                page,
                entity,
            ) in rows
        ],
    }


@router.post("/anomalies/detect")
def detect_anomalies(
    request: Request,
    db: Session = Depends(
        get_db
    ),
    current_user: User = Depends(
        get_current_user
    ),
    lookback_days: int = Query(
        30,
        ge=1,
        le=365,
    ),
):
    """
    Run deterministic anomaly candidate detection.

    The current implementation is intentionally marked as heuristic and does
    not persist Anomaly records. Production anomaly baselines belong in a
    worker-driven detection engine rather than inside a request handler.
    """
    _require_csrf(
        request
    )

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
            days=lookback_days
        )
    )

    rows = (
        _event_query(
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
            1000
        )
        .all()
    )

    if not rows:
        return {
            "anomalies":
                [],

            "message":
                "No events available",

            "lookback_days":
                lookback_days,

            "persisted":
                False,
        }

    events_by_page = {}

    for (
        event,
        page,
        entity,
    ) in rows:

        key = str(
            page.id
        )

        if (
            key
            not in events_by_page
        ):
            events_by_page[
                key
            ] = {
                "page":
                    page,

                "entity":
                    entity,

                "events":
                    [],
            }

        events_by_page[
            key
        ][
            "events"
        ].append(
            event
        )

    page_counts = [
        len(
            item[
                "events"
            ]
        )
        for item
        in events_by_page.values()
    ]

    mean_count = (
        sum(
            page_counts
        )
        / max(
            1,
            len(
                page_counts
            ),
        )
    )

    variance = (
        sum(
            (
                count
                - mean_count
            ) ** 2
            for count in page_counts
        )
        / max(
            1,
            len(
                page_counts
            ),
        )
    )

    standard_deviation = (
        variance ** 0.5
    )

    frequency_threshold = max(
        5.0,
        mean_count
        + standard_deviation
        * 1.5,
    )

    candidates = []

    # ----------------------------------------------------------
    # Frequency anomaly
    # ----------------------------------------------------------

    for item in (
        events_by_page.values()
    ):
        page = item[
            "page"
        ]

        entity = item[
            "entity"
        ]

        events = item[
            "events"
        ]

        count = len(
            events
        )

        if (
            count
            <= frequency_threshold
        ):
            continue

        deviation = (
            (
                count
                - mean_count
            )
            / max(
                1.0,
                standard_deviation,
            )
        )

        deviation_score = min(
            1.0,
            max(
                0.0,
                deviation / 4.0,
            ),
        )

        significance = min(
            1.0,
            0.55
            + deviation_score
            * 0.4,
        )

        candidates.append(
            {
                "anomaly_type":
                    "frequency",

                "entity_id":
                    str(
                        entity.id
                    ),

                "entity_name":
                    entity.name,

                "monitored_page_id":
                    str(
                        page.id
                    ),

                "url":
                    page.url,

                "description":
                    (
                        f"{count} changes were "
                        f"detected for "
                        f"{entity.name} during "
                        f"the last "
                        f"{lookback_days} days."
                    ),

                "deviation_score":
                    round(
                        deviation_score,
                        3,
                    ),

                "significance":
                    round(
                        significance,
                        3,
                    ),

                "expected_value":
                    round(
                        mean_count,
                        2,
                    ),

                "actual_value":
                    float(
                        count
                    ),

                "baseline_days":
                    lookback_days,
            }
        )

    # ----------------------------------------------------------
    # Magnitude / impact anomaly
    # ----------------------------------------------------------

    impact_values = [
        float(
            event.impact_score
            or 0
        )
        for (
            event,
            _page,
            _entity,
        ) in rows
    ]

    mean_impact = (
        sum(
            impact_values
        )
        / max(
            1,
            len(
                impact_values
            ),
        )
    )

    impact_variance = (
        sum(
            (
                impact
                - mean_impact
            ) ** 2
            for impact
            in impact_values
        )
        / max(
            1,
            len(
                impact_values
            ),
        )
    )

    impact_deviation = (
        impact_variance
        ** 0.5
    )

    impact_threshold = max(
        80.0,
        mean_impact
        + impact_deviation
        * 1.5,
    )

    for (
        event,
        page,
        entity,
    ) in rows:

        impact_score = float(
            event.impact_score
            or 0
        )

        if (
            impact_score
            <= impact_threshold
        ):
            continue

        deviation = (
            (
                impact_score
                - mean_impact
            )
            / max(
                1.0,
                impact_deviation,
            )
        )

        candidates.append(
            {
                "anomaly_type":
                    "magnitude",

                "event_id":
                    str(
                        event.id
                    ),

                "entity_id":
                    str(
                        entity.id
                    ),

                "entity_name":
                    entity.name,

                "monitored_page_id":
                    str(
                        page.id
                    ),

                "url":
                    page.url,

                "description":
                    (
                        "Semantic impact exceeds "
                        "the recent tenant baseline."
                    ),

                "deviation_score":
                    round(
                        min(
                            1.0,
                            max(
                                0.0,
                                deviation
                                / 4.0,
                            ),
                        ),
                        3,
                    ),

                "significance":
                    round(
                        min(
                            1.0,
                            0.6
                            + impact_score
                            / 250.0,
                        ),
                        3,
                    ),

                "expected_value":
                    round(
                        mean_impact,
                        2,
                    ),

                "actual_value":
                    impact_score,

                "baseline_days":
                    lookback_days,
            }
        )

    candidates.sort(
        key=lambda anomaly: (
            anomaly[
                "significance"
            ],
            anomaly[
                "deviation_score"
            ],
        ),
        reverse=True,
    )

    return {
        "anomalies":
            candidates[:100],

        "message":
            (
                f"Detected "
                f"{len(candidates)} "
                "candidate anomalies"
            ),

        "lookback_days":
            lookback_days,

        "event_count":
            len(rows),

        "baseline": {
            "mean_events_per_page":
                round(
                    mean_count,
                    3,
                ),

            "event_count_stddev":
                round(
                    standard_deviation,
                    3,
                ),

            "frequency_threshold":
                round(
                    frequency_threshold,
                    3,
                ),

            "mean_impact_score":
                round(
                    mean_impact,
                    3,
                ),

            "impact_score_stddev":
                round(
                    impact_deviation,
                    3,
                ),

            "impact_threshold":
                round(
                    impact_threshold,
                    3,
                ),
        },

        "persisted":
            False,
    }


# ---------------------------------------------------------------------------
# Entity relationships
# ---------------------------------------------------------------------------

@router.get(
    "/relationships/{entity_id}"
)
def get_entity_relationships(
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
):
    """
    Return relationship evidence for one tenant-owned entity.

    Relationship strength is based on explicit `entities_affected` evidence
    recorded by SemanticDriftEvent. It is not presented as graph topology
    unless the backend has actually recorded a relationship.
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

    source_entity = (
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

    if source_entity is None:
        raise HTTPException(
            status_code=404,
            detail="Entity not found",
        )

    rows = (
        _event_query(
            db,
            organization_id,
        )
        .filter(
            Entity.id
            == parsed_entity_id
        )
        .order_by(
            SemanticDriftEvent.detected_at.desc(),
            SemanticDriftEvent.id.desc(),
        )
        .limit(
            limit
        )
        .all()
    )

    if not rows:
        return {
            "entity_id":
                str(
                    source_entity.id
                ),

            "entity_name":
                source_entity.name,

            "event_count":
                0,

            "related_entities":
                [],

            "categories":
                [],
        }

    relationship_counts = (
        Counter()
    )

    category_counts = (
        Counter()
    )

    for (
        event,
        _page,
        _entity,
    ) in rows:

        if event.category:
            category_counts[
                event.category
            ] += 1

        affected = (
            event.entities_affected
            or []
        )

        if not isinstance(
            affected,
            list,
        ):
            continue

        for affected_id in affected:
            candidate = str(
                affected_id
            ).strip()

            if (
                not candidate
                or candidate
                == str(
                    source_entity.id
                )
            ):
                continue

            try:
                canonical = str(
                    uuid.UUID(
                        candidate
                    )
                )

            except (
                ValueError,
                TypeError,
                AttributeError,
            ):
                continue

            relationship_counts[
                canonical
            ] += 1

    if not relationship_counts:
        return {
            "entity_id":
                str(
                    source_entity.id
                ),

            "entity_name":
                source_entity.name,

            "event_count":
                len(rows),

            "related_entities":
                [],

            "categories": [
                {
                    "category":
                        category,

                    "count":
                        count,
                }
                for (
                    category,
                    count,
                ) in (
                    category_counts
                    .most_common()
                )
            ],
        }

    related_ids = [
        uuid.UUID(
            entity_id_value
        )
        for entity_id_value
        in relationship_counts
    ]

    related_entities = (
        db.query(
            Entity
        )
        .filter(
            Entity.organization_id
            == organization_id,
            Entity.id.in_(
                related_ids
            ),
        )
        .all()
    )

    entity_map = {
        str(entity.id):
            entity
        for entity
        in related_entities
    }

    relationships = []

    for (
        related_id,
        shared_count,
    ) in (
        relationship_counts
        .most_common()
    ):
        entity = (
            entity_map.get(
                related_id
            )
        )

        if entity is None:
            continue

        relationships.append(
            {
                "entity_id":
                    str(
                        entity.id
                    ),

                "entity_name":
                    entity.name,

                "domain":
                    entity.domain,

                "shared_changes":
                    shared_count,

                "strength":
                    round(
                        min(
                            1.0,
                            shared_count
                            / max(
                                1,
                                len(rows),
                            ),
                        ),
                        4,
                    ),
            }
        )

    return {
        "entity_id":
            str(
                source_entity.id
            ),

        "entity_name":
            source_entity.name,

        "domain":
            source_entity.domain,

        "event_count":
            len(rows),

        "related_entities":
            relationships,

        "categories": [
            {
                "category":
                    category,

                "count":
                    count,
            }
            for (
                category,
                count,
            ) in (
                category_counts
                .most_common()
            )
        ],
    }
# ---------------------------------------------------------------------------
# Watchlists
# ---------------------------------------------------------------------------

def _serialize_watchlist(
    watchlist: Watchlist,
    *,
    entities: Optional[
        list[dict]
    ] = None,
) -> dict:
    entity_ids = (
        watchlist.entity_ids
        or []
    )

    return {
        "id":
            str(
                watchlist.id
            ),

        "name":
            watchlist.name,

        "description":
            watchlist.description,

        "entity_ids":
            entity_ids,

        "page_ids":
            watchlist.page_ids
            or [],

        "categories":
            watchlist.categories
            or [],

        "entities":
            (
                entities
                if entities is not None
                else []
            ),

        "entity_count":
            len(
                entity_ids
            ),

        "alert_on_high_risk":
            bool(
                watchlist.alert_on_high_risk
            ),

        "alert_on_medium_risk":
            bool(
                watchlist.alert_on_medium_risk
            ),

        "alert_on_pattern":
            bool(
                watchlist.alert_on_pattern
            ),

        "alert_on_anomaly":
            bool(
                watchlist.alert_on_anomaly
            ),

        "is_active":
            bool(
                watchlist.is_active
            ),

        "is_archived":
            bool(
                watchlist.is_archived
            ),

        "last_checked_at":
            _serialize_datetime(
                watchlist.last_checked_at
            ),

        "created_at":
            _serialize_datetime(
                watchlist.created_at
            ),

        "updated_at":
            _serialize_datetime(
                watchlist.updated_at
            ),
    }

@router.get("/watchlist")
def get_watchlists(
    db: Session = Depends(
        get_db
    ),
    current_user: User = Depends(
        get_current_user
    ),
    include_archived: bool = False,
):
    """
    Return watchlists owned by the authenticated organization.

    Entity metadata and recent activity are resolved in bulk to avoid the
    per-entity N+1 query pattern used by the original implementation.
    """
    organization_id = (
        _organization_id(
            current_user
        )
    )

    query = (
        db.query(
            Watchlist
        )
        .filter(
            Watchlist.organization_id
            == organization_id
        )
    )

    if not include_archived:
        query = query.filter(
            Watchlist.is_active.is_(
                True
            ),
            Watchlist.is_archived.is_(
                False
            ),
        )

    watchlists = (
        query
        .order_by(
            Watchlist.created_at.desc(),
            Watchlist.id.desc(),
        )
        .all()
    )

    if not watchlists:
        return {
            "data": []
        }

    entity_id_strings = {
        str(entity_id)
        for watchlist in watchlists
        for entity_id in (
            watchlist.entity_ids
            or []
        )
        if entity_id
    }

    parsed_entity_ids = []

    for entity_id in (
        entity_id_strings
    ):
        try:
            parsed_entity_ids.append(
                uuid.UUID(
                    entity_id
                )
            )

        except (
            ValueError,
            TypeError,
            AttributeError,
        ):
            continue

    entity_map = {}

    if parsed_entity_ids:
        entities = (
            db.query(
                Entity
            )
            .filter(
                Entity.organization_id
                == organization_id,
                Entity.id.in_(
                    parsed_entity_ids
                ),
            )
            .all()
        )

        entity_map = {
            str(entity.id):
                entity
            for entity in entities
        }

    recent_cutoff = (
        datetime.now(
            timezone.utc
        )
        - timedelta(
            days=30
        )
    )

    recent_counts = {}

    if parsed_entity_ids:
        count_rows = (
            db.query(
                Entity.id,
                func.count(
                    SemanticDriftEvent.id
                ).label(
                    "event_count"
                ),
            )
            .join(
                MonitoredPage,
                (
                    MonitoredPage.entity_id
                    == Entity.id
                ),
            )
            .join(
                SemanticDriftEvent,
                (
                    SemanticDriftEvent.monitored_page_id
                    == MonitoredPage.id
                ),
            )
            .filter(
                Entity.organization_id
                == organization_id,
                MonitoredPage.organization_id
                == organization_id,
                SemanticDriftEvent.organization_id
                == organization_id,
                Entity.id.in_(
                    parsed_entity_ids
                ),
                SemanticDriftEvent.detected_at
                >= recent_cutoff,
            )
            .group_by(
                Entity.id
            )
            .all()
        )

        recent_counts = {
            str(entity_id):
                int(
                    event_count
                    or 0
                )
            for (
                entity_id,
                event_count,
            ) in count_rows
        }

    data = []

    for watchlist in watchlists:
        entities = []

        for entity_id in (
            watchlist.entity_ids
            or []
        ):
            key = str(
                entity_id
            )

            entity = (
                entity_map.get(
                    key
                )
            )

            if entity is None:
                continue

            entities.append(
                {
                    "id":
                        str(entity.id),

                    "name":
                        entity.name,

                    "domain":
                        entity.domain,

                    "category":
                        entity.category,

                    "recent_changes":
                        recent_counts.get(
                            key,
                            0,
                        ),
                }
            )

        data.append(
            _serialize_watchlist(
                watchlist,
                entities=entities,
            )
        )

    return {
        "data":
            data
    }

def _watchlist_alert_policy_state(
    watchlist: Watchlist,
) -> tuple:
    """
    Return the persisted Watchlist state that can change Alert eligibility.

    Descriptive metadata and operational timestamps are intentionally
    excluded because they do not affect candidate policy.
    """
    return (
        tuple(
            watchlist.entity_ids
            or []
        ),
        tuple(
            watchlist.page_ids
            or []
        ),
        tuple(
            watchlist.categories
            or []
        ),
        bool(
            watchlist.alert_on_high_risk
        ),
        bool(
            watchlist.alert_on_medium_risk
        ),
        bool(
            watchlist.alert_on_pattern
        ),
        bool(
            watchlist.alert_on_anomaly
        ),
        bool(
            watchlist.is_active
        ),
        bool(
            watchlist.is_archived
        ),
    )


@router.post("/watchlist")
def create_watchlist(
    body: WatchlistCreateRequest,
    request: Request,
    db: Session = Depends(
        get_db
    ),
    current_user: User = Depends(
        get_current_user
    ),
):
    """
    Create a tenant-owned watchlist.

    IDs stored in JSON columns are canonical UUID strings. UUID objects are
    intentionally not written to JSON fields because they are not guaranteed
    to be JSON serializable across database drivers.
    """
    _require_csrf(
        request
    )

    organization_id = (
        _organization_id(
            current_user
        )
    )

    name = (
        body.name.strip()
    )

    if not name:
        raise HTTPException(
            status_code=422,
            detail=(
                "Watchlist name is required"
            ),
        )

    entity_ids = (
        _validate_entity_ids(
            db,
            organization_id,
            body.entity_ids,
        )
    )

    page_ids = (
        _validate_page_ids(
            db,
            organization_id,
            body.page_ids,
        )
    )

    categories = (
        _normalize_watchlist_categories(
            body.categories
        )
    )

    watchlist = Watchlist(
        organization_id=organization_id,
        name=name,
        description=(
            body.description.strip()
            if (
                body.description
                and body.description.strip()
            )
            else None
        ),
        entity_ids=entity_ids,
        page_ids=page_ids,
        categories=categories,
        alert_on_high_risk=(
            body.alert_on_high_risk
        ),
        alert_on_medium_risk=(
            body.alert_on_medium_risk
        ),
        alert_on_pattern=(
            body.alert_on_pattern
        ),
        alert_on_anomaly=(
            body.alert_on_anomaly
        ),
        is_active=True,
        is_archived=False,
    )

    db.add(
        watchlist
    )

    bump_alert_policy_revision(
        db,
        organization_id=organization_id,
    )

    db.commit()
    db.refresh(
        watchlist
    )

    return _serialize_watchlist(
        watchlist
    )

@router.patch(
    "/watchlist/{watchlist_id}"
)
def update_watchlist(
    watchlist_id: str,
    body: WatchlistUpdateRequest,
    request: Request,
    db: Session = Depends(
        get_db
    ),
    current_user: User = Depends(
        get_current_user
    ),
):
    """
    Partially update an active, tenant-owned watchlist.

    Archived watchlists are immutable. Fields omitted from the request retain
    their persisted values, including boolean policy fields explicitly set to
    false.
    """
    _require_csrf(
        request
    )

    organization_id = (
        _organization_id(
            current_user
        )
    )

    parsed_watchlist_id = (
        _parse_uuid(
            watchlist_id,
            resource="Watchlist",
        )
    )

    watchlist = (
        db.query(
            Watchlist
        )
        .filter(
            Watchlist.id
            == parsed_watchlist_id,
            Watchlist.organization_id
            == organization_id,
        )
        .first()
    )

    if watchlist is None:
        raise HTTPException(
            status_code=404,
            detail="Watchlist not found",
        )

    if watchlist.is_archived:
        raise HTTPException(
            status_code=409,
            detail=(
                "Archived watchlists "
                "cannot be modified"
            ),
        )

    fields_set = (
        body.model_fields_set
    )

    if not fields_set:
        raise HTTPException(
            status_code=422,
            detail=(
                "At least one watchlist "
                "field is required"
            ),
        )

    policy_state_before = (
        _watchlist_alert_policy_state(
            watchlist
        )
    )

    if "name" in fields_set:
        name = (
            body.name.strip()
            if body.name
            else ""
        )

        if not name:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Watchlist name "
                    "is required"
                ),
            )

        watchlist.name = name

    if "description" in fields_set:
        watchlist.description = (
            body.description.strip()
            if (
                body.description
                and body.description.strip()
            )
            else None
        )

    if "entity_ids" in fields_set:
        if body.entity_ids is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "entity_ids may "
                    "not be null"
                ),
            )

        watchlist.entity_ids = (
            _validate_entity_ids(
                db,
                organization_id,
                body.entity_ids,
            )
        )

    if "page_ids" in fields_set:
        if body.page_ids is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "page_ids may "
                    "not be null"
                ),
            )

        watchlist.page_ids = (
            _validate_page_ids(
                db,
                organization_id,
                body.page_ids,
            )
        )

    if "categories" in fields_set:
        if body.categories is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    "categories may "
                    "not be null"
                ),
            )

        watchlist.categories = (
            _normalize_watchlist_categories(
                body.categories
            )
        )

    policy_fields = (
        "alert_on_high_risk",
        "alert_on_medium_risk",
        "alert_on_pattern",
        "alert_on_anomaly",
        "is_active",
    )

    for field_name in policy_fields:
        if field_name not in fields_set:
            continue

        value = getattr(
            body,
            field_name,
        )

        if value is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{field_name} may "
                    "not be null"
                ),
            )

        setattr(
            watchlist,
            field_name,
            value,
        )

    policy_state_after = (
        _watchlist_alert_policy_state(
            watchlist
        )
    )

    if (
        policy_state_after
        != policy_state_before
    ):
        bump_alert_policy_revision(
            db,
            organization_id=organization_id,
        )

    db.commit()
    db.refresh(
        watchlist
    )

    return _serialize_watchlist(
        watchlist
    )

@router.delete(
    "/watchlist/{watchlist_id}"
)
def archive_watchlist(
    watchlist_id: str,
    request: Request,
    db: Session = Depends(
        get_db
    ),
    current_user: User = Depends(
        get_current_user
    ),
):
    """
    Archive a watchlist without destroying historical configuration.
    """
    _require_csrf(
        request
    )

    organization_id = (
        _organization_id(
            current_user
        )
    )

    parsed_watchlist_id = (
        _parse_uuid(
            watchlist_id,
            resource="Watchlist",
        )
    )

    watchlist = (
        db.query(
            Watchlist
        )
        .filter(
            Watchlist.id
            == parsed_watchlist_id,
            Watchlist.organization_id
            == organization_id,
        )
        .first()
    )

    if watchlist is None:
        raise HTTPException(
            status_code=404,
            detail="Watchlist not found",
        )

    policy_state_before = (
        _watchlist_alert_policy_state(
            watchlist
        )
    )

    watchlist.is_active = False
    watchlist.is_archived = True

    policy_state_after = (
        _watchlist_alert_policy_state(
            watchlist
        )
    )

    if (
        policy_state_after
        != policy_state_before
    ):
        bump_alert_policy_revision(
            db,
            organization_id=organization_id,
        )

    db.commit()

    return {
        "ok":
            True,

        "id":
            str(
                watchlist.id
            ),
    }


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

@router.get("/alerts")
def get_alerts(
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
    severity: Optional[str] = None,
    status: Optional[str] = None,
):
    """
    Return tenant-scoped alerts with authorized event context.
    """
    organization_id = (
        _organization_id(
            current_user
        )
    )

    query = (
        db.query(
            Alert,
            SemanticDriftEvent,
            MonitoredPage,
            Entity,
        )
        .outerjoin(
            SemanticDriftEvent,
            and_(
                Alert.event_id
                == SemanticDriftEvent.id,
                SemanticDriftEvent.organization_id
                == organization_id,
            ),
        )
        .outerjoin(
            MonitoredPage,
            and_(
                SemanticDriftEvent.monitored_page_id
                == MonitoredPage.id,
                MonitoredPage.organization_id
                == organization_id,
            ),
        )
        .outerjoin(
            Entity,
            and_(
                MonitoredPage.entity_id
                == Entity.id,
                Entity.organization_id
                == organization_id,
            ),
        )
        .filter(
            Alert.organization_id
            == organization_id,
        )
    )

    if severity:
        normalized_severity = (
            severity
            .strip()
            .lower()
        )

        if normalized_severity:
            query = query.filter(
                func.lower(
                    Alert.severity
                )
                == normalized_severity
            )

    if status:
        normalized_status = (
            status
            .strip()
            .lower()
        )

        if normalized_status:
            query = query.filter(
                func.lower(
                    Alert.status
                )
                == normalized_status
            )

    total = (
        query
        .order_by(None)
        .count()
    )

    rows = (
        query
        .order_by(
            Alert.created_at.desc(),
            Alert.id.desc(),
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
                    str(
                        alert.id
                    ),

                "event_id":
                    (
                        str(
                            event.id
                        )
                        if event is not None
                        else None
                    ),

                "entity_id":
                    (
                        str(
                            entity.id
                        )
                        if entity is not None
                        else None
                    ),

                "entity_name":
                    (
                        entity.name
                        if entity is not None
                        else None
                    ),

                "domain":
                    (
                        entity.domain
                        if entity is not None
                        else None
                    ),

                "url":
                    (
                        page.url
                        if page is not None
                        else None
                    ),

                "page_type":
                    (
                        page.page_type
                        if page is not None
                        else None
                    ),

                "alert_type":
                    alert.alert_type,

                "source_type":
                    alert.source_type,

                "source_id":
                    (
                        str(
                            alert.source_id
                        )
                        if alert.source_id is not None
                        else None
                    ),

                "occurred_at":
                    _serialize_datetime(
                        alert.occurred_at
                    ),

                "title":
                    alert.title,

                "message":
                    alert.message,

                "severity":
                    alert.severity,

                "status":
                    alert.status,

                "recommendation":
                    alert.recommendation,

                "affected_entities":
                    alert.affected_entities
                    or [],

                "sent_to":
                    alert.sent_to
                    or [],

                "created_at":
                    _serialize_datetime(
                        alert.created_at
                    ),

                "updated_at":
                    _serialize_datetime(
                        alert.updated_at
                    ),

                "sent_at":
                    _serialize_datetime(
                        alert.sent_at
                    ),

                "read_at":
                    _serialize_datetime(
                        alert.read_at
                    ),

                "dismissed_at":
                    _serialize_datetime(
                        alert.dismissed_at
                    ),
            }
            for (
                alert,
                event,
                page,
                entity,
            ) in rows
        ],
    }


# ---------------------------------------------------------------------------
# Universal temporal search
# ---------------------------------------------------------------------------

@router.get("/search")
def temporal_search(
    q: str = Query(
        min_length=1,
        max_length=200,
    ),
    db: Session = Depends(
        get_db
    ),
    current_user: User = Depends(
        get_current_user
    ),
    limit: int = Query(
        20,
        ge=1,
        le=100,
    ),
):
    """
    Search entities and semantic drift events inside the current tenant.

    Results are intentionally heterogeneous because this endpoint is designed
    for command-palette navigation rather than a relational table.
    """
    organization_id = (
        _organization_id(
            current_user
        )
    )

    query_text = (
        q.strip()
    )

    if not query_text:
        raise HTTPException(
            status_code=422,
            detail=(
                "Search query is required"
            ),
        )

    pattern = (
        f"%{query_text}%"
    )

    entity_limit = min(
        limit,
        20,
    )

    entity_rows = (
        db.query(
            Entity,
            func.count(
                SemanticDriftEvent.id
            ).label(
                "event_count"
            ),
        )
        .outerjoin(
            MonitoredPage,
            (
                MonitoredPage.entity_id
                == Entity.id
            )
            & (
                MonitoredPage.organization_id
                == organization_id
            ),
        )
        .outerjoin(
            SemanticDriftEvent,
            (
                SemanticDriftEvent.monitored_page_id
                == MonitoredPage.id
            )
            & (
                SemanticDriftEvent.organization_id
                == organization_id
            ),
        )
        .filter(
            Entity.organization_id
            == organization_id,
            or_(
                Entity.name.ilike(
                    pattern
                ),
                Entity.domain.ilike(
                    pattern
                ),
                Entity.category.ilike(
                    pattern
                ),
            ),
        )
        .group_by(
            Entity.id
        )
        .order_by(
            func.count(
                SemanticDriftEvent.id
            ).desc(),
            Entity.name.asc(),
        )
        .limit(
            entity_limit
        )
        .all()
    )

    results = [
        {
            "type":
                "entity",

            "id":
                str(
                    entity.id
                ),

            "name":
                entity.name,

            "domain":
                entity.domain,

            "category":
                entity.category,

            "event_count":
                int(
                    event_count
                    or 0
                ),
        }
        for (
            entity,
            event_count,
        ) in entity_rows
    ]

    remaining = max(
        0,
        limit
        - len(
            results
        ),
    )

    if remaining > 0:
        event_rows = (
            _event_query(
                db,
                organization_id,
            )
            .filter(
                or_(
                    SemanticDriftEvent.summary_text.ilike(
                        pattern
                    ),
                    SemanticDriftEvent.category.ilike(
                        pattern
                    ),
                    Entity.name.ilike(
                        pattern
                    ),
                    Entity.domain.ilike(
                        pattern
                    ),
                    MonitoredPage.url.ilike(
                        pattern
                    ),
                )
            )
            .order_by(
                SemanticDriftEvent.detected_at.desc(),
                SemanticDriftEvent.id.desc(),
            )
            .limit(
                remaining
            )
            .all()
        )

        for (
            event,
            page,
            entity,
        ) in event_rows:

            summary = (
                event.summary_text
                or ""
            )

            if len(summary) > 200:
                summary = (
                    f"{summary[:200]}..."
                )

            results.append(
                {
                    "type":
                        "event",

                    "id":
                        str(
                            event.id
                        ),

                    "entity_id":
                        str(
                            entity.id
                        ),

                    "entity":
                        entity.name,

                    "domain":
                        entity.domain,

                    "url":
                        page.url,

                    "page_type":
                        page.page_type,

                    "summary":
                        summary,

                    "risk":
                        _event_risk(
                            event
                        ),

                    "category":
                        event.category,

                    "detected_at":
                        _serialize_datetime(
                            event.detected_at
                        ),
                }
            )

    return {
        "query":
            query_text,

        "total":
            len(
                results
            ),

        "data":
            results[
                :limit
            ],
    }
