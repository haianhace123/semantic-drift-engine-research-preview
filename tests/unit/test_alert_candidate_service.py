from __future__ import annotations

from datetime import datetime, timezone
import uuid

import pytest

from src.models.anomaly import Anomaly
from src.models.monitored_page import MonitoredPage
from src.models.pattern import Pattern
from src.models.risk_assessment import RiskAssessment
from src.models.semantic_drift_event import SemanticDriftEvent
from src.models.watchlist import Watchlist
from src.services.alert_candidate_service import (
    AlertCandidatePolicyError,
    build_anomaly_alert_candidate,
    build_pattern_alert_candidate,
    build_risk_alert_candidate,
)
from src.services.risk_assessment_service import (
    RISK_VERSION_V2,
)


NOW = datetime(
    2026,
    9,
    11,
    2,
    0,
    tzinfo=timezone.utc,
)


def _watchlist(
    *,
    organization_id: uuid.UUID,
    entity_ids=None,
    page_ids=None,
    categories=None,
    alert_on_high_risk=True,
    alert_on_medium_risk=False,
    alert_on_pattern=True,
    alert_on_anomaly=True,
    is_active=True,
    is_archived=False,
) -> Watchlist:
    return Watchlist(
        id=uuid.uuid4(),
        organization_id=organization_id,
        name="Test Watchlist",
        entity_ids=entity_ids or [],
        page_ids=page_ids or [],
        categories=categories or [],
        alert_on_high_risk=alert_on_high_risk,
        alert_on_medium_risk=alert_on_medium_risk,
        alert_on_pattern=alert_on_pattern,
        alert_on_anomaly=alert_on_anomaly,
        is_active=is_active,
        is_archived=is_archived,
    )


def _page(
    *,
    organization_id: uuid.UUID,
    entity_id: uuid.UUID,
) -> MonitoredPage:
    return MonitoredPage(
        id=uuid.uuid4(),
        organization_id=organization_id,
        entity_id=entity_id,
        url="https://example.com/pricing",
        page_type="pricing",
    )


def _event(
    *,
    organization_id: uuid.UUID,
    page: MonitoredPage,
    category: str | None = "pricing",
) -> SemanticDriftEvent:
    return SemanticDriftEvent(
        id=uuid.uuid4(),
        organization_id=organization_id,
        monitored_page_id=page.id,
        old_snapshot_id=uuid.uuid4(),
        new_snapshot_id=uuid.uuid4(),
        semantic_delta_json={},
        summary_text="Pricing terms changed.",
        category=category,
        detected_at=NOW,
        analysis_status="completed",
        vector_analysis_status="completed",
    )


def _risk(
    *,
    organization_id: uuid.UUID,
    event_id: uuid.UUID | None,
    score: float,
    risk_version: str = RISK_VERSION_V2,
) -> RiskAssessment:
    return RiskAssessment(
        id=uuid.uuid4(),
        organization_id=organization_id,
        semantic_drift_event_id=event_id,
        risk_version=risk_version,
        assessed_at=NOW,
        score=score,
        semantic_magnitude_contribution=0.0,
        legacy_impact_contribution=0.0,
        semantic_severity_contribution=0.0,
        page_context_contribution=0.0,
        confidence_contribution=0.0,
        topology_score=0.0,
        topology_contribution=0.0,
        topology_shift_component=0.0,
        topology_centrality_component=0.0,
        topology_downstream_component=0.0,
        has_topology_signal=False,
        has_previous_topology_state=False,
    )


def _pattern(
    *,
    organization_id: uuid.UUID,
    entity_id: uuid.UUID,
) -> Pattern:
    return Pattern(
        id=uuid.uuid4(),
        organization_id=organization_id,
        name="Repeated pricing changes",
        description="Repeated pricing changes detected.",
        pattern_type="recurring_change",
        detection_key="pattern-key",
        detection_method="test",
        event_ids=[
            str(
                uuid.uuid4()
            )
        ],
        entities=[
            str(
                entity_id
            )
        ],
        categories=[],
        event_count=1,
        entity_count=1,
        time_span_days=1.0,
        significance_score=0.75,
        first_event_at=NOW,
        last_event_at=NOW,
        summary="Repeated pricing changes detected.",
        detected_at=NOW,
        is_active=True,
        is_archived=False,
    )


def _anomaly(
    *,
    organization_id: uuid.UUID,
    event_id: uuid.UUID,
) -> Anomaly:
    return Anomaly(
        id=uuid.uuid4(),
        organization_id=organization_id,
        event_id=event_id,
        anomaly_type="magnitude_spike",
        detection_key="anomaly-key",
        description="Unexpected semantic magnitude spike.",
        expected_value=0.1,
        actual_value=0.9,
        deviation_score=0.8,
        significance=0.9,
        baseline_days=30,
        window_days=1,
        detection_method="test",
        detected_at=NOW,
        is_confirmed=False,
        is_dismissed=False,
        is_archived=False,
    )


