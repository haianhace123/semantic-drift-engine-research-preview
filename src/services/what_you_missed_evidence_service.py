"""
Tenant-scoped evidence loader for What You Missed v1.

This module builds a read-only evidence bundle over a semantic-event window.

Temporal semantics
------------------
The primary missed-event window is:

    since < SemanticDriftEvent.detected_at <= until

Derived evidence is attached only to events from that window and must be
known by ``until``:

- Risk v2:
    assessed_at <= until
    created_at <= until

- Topology shift:
    observed_at <= until
    created_at <= until

- Pattern:
    detected_at <= until
    and its persisted event_ids intersect the missed-event set

- Anomaly:
    detected_at <= until
    and its anchor event belongs to the missed-event set

SemanticDriftEvent currently has no dedicated created_at field, so
detected_at remains the strongest persisted chronology for the event itself.

This service is intentionally:
- deterministic;
- tenant scoped;
- read only;
- free of LLM/API calls;
- free of commit/flush/mutation behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import (
    datetime,
    timezone,
)
import uuid

from sqlalchemy.orm import Session

from src.models.anomaly import Anomaly
from src.models.entity import Entity
from src.models.monitored_page import MonitoredPage
from src.models.pattern import Pattern
from src.services.persisted_pattern_qualification_service import (
    qualified_pattern_ids,
)
from src.models.risk_assessment import RiskAssessment
from src.models.semantic_drift_event import (
    SemanticDriftEvent,
)
from src.models.topology_shift_snapshot import (
    TopologyShiftSnapshot,
)


from src.services.risk_version import RISK_VERSION_V2


class WhatYouMissedEvidenceValidationError(
    ValueError
):
    """Raised when evidence inputs or persisted evidence are invalid."""


@dataclass(frozen=True)
class MissedEventEvidence:
    event_id: uuid.UUID
    monitored_page_id: uuid.UUID
    entity_id: uuid.UUID

    entity_name: str
    entity_domain: str | None
    page_url: str
    page_type: str

    occurred_at: datetime

    category: str | None
    change_type: str | None
    summary_text: str | None

    semantic_magnitude: float | None
    impact_score: int
    confidence: float | None

    risk_level: str

    is_pattern_part: bool
    is_anomaly: bool


@dataclass(frozen=True)
class MissedRiskEvidence:
    assessment_id: uuid.UUID
    event_id: uuid.UUID

    assessed_at: datetime
    created_at: datetime

    score: float

    semantic_magnitude_contribution: float
    legacy_impact_contribution: float
    semantic_severity_contribution: float
    page_context_contribution: float
    confidence_contribution: float

    topology_score: float
    topology_contribution: float

    topology_shift_component: float
    topology_centrality_component: float
    topology_downstream_component: float

    has_topology_signal: bool
    has_previous_topology_state: bool


@dataclass(frozen=True)
class MissedTopologyEvidence:
    topology_shift_id: uuid.UUID
    event_id: uuid.UUID
    entity_id: uuid.UUID

    observed_at: datetime
    created_at: datetime
    previous_observed_at: datetime | None

    classification: str
    severity: str
    shift_score: float

    entity_count_delta: int
    active_edge_count_delta: int

    in_degree_delta: int
    out_degree_delta: int
    degree_delta: int

    degree_centrality_delta: float

    downstream_reach_delta: int
    downstream_reach_ratio_delta: float


@dataclass(frozen=True)
class MissedPatternEvidence:
    pattern_id: uuid.UUID

    pattern_type: str
    detection_method: str | None

    event_ids: tuple[
        uuid.UUID,
        ...
    ]

    matched_event_ids: tuple[
        uuid.UUID,
        ...
    ]

    event_count: int
    entity_count: int
    time_span_days: int

    significance_score: float

    first_event_at: datetime | None
    last_event_at: datetime | None
    detected_at: datetime

    name: str
    description: str | None
    summary: str | None


@dataclass(frozen=True)
class MissedAnomalyEvidence:
    anomaly_id: uuid.UUID
    event_id: uuid.UUID

    anomaly_type: str
    detection_method: str | None

    expected_value: float | None
    actual_value: float | None

    deviation_score: float
    significance: float

    baseline_days: int
    window_days: int

    detected_at: datetime

    description: str | None


@dataclass(frozen=True)
class WhatYouMissedEvidenceBundle:
    organization_id: uuid.UUID

    since: datetime
    until: datetime

    events: tuple[
        MissedEventEvidence,
        ...
    ]

    risks: tuple[
        MissedRiskEvidence,
        ...
    ]

    topology: tuple[
        MissedTopologyEvidence,
        ...
    ]

    patterns: tuple[
        MissedPatternEvidence,
        ...
    ]

    anomalies: tuple[
        MissedAnomalyEvidence,
        ...
    ]

    @property
    def event_count(
        self,
    ) -> int:
        return len(
            self.events
        )

    @property
    def risk_count(
        self,
    ) -> int:
        return len(
            self.risks
        )

    @property
    def topology_count(
        self,
    ) -> int:
        return len(
            self.topology
        )

    @property
    def pattern_count(
        self,
    ) -> int:
        return len(
            self.patterns
        )

    @property
    def anomaly_count(
        self,
    ) -> int:
        return len(
            self.anomalies
        )


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
            str(
                value
            )
        )
    except (
        TypeError,
        ValueError,
        AttributeError,
    ) as exc:
        raise (
            WhatYouMissedEvidenceValidationError(
                f"{field_name} must be a valid UUID."
            )
        ) from exc


def _normalize_datetime(
    value,
    *,
    field_name: str,
) -> datetime:
    if not isinstance(
        value,
        datetime,
    ):
        raise (
            WhatYouMissedEvidenceValidationError(
                f"{field_name} must be a datetime."
            )
        )

    if value.tzinfo is None:
        return value.replace(
            tzinfo=timezone.utc
        )

    return value.astimezone(
        timezone.utc
    )


def _normalize_window(
    *,
    since: datetime,
    until: datetime,
) -> tuple[
    datetime,
    datetime,
]:
    normalized_since = (
        _normalize_datetime(
            since,
            field_name="since",
        )
    )

    normalized_until = (
        _normalize_datetime(
            until,
            field_name="until",
        )
    )

    if normalized_until <= normalized_since:
        raise (
            WhatYouMissedEvidenceValidationError(
                "until must be later than since."
            )
        )

    return (
        normalized_since,
        normalized_until,
    )


def _optional_float(
    value,
) -> float | None:
    if value is None:
        return None

    return float(
        value
    )


def _normalize_pattern_event_ids(
    value,
    *,
    pattern_id: uuid.UUID,
) -> tuple[
    uuid.UUID,
    ...
]:
    if value is None:
        return tuple()

    if not isinstance(
        value,
        (
            list,
            tuple,
        ),
    ):
        raise (
            WhatYouMissedEvidenceValidationError(
                "Persisted Pattern.event_ids must be a list "
                f"for pattern {pattern_id}."
            )
        )

    normalized: list[
        uuid.UUID
    ] = []

    for raw_event_id in value:
        try:
            event_id = _as_uuid(
                raw_event_id,
                field_name=(
                    "Pattern.event_ids"
                ),
            )
        except (
            WhatYouMissedEvidenceValidationError
        ) as exc:
            raise (
                WhatYouMissedEvidenceValidationError(
                    "Persisted Pattern.event_ids contains "
                    f"an invalid event id for pattern {pattern_id}."
                )
            ) from exc

        normalized.append(
            event_id
        )

    return tuple(
        normalized
    )


def _load_events(
    db: Session,
    *,
    organization_id: uuid.UUID,
    since: datetime,
    until: datetime,
) -> tuple[
    MissedEventEvidence,
    ...
]:
    rows = (
        db.query(
            SemanticDriftEvent,
            MonitoredPage,
            Entity,
        )
        .join(
            MonitoredPage,
            MonitoredPage.id
            == SemanticDriftEvent.monitored_page_id,
        )
        .join(
            Entity,
            Entity.id
            == MonitoredPage.entity_id,
        )
        .filter(
            SemanticDriftEvent.organization_id
            == organization_id,
            MonitoredPage.organization_id
            == organization_id,
            Entity.organization_id
            == organization_id,
            SemanticDriftEvent.detected_at
            > since,
            SemanticDriftEvent.detected_at
            <= until,
        )
        .order_by(
            SemanticDriftEvent.detected_at.asc(),
            SemanticDriftEvent.id.asc(),
        )
        .all()
    )

    return tuple(
        MissedEventEvidence(
            event_id=event.id,
            monitored_page_id=page.id,
            entity_id=entity.id,
            entity_name=str(
                entity.name
            ),
            entity_domain=(
                str(
                    entity.domain
                )
                if entity.domain
                is not None
                else None
            ),
            page_url=str(
                page.url
            ),
            page_type=str(
                page.page_type
            ),
            occurred_at=event.detected_at,
            category=(
                str(
                    event.category
                )
                if event.category
                is not None
                else None
            ),
            change_type=(
                str(
                    event.change_type
                )
                if event.change_type
                is not None
                else None
            ),
            summary_text=event.summary_text,
            semantic_magnitude=(
                _optional_float(
                    event.semantic_magnitude
                )
            ),
            impact_score=int(
                event.impact_score
                or 0
            ),
            confidence=(
                _optional_float(
                    event.confidence
                )
            ),
            risk_level=str(
                event.risk_level
                or "unknown"
            ),
            is_pattern_part=bool(
                event.is_pattern_part
            ),
            is_anomaly=bool(
                event.is_anomaly
            ),
        )
        for event, page, entity in rows
    )


def _load_risks(
    db: Session,
    *,
    organization_id: uuid.UUID,
    event_ids: tuple[
        uuid.UUID,
        ...
    ],
    until: datetime,
) -> tuple[
    MissedRiskEvidence,
    ...
]:
    if not event_ids:
        return tuple()

    rows = (
        db.query(
            RiskAssessment
        )
        .join(
            SemanticDriftEvent,
            SemanticDriftEvent.id
            == RiskAssessment.semantic_drift_event_id,
        )
        .filter(
            RiskAssessment.organization_id
            == organization_id,
            SemanticDriftEvent.organization_id
            == organization_id,
            # Preserve the historical events in the report, but exclude
            # unqualified Risk v2 assessments from risk evidence.
            SemanticDriftEvent.analysis_status
            == "completed",
            SemanticDriftEvent.vector_analysis_status
            == "completed",
            RiskAssessment.semantic_drift_event_id.in_(
                event_ids
            ),
            RiskAssessment.risk_version
            == RISK_VERSION_V2,
            RiskAssessment.assessed_at
            <= until,
            RiskAssessment.created_at
            <= until,
        )
        .order_by(
            RiskAssessment.assessed_at.asc(),
            RiskAssessment.id.asc(),
        )
        .all()
    )

    return tuple(
        MissedRiskEvidence(
            assessment_id=row.id,
            event_id=(
                row.semantic_drift_event_id
            ),
            assessed_at=row.assessed_at,
            created_at=row.created_at,
            score=float(
                row.score
            ),
            semantic_magnitude_contribution=float(
                row.semantic_magnitude_contribution
            ),
            legacy_impact_contribution=float(
                row.legacy_impact_contribution
            ),
            semantic_severity_contribution=float(
                row.semantic_severity_contribution
            ),
            page_context_contribution=float(
                row.page_context_contribution
            ),
            confidence_contribution=float(
                row.confidence_contribution
            ),
            topology_score=float(
                row.topology_score
            ),
            topology_contribution=float(
                row.topology_contribution
            ),
            topology_shift_component=float(
                row.topology_shift_component
            ),
            topology_centrality_component=float(
                row.topology_centrality_component
            ),
            topology_downstream_component=float(
                row.topology_downstream_component
            ),
            has_topology_signal=bool(
                row.has_topology_signal
            ),
            has_previous_topology_state=bool(
                row.has_previous_topology_state
            ),
        )
        for row in rows
    )


def _load_topology(
    db: Session,
    *,
    organization_id: uuid.UUID,
    event_ids: tuple[
        uuid.UUID,
        ...
    ],
    until: datetime,
) -> tuple[
    MissedTopologyEvidence,
    ...
]:
    if not event_ids:
        return tuple()

    rows = (
        db.query(
            TopologyShiftSnapshot
        )
        .filter(
            TopologyShiftSnapshot.organization_id
            == organization_id,
            TopologyShiftSnapshot.semantic_drift_event_id.in_(
                event_ids
            ),
            TopologyShiftSnapshot.observed_at
            <= until,
            TopologyShiftSnapshot.created_at
            <= until,
        )
        .order_by(
            TopologyShiftSnapshot.observed_at.asc(),
            TopologyShiftSnapshot.entity_id.asc(),
            TopologyShiftSnapshot.id.asc(),
        )
        .all()
    )

    return tuple(
        MissedTopologyEvidence(
            topology_shift_id=row.id,
            event_id=(
                row.semantic_drift_event_id
            ),
            entity_id=row.entity_id,
            observed_at=row.observed_at,
            created_at=row.created_at,
            previous_observed_at=(
                row.previous_observed_at
            ),
            classification=str(
                row.classification
            ),
            severity=str(
                row.severity
            ),
            shift_score=float(
                row.shift_score
            ),
            entity_count_delta=int(
                row.entity_count_delta
            ),
            active_edge_count_delta=int(
                row.active_edge_count_delta
            ),
            in_degree_delta=int(
                row.in_degree_delta
            ),
            out_degree_delta=int(
                row.out_degree_delta
            ),
            degree_delta=int(
                row.degree_delta
            ),
            degree_centrality_delta=float(
                row.degree_centrality_delta
            ),
            downstream_reach_delta=int(
                row.downstream_reach_delta
            ),
            downstream_reach_ratio_delta=float(
                row.downstream_reach_ratio_delta
            ),
        )
        for row in rows
    )


def _load_patterns(
    db: Session,
    *,
    organization_id: uuid.UUID,
    missed_event_ids: set[
        uuid.UUID
    ],
    since: datetime,
    until: datetime,
) -> tuple[
    MissedPatternEvidence,
    ...
]:
    if not missed_event_ids:
        return tuple()

    rows = (
        db.query(
            Pattern
        )
        .filter(
            Pattern.organization_id
            == organization_id,
            Pattern.detected_at
            <= until,
            Pattern.last_event_at
            > since,
            Pattern.first_event_at
            <= until,
        )
        .order_by(
            Pattern.detected_at.asc(),
            Pattern.id.asc(),
        )
        .all()
    )

    qualified_ids = qualified_pattern_ids(
        db,
        organization_id=organization_id,
        patterns=rows,
    )
    results: list[
        MissedPatternEvidence
    ] = []

    for row in rows:
        if row.id not in qualified_ids:
            continue
        pattern_event_ids = (
            _normalize_pattern_event_ids(
                row.event_ids,
                pattern_id=row.id,
            )
        )

        matched_event_ids = tuple(
            event_id
            for event_id
            in pattern_event_ids
            if event_id
            in missed_event_ids
        )

        if not matched_event_ids:
            continue

        results.append(
            MissedPatternEvidence(
                pattern_id=row.id,
                pattern_type=str(
                    row.pattern_type
                ),
                detection_method=(
                    str(
                        row.detection_method
                    )
                    if row.detection_method
                    is not None
                    else None
                ),
                event_ids=pattern_event_ids,
                matched_event_ids=(
                    matched_event_ids
                ),
                event_count=int(
                    row.event_count
                    or 0
                ),
                entity_count=int(
                    row.entity_count
                    or 0
                ),
                time_span_days=int(
                    row.time_span_days
                    or 0
                ),
                significance_score=float(
                    row.significance_score
                    or 0.0
                ),
                first_event_at=(
                    row.first_event_at
                ),
                last_event_at=(
                    row.last_event_at
                ),
                detected_at=(
                    row.detected_at
                ),
                name=str(
                    row.name
                ),
                description=(
                    row.description
                ),
                summary=row.summary,
            )
        )

    return tuple(
        results
    )


def _load_anomalies(
    db: Session,
    *,
    organization_id: uuid.UUID,
    event_ids: tuple[
        uuid.UUID,
        ...
    ],
    until: datetime,
) -> tuple[
    MissedAnomalyEvidence,
    ...
]:
    if not event_ids:
        return tuple()

    rows = (
        db.query(
            Anomaly
        )
        .filter(
            Anomaly.organization_id
            == organization_id,
            Anomaly.event_id.in_(
                event_ids
            ),
            Anomaly.detected_at
            <= until,
        )
        .order_by(
            Anomaly.detected_at.asc(),
            Anomaly.event_id.asc(),
            Anomaly.id.asc(),
        )
        .all()
    )

    return tuple(
        MissedAnomalyEvidence(
            anomaly_id=row.id,
            event_id=row.event_id,
            anomaly_type=str(
                row.anomaly_type
            ),
            detection_method=(
                str(
                    row.detection_method
                )
                if row.detection_method
                is not None
                else None
            ),
            expected_value=(
                _optional_float(
                    row.expected_value
                )
            ),
            actual_value=(
                _optional_float(
                    row.actual_value
                )
            ),
            deviation_score=float(
                row.deviation_score
                or 0.0
            ),
            significance=float(
                row.significance
                or 0.0
            ),
            baseline_days=int(
                row.baseline_days
                or 0
            ),
            window_days=int(
                row.window_days
            ),
            detected_at=(
                row.detected_at
            ),
            description=(
                row.description
            ),
        )
        for row in rows
    )


def load_what_you_missed_evidence(
    db: Session,
    *,
    organization_id,
    since: datetime,
    until: datetime,
) -> WhatYouMissedEvidenceBundle:
    """
    Load persisted evidence for What You Missed v1.

    The event window is left-open/right-closed:

        since < event.detected_at <= until

    This means adjacent windows do not duplicate events:

        (08:00, 12:00]
        (12:00, 16:00]

    Derived evidence is restricted to those missed events and is
    knowledge-gated by ``until`` where a persisted created_at timestamp
    exists.

    The caller owns the Session lifecycle. This function performs no
    commit, rollback, flush, mutation, LLM call, or external API request.
    """

    organization_uuid = _as_uuid(
        organization_id,
        field_name="organization_id",
    )

    (
        normalized_since,
        normalized_until,
    ) = _normalize_window(
        since=since,
        until=until,
    )

    events = _load_events(
        db,
        organization_id=organization_uuid,
        since=normalized_since,
        until=normalized_until,
    )

    event_ids = tuple(
        event.event_id
        for event in events
    )

    event_id_set = set(
        event_ids
    )

    risks = _load_risks(
        db,
        organization_id=organization_uuid,
        event_ids=event_ids,
        until=normalized_until,
    )

    topology = _load_topology(
        db,
        organization_id=organization_uuid,
        event_ids=event_ids,
        until=normalized_until,
    )

    patterns = _load_patterns(
        db,
        organization_id=organization_uuid,
        missed_event_ids=event_id_set,
        since=normalized_since,
        until=normalized_until,
    )

    anomalies = _load_anomalies(
        db,
        organization_id=organization_uuid,
        event_ids=event_ids,
        until=normalized_until,
    )

    return WhatYouMissedEvidenceBundle(
        organization_id=organization_uuid,
        since=normalized_since,
        until=normalized_until,
        events=events,
        risks=risks,
        topology=topology,
        patterns=patterns,
        anomalies=anomalies,
    )