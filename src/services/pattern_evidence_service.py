"""
Database evidence loader for deterministic Pattern Detection v1.

This service is read-only. It converts persisted tenant-scoped temporal
evidence into the pure inputs consumed by pattern_detection_service.

Temporal rules:
- semantic events use effective time because they do not have created_at;
- Risk v2 uses assessed_at <= as_of for temporal eligibility;
- Risk v2 materialization visibility is bounded by
  (created_at, id) <= (knowledge_cutoff, knowledge_cutoff_record_id)
  when a record-id cutoff is supplied;
- callers that omit knowledge_cutoff_record_id retain the legacy
  timestamp-only created_at <= knowledge_cutoff behavior;
- topology shifts require both observed_at <= as_of and created_at <= as_of.

The separate effective-time and materialization-time filters prevent
backfilled derived evidence from leaking into earlier detection cursors.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import (
    datetime,
    timedelta,
    timezone,
)
import uuid

from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from src.models.entity import Entity
from src.models.monitored_page import MonitoredPage
from src.models.risk_assessment import RiskAssessment
from src.models.semantic_drift_event import (
    SemanticDriftEvent,
)
from src.models.topology_shift_snapshot import (
    TopologyShiftSnapshot,
)
from src.services.pattern_detection_service import (
    PatternEventInput,
    RiskAssessmentInput,
    TopologyShiftInput,
)


from src.services.risk_version import RISK_VERSION_V2


class PatternEvidenceValidationError(
    ValueError
):
    pass


@dataclass(frozen=True)
class PatternEvidenceBundle:
    organization_id: uuid.UUID
    as_of: datetime
    window_start: datetime
    lookback_days: int
    events: tuple[
        PatternEventInput,
        ...
    ]
    risk_assessments: tuple[
        RiskAssessmentInput,
        ...
    ]
    topology_shifts: tuple[
        TopologyShiftInput,
        ...
    ]


def _as_uuid(
    value,
    *,
    field_name: str,
) -> uuid.UUID:
    if isinstance(
        value,
        uuid.UUID,
    ):
        return value

    try:
        return uuid.UUID(
            str(value)
        )

    except (
        TypeError,
        ValueError,
        AttributeError,
    ) as exc:
        raise (
            PatternEvidenceValidationError(
                f"{field_name} must be a valid UUID"
            )
        ) from exc


def _normalize_cursor(
    value: datetime,
) -> datetime:
    if not isinstance(
        value,
        datetime,
    ):
        raise PatternEvidenceValidationError(
            "as_of must be a datetime"
        )

    if value.tzinfo is None:
        return value.replace(
            tzinfo=timezone.utc
        )

    return value.astimezone(
        timezone.utc
    )


def _validate_lookback_days(
    value: int,
) -> int:
    if (
        isinstance(
            value,
            bool,
        )
        or not isinstance(
            value,
            int,
        )
        or value < 1
        or value > 365
    ):
        raise PatternEvidenceValidationError(
            "lookback_days must be an integer between 1 and 365"
        )

    return value


def _load_event_evidence(
    db: Session,
    *,
    organization_id: uuid.UUID,
    window_start: datetime,
    as_of: datetime,
) -> tuple[
    PatternEventInput,
    ...
]:
    rows = (
        db.query(
            SemanticDriftEvent.id,
            MonitoredPage.entity_id,
            SemanticDriftEvent.detected_at,
        )
        .join(
            MonitoredPage,
            MonitoredPage.id
            == SemanticDriftEvent.monitored_page_id,
        )
        .filter(
            SemanticDriftEvent.organization_id
            == organization_id,
            MonitoredPage.organization_id
            == organization_id,
            SemanticDriftEvent.detected_at
            >= window_start,
            SemanticDriftEvent.detected_at
            <= as_of,
        )
        .order_by(
            SemanticDriftEvent.detected_at.asc(),
            SemanticDriftEvent.id.asc(),
        )
        .all()
    )

    return tuple(
        PatternEventInput(
            event_id=row[0],
            entity_id=row[1],
            occurred_at=row[2],
        )
        for row in rows
    )


def _load_risk_evidence(
    db: Session,
    *,
    organization_id: uuid.UUID,
    window_start: datetime,
    as_of: datetime,
    knowledge_cutoff: datetime,
    knowledge_cutoff_record_id: uuid.UUID | None,
) -> tuple[
    RiskAssessmentInput,
    ...
]:
    if knowledge_cutoff_record_id is None:
        materialization_boundary = (
            RiskAssessment.created_at
            <= knowledge_cutoff
        )
    else:
        materialization_boundary = or_(
            RiskAssessment.created_at
            < knowledge_cutoff,
            and_(
                RiskAssessment.created_at
                == knowledge_cutoff,
                RiskAssessment.id
                <= knowledge_cutoff_record_id,
            ),
        )

    rows = (
        db.query(
            RiskAssessment.semantic_drift_event_id,
            MonitoredPage.entity_id,
            RiskAssessment.assessed_at,
            RiskAssessment.score,
        )
        .join(
            SemanticDriftEvent,
            SemanticDriftEvent.id
            == RiskAssessment.semantic_drift_event_id,
        )
        .join(
            MonitoredPage,
            MonitoredPage.id
            == SemanticDriftEvent.monitored_page_id,
        )
        .filter(
            RiskAssessment.organization_id
            == organization_id,
            SemanticDriftEvent.organization_id
            == organization_id,
            MonitoredPage.organization_id
            == organization_id,
            # Historical Risk v2 rows may predate semantic readiness gates.
            # Do not promote those assessments into new escalation patterns.
            SemanticDriftEvent.analysis_status
            == "completed",
            SemanticDriftEvent.vector_analysis_status
            == "completed",
            RiskAssessment.semantic_drift_event_id.isnot(
                None
            ),
            RiskAssessment.risk_version
            == RISK_VERSION_V2,
            RiskAssessment.assessed_at
            >= window_start,
            RiskAssessment.assessed_at
            <= as_of,
            materialization_boundary,
        )
        .order_by(
            RiskAssessment.assessed_at.asc(),
            RiskAssessment.semantic_drift_event_id.asc(),
            RiskAssessment.id.asc(),
        )
        .all()
    )

    return tuple(
        RiskAssessmentInput(
            event_id=row[0],
            entity_id=row[1],
            occurred_at=row[2],
            score=float(
                row[3]
            ),
        )
        for row in rows
    )


def _load_topology_evidence(
    db: Session,
    *,
    organization_id: uuid.UUID,
    window_start: datetime,
    as_of: datetime,
) -> tuple[
    TopologyShiftInput,
    ...
]:
    rows = (
        db.query(
            TopologyShiftSnapshot.semantic_drift_event_id,
            TopologyShiftSnapshot.entity_id,
            TopologyShiftSnapshot.observed_at,
            TopologyShiftSnapshot.classification,
            TopologyShiftSnapshot.shift_score,
        )
        .join(
            Entity,
            Entity.id
            == TopologyShiftSnapshot.entity_id,
        )
        .filter(
            TopologyShiftSnapshot.organization_id
            == organization_id,
            Entity.organization_id
            == organization_id,
            TopologyShiftSnapshot.semantic_drift_event_id.isnot(
                None
            ),
            TopologyShiftSnapshot.observed_at
            >= window_start,
            TopologyShiftSnapshot.observed_at
            <= as_of,
            TopologyShiftSnapshot.created_at
            <= as_of,
        )
        .order_by(
            TopologyShiftSnapshot.observed_at.asc(),
            TopologyShiftSnapshot.semantic_drift_event_id.asc(),
            TopologyShiftSnapshot.entity_id.asc(),
            TopologyShiftSnapshot.id.asc(),
        )
        .all()
    )

    return tuple(
        TopologyShiftInput(
            event_id=row[0],
            entity_id=row[1],
            observed_at=row[2],
            classification=str(
                row[3]
            ),
            shift_score=float(
                row[4]
            ),
        )
        for row in rows
    )


def load_pattern_evidence(
    db: Session,
    *,
    organization_id,
    as_of: datetime,
    knowledge_cutoff: datetime | None = None,
    knowledge_cutoff_record_id=None,
    lookback_days: int = 30,
) -> PatternEvidenceBundle:
    """
    Load all deterministic Pattern v1 evidence for one tenant and
    one knowledge-time cursor.

    No commit, flush, or mutation is performed.
    """
    normalized_organization_id = (
        _as_uuid(
            organization_id,
            field_name="organization_id",
        )
    )

    normalized_as_of = (
        _normalize_cursor(
            as_of
        )
    )

    normalized_knowledge_cutoff = (
        normalized_as_of
        if knowledge_cutoff is None
        else _normalize_cursor(
            knowledge_cutoff
        )
    )

    normalized_knowledge_cutoff_record_id = (
        None
        if knowledge_cutoff_record_id is None
        else _as_uuid(
            knowledge_cutoff_record_id,
            field_name=(
                "knowledge_cutoff_record_id"
            ),
        )
    )

    normalized_lookback = (
        _validate_lookback_days(
            lookback_days
        )
    )

    window_start = (
        normalized_as_of
        - timedelta(
            days=normalized_lookback
        )
    )

    return PatternEvidenceBundle(
        organization_id=(
            normalized_organization_id
        ),
        as_of=normalized_as_of,
        window_start=window_start,
        lookback_days=(
            normalized_lookback
        ),
        events=_load_event_evidence(
            db,
            organization_id=(
                normalized_organization_id
            ),
            window_start=window_start,
            as_of=normalized_as_of,
        ),
        risk_assessments=(
            _load_risk_evidence(
                db,
                organization_id=(
                    normalized_organization_id
                ),
                window_start=window_start,
                as_of=normalized_as_of,
                knowledge_cutoff=(
                    normalized_knowledge_cutoff
                ),
                knowledge_cutoff_record_id=(
                    normalized_knowledge_cutoff_record_id
                ),
            )
        ),
        topology_shifts=(
            _load_topology_evidence(
                db,
                organization_id=(
                    normalized_organization_id
                ),
                window_start=window_start,
                as_of=normalized_as_of,
            )
        ),
    )