def test_high_risk_without_watchlist_is_enabled_by_default() -> None:
    organization_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    page = _page(
        organization_id=organization_id,
        entity_id=entity_id,
    )

    event = _event(
        organization_id=organization_id,
        page=page,
    )

    assessment = _risk(
        organization_id=organization_id,
        event_id=event.id,
        score=0.70,
    )

    candidate = build_risk_alert_candidate(
        organization_id=organization_id,
        assessment=assessment,
        event=event,
        monitored_page=page,
    )

    assert candidate is not None
    assert candidate.source_type == "risk"
    assert candidate.source_id == assessment.id
    assert candidate.event_id == event.id
    assert candidate.severity == "high"
    assert candidate.occurred_at == event.detected_at
    assert candidate.affected_entities == (
        str(
            entity_id
        ),
    )


@pytest.mark.parametrize(
    ("analysis_status", "vector_status"),
    [
        ("pending", "completed"),
        ("failed", "completed"),
        ("completed", "pending"),
        ("completed", "failed"),
        (None, "completed"),
        ("completed", None),
    ],
)
def test_risk_alert_does_not_promote_unqualified_historical_assessment(
    analysis_status: str | None,
    vector_status: str | None,
) -> None:
    organization_id = uuid.uuid4()
    entity_id = uuid.uuid4()
    page = _page(organization_id=organization_id, entity_id=entity_id)
    event = _event(organization_id=organization_id, page=page)
    event.analysis_status = analysis_status
    event.vector_analysis_status = vector_status
    assessment = _risk(
        organization_id=organization_id,
        event_id=event.id,
        score=0.95,
    )
    # High-severity and a matching watchlist must not bypass evidence safety.
    watchlist = _watchlist(organization_id=organization_id)
    assert build_risk_alert_candidate(
        organization_id=organization_id,
        assessment=assessment,
        event=event,
        monitored_page=page,
        watchlists=(watchlist,),
    ) is None


def test_medium_risk_without_watchlist_is_suppressed() -> None:
    organization_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    page = _page(
        organization_id=organization_id,
        entity_id=entity_id,
    )

    event = _event(
        organization_id=organization_id,
        page=page,
    )

    assessment = _risk(
        organization_id=organization_id,
        event_id=event.id,
        score=0.50,
    )

    candidate = build_risk_alert_candidate(
        organization_id=organization_id,
        assessment=assessment,
        event=event,
        monitored_page=page,
    )

    assert candidate is None


def test_matching_watchlist_can_enable_medium_risk() -> None:
    organization_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    page = _page(
        organization_id=organization_id,
        entity_id=entity_id,
    )

    event = _event(
        organization_id=organization_id,
        page=page,
    )

    assessment = _risk(
        organization_id=organization_id,
        event_id=event.id,
        score=0.50,
    )

    watchlist = _watchlist(
        organization_id=organization_id,
        entity_ids=[
            str(
                entity_id
            )
        ],
        alert_on_medium_risk=True,
    )

    candidate = build_risk_alert_candidate(
        organization_id=organization_id,
        assessment=assessment,
        event=event,
        monitored_page=page,
        watchlists=(
            watchlist,
        ),
    )

    assert candidate is not None
    assert candidate.severity == "medium"


def test_nonmatching_watchlist_suppresses_high_risk() -> None:
    organization_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    page = _page(
        organization_id=organization_id,
        entity_id=entity_id,
    )

    event = _event(
        organization_id=organization_id,
        page=page,
    )

    assessment = _risk(
        organization_id=organization_id,
        event_id=event.id,
        score=0.70,
    )

    watchlist = _watchlist(
        organization_id=organization_id,
        entity_ids=[
            str(
                uuid.uuid4()
            )
        ],
    )

    candidate = build_risk_alert_candidate(
        organization_id=organization_id,
        assessment=assessment,
        event=event,
        monitored_page=page,
        watchlists=(
            watchlist,
        ),
    )

    assert candidate is None


def test_global_watchlist_matches_any_high_risk() -> None:
    organization_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    page = _page(
        organization_id=organization_id,
        entity_id=entity_id,
    )

    event = _event(
        organization_id=organization_id,
        page=page,
    )

    assessment = _risk(
        organization_id=organization_id,
        event_id=event.id,
        score=0.90,
    )

    watchlist = _watchlist(
        organization_id=organization_id,
    )

    candidate = build_risk_alert_candidate(
        organization_id=organization_id,
        assessment=assessment,
        event=event,
        monitored_page=page,
        watchlists=(
            watchlist,
        ),
    )

    assert candidate is not None
    assert candidate.severity == "critical"


def test_category_matching_is_case_insensitive() -> None:
    organization_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    page = _page(
        organization_id=organization_id,
        entity_id=entity_id,
    )

    event = _event(
        organization_id=organization_id,
        page=page,
        category="Pricing",
    )

    assessment = _risk(
        organization_id=organization_id,
        event_id=event.id,
        score=0.70,
    )

    watchlist = _watchlist(
        organization_id=organization_id,
        categories=[
            " pricing ",
        ],
    )

    candidate = build_risk_alert_candidate(
        organization_id=organization_id,
        assessment=assessment,
        event=event,
        monitored_page=page,
        watchlists=(
            watchlist,
        ),
    )

    assert candidate is not None


def test_archived_watchlist_does_not_override_default_policy() -> None:
    organization_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    page = _page(
        organization_id=organization_id,
        entity_id=entity_id,
    )

    event = _event(
        organization_id=organization_id,
        page=page,
    )

    assessment = _risk(
        organization_id=organization_id,
        event_id=event.id,
        score=0.70,
    )

    watchlist = _watchlist(
        organization_id=organization_id,
        entity_ids=[
            str(
                uuid.uuid4()
            )
        ],
        is_active=False,
        is_archived=True,
    )

    candidate = build_risk_alert_candidate(
        organization_id=organization_id,
        assessment=assessment,
        event=event,
        monitored_page=page,
        watchlists=(
            watchlist,
        ),
    )

    assert candidate is not None


def test_wrong_risk_version_is_ignored() -> None:
    organization_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    page = _page(
        organization_id=organization_id,
        entity_id=entity_id,
    )

    event = _event(
        organization_id=organization_id,
        page=page,
    )

    assessment = _risk(
        organization_id=organization_id,
        event_id=event.id,
        score=0.90,
        risk_version="v1",
    )

    candidate = build_risk_alert_candidate(
        organization_id=organization_id,
        assessment=assessment,
        event=event,
        monitored_page=page,
    )

    assert candidate is None


def test_eventless_high_risk_can_use_default_policy() -> None:
    organization_id = uuid.uuid4()

    assessment = _risk(
        organization_id=organization_id,
        event_id=None,
        score=0.70,
    )

    candidate = build_risk_alert_candidate(
        organization_id=organization_id,
        assessment=assessment,
        event=None,
        monitored_page=None,
    )

    assert candidate is not None
    assert candidate.event_id is None
    assert candidate.occurred_at == assessment.assessed_at
    assert candidate.affected_entities == ()


def test_cross_tenant_watchlist_is_rejected() -> None:
    organization_id = uuid.uuid4()
    foreign_organization_id = uuid.uuid4()

    assessment = _risk(
        organization_id=organization_id,
        event_id=None,
        score=0.70,
    )

    foreign_watchlist = _watchlist(
        organization_id=foreign_organization_id,
    )

    with pytest.raises(
        AlertCandidatePolicyError
    ):
        build_risk_alert_candidate(
            organization_id=organization_id,
            assessment=assessment,
            event=None,
            monitored_page=None,
            watchlists=(
                foreign_watchlist,
            ),
        )


def test_pattern_without_watchlist_is_enabled_by_default() -> None:
    organization_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    pattern = _pattern(
        organization_id=organization_id,
        entity_id=entity_id,
    )

    candidate = (
        build_pattern_alert_candidate(
            organization_id=organization_id,
            pattern=pattern,
        )
    )

    assert candidate is not None
    assert candidate.source_type == "pattern"
    assert candidate.source_id == pattern.id
    assert candidate.event_id is None
    assert candidate.severity is None
    assert candidate.affected_entities == (
        str(
            entity_id
        ),
    )


def test_pattern_requires_matching_enabled_watchlist() -> None:
    organization_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    pattern = _pattern(
        organization_id=organization_id,
        entity_id=entity_id,
    )

    nonmatching = _watchlist(
        organization_id=organization_id,
        entity_ids=[
            str(
                uuid.uuid4()
            )
        ],
        alert_on_pattern=True,
    )

    disabled_matching = _watchlist(
        organization_id=organization_id,
        entity_ids=[
            str(
                entity_id
            )
        ],
        alert_on_pattern=False,
    )

    candidate = (
        build_pattern_alert_candidate(
            organization_id=organization_id,
            pattern=pattern,
            watchlists=(
                nonmatching,
                disabled_matching,
            ),
        )
    )

    assert candidate is None


def test_pattern_matching_enabled_watchlist_creates_candidate() -> None:
    organization_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    pattern = _pattern(
        organization_id=organization_id,
        entity_id=entity_id,
    )

    watchlist = _watchlist(
        organization_id=organization_id,
        entity_ids=[
            str(
                entity_id
            )
        ],
        alert_on_pattern=True,
    )

    candidate = (
        build_pattern_alert_candidate(
            organization_id=organization_id,
            pattern=pattern,
            watchlists=(
                watchlist,
            ),
        )
    )

    assert candidate is not None


def test_archived_pattern_is_suppressed() -> None:
    organization_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    pattern = _pattern(
        organization_id=organization_id,
        entity_id=entity_id,
    )

    pattern.is_archived = True

    candidate = (
        build_pattern_alert_candidate(
            organization_id=organization_id,
            pattern=pattern,
        )
    )

    assert candidate is None


def test_anomaly_without_watchlist_is_enabled_by_default() -> None:
    organization_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    page = _page(
        organization_id=organization_id,
        entity_id=entity_id,
    )

    event = _event(
        organization_id=organization_id,
        page=page,
    )

    anomaly = _anomaly(
        organization_id=organization_id,
        event_id=event.id,
    )

    candidate = (
        build_anomaly_alert_candidate(
            organization_id=organization_id,
            anomaly=anomaly,
            event=event,
            monitored_page=page,
        )
    )

    assert candidate is not None
    assert candidate.source_type == "anomaly"
    assert candidate.source_id == anomaly.id
    assert candidate.event_id == event.id
    assert candidate.severity is None
    assert candidate.occurred_at == anomaly.detected_at
    assert candidate.affected_entities == (
        str(
            entity_id
        ),
    )

def test_anomaly_requires_matching_enabled_watchlist() -> None:
    organization_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    page = _page(
        organization_id=organization_id,
        entity_id=entity_id,
    )

    event = _event(
        organization_id=organization_id,
        page=page,
    )

    anomaly = _anomaly(
        organization_id=organization_id,
        event_id=event.id,
    )

    nonmatching = _watchlist(
        organization_id=organization_id,
        entity_ids=[
            str(
                uuid.uuid4()
            )
        ],
        alert_on_anomaly=True,
    )

    disabled_matching = _watchlist(
        organization_id=organization_id,
        entity_ids=[
            str(
                entity_id
            )
        ],
        alert_on_anomaly=False,
    )

    candidate = (
        build_anomaly_alert_candidate(
            organization_id=organization_id,
            anomaly=anomaly,
            event=event,
            monitored_page=page,
            watchlists=(
                nonmatching,
                disabled_matching,
            ),
        )
    )

    assert candidate is None


def test_matching_watchlist_can_enable_anomaly() -> None:
    organization_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    page = _page(
        organization_id=organization_id,
        entity_id=entity_id,
    )

    event = _event(
        organization_id=organization_id,
        page=page,
    )

    anomaly = _anomaly(
        organization_id=organization_id,
        event_id=event.id,
    )

    watchlist = _watchlist(
        organization_id=organization_id,
        entity_ids=[
            str(
                entity_id
            )
        ],
        alert_on_anomaly=True,
    )

    candidate = (
        build_anomaly_alert_candidate(
            organization_id=organization_id,
            anomaly=anomaly,
            event=event,
            monitored_page=page,
            watchlists=(
                watchlist,
            ),
        )
    )

    assert candidate is not None
    assert candidate.severity is None


def test_dismissed_anomaly_is_suppressed() -> None:
    organization_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    page = _page(
        organization_id=organization_id,
        entity_id=entity_id,
    )

    event = _event(
        organization_id=organization_id,
        page=page,
    )

    anomaly = _anomaly(
        organization_id=organization_id,
        event_id=event.id,
    )

    anomaly.is_dismissed = True

    candidate = (
        build_anomaly_alert_candidate(
            organization_id=organization_id,
            anomaly=anomaly,
            event=event,
            monitored_page=page,
        )
    )

    assert candidate is None


def test_anomaly_event_mismatch_is_rejected_before_policy() -> None:
    organization_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    page = _page(
        organization_id=organization_id,
        entity_id=entity_id,
    )

    event = _event(
        organization_id=organization_id,
        page=page,
    )

    anomaly = _anomaly(
        organization_id=organization_id,
        event_id=uuid.uuid4(),
    )

    with pytest.raises(
        AlertCandidatePolicyError
    ):
        build_anomaly_alert_candidate(
            organization_id=organization_id,
            anomaly=anomaly,
            event=event,
            monitored_page=page,
        )