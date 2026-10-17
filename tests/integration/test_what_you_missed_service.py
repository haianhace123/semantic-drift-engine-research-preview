from __future__ import annotations

from datetime import (
    datetime,
    timedelta,
    timezone,
)
import os
import uuid

import pytest

from src.core.database import SessionLocal
from src.models.anomaly import Anomaly
from src.models.entity import Entity
from src.models.monitored_page import MonitoredPage
from src.models.organization import Organization
from src.models.page_snapshot import PageSnapshot
from src.models.pattern import Pattern
from src.models.risk_assessment import RiskAssessment
from src.models.semantic_drift_event import (
    SemanticDriftEvent,
)
from src.models.topology_shift_snapshot import (
    TopologyShiftSnapshot,
)
from src.services.what_you_missed_service import (
    get_what_you_missed,
)


pytestmark = pytest.mark.skipif(
    not os.getenv(
        "RUN_DB_INTEGRATION_TESTS"
    ),
    reason=(
        "RUN_DB_INTEGRATION_TESTS is not enabled"
    ),
)


BASE = datetime(
    2026,
    9,
    2,
    4,
    0,
    tzinfo=timezone.utc,
)


def _create_organization(
    db,
):
    token = uuid.uuid4().hex

    organization = Organization(
        id=uuid.uuid4(),
        name=(
            "What You Missed E2E "
            f"{token[:8]}"
        ),
        slug=(
            "what-you-missed-e2e-"
            f"{token}"
        ),
        billing_email=(
            f"wym-{token[:16]}"
            "@example.com"
        ),
    )

    db.add(
        organization
    )
    db.flush()

    return organization


def _create_entity_and_page(
    db,
    *,
    organization,
    suffix: str,
):
    token = uuid.uuid4().hex

    entity = Entity(
        organization_id=organization.id,
        name=(
            f"Entity {suffix}"
        ),
        domain=(
            f"{suffix.lower()}-"
            f"{token}.example.com"
        ),
        category="technology",
    )

    db.add(
        entity
    )
    db.flush()

    page = MonitoredPage(
        organization_id=organization.id,
        entity_id=entity.id,
        url=(
            f"https://"
            f"{entity.domain}/docs"
        ),
        page_type="docs",
        crawl_frequency="daily",
        is_active=True,
    )

    db.add(
        page
    )
    db.flush()

    return entity, page


def _create_event(
    db,
    *,
    organization,
    page,
    detected_at: datetime,
    suffix: str,
    magnitude: float,
    impact: int,
):
    token = uuid.uuid4().hex

    old_content = (
        f"old content {suffix}"
    )

    new_content = (
        f"new content {suffix}"
    )

    old_snapshot = PageSnapshot(
        organization_id=organization.id,
        monitored_page_id=page.id,
        content_hash=(
            f"old-{suffix}-{token}"
        ),
        raw_content=old_content,
        content_size=len(
            old_content
        ),
        crawled_at=(
            detected_at
            - timedelta(
                minutes=1
            )
        ),
    )

    new_snapshot = PageSnapshot(
        organization_id=organization.id,
        monitored_page_id=page.id,
        content_hash=(
            f"new-{suffix}-{token}"
        ),
        raw_content=new_content,
        content_size=len(
            new_content
        ),
        crawled_at=detected_at,
    )

    db.add_all(
        [
            old_snapshot,
            new_snapshot,
        ]
    )
    db.flush()

    event = SemanticDriftEvent(
        organization_id=organization.id,
        monitored_page_id=page.id,
        old_snapshot_id=old_snapshot.id,
        new_snapshot_id=new_snapshot.id,
        # These fixtures represent successfully analyzed historical changes.
        analysis_status="completed",
        vector_analysis_status="completed",
        semantic_delta_json={
            "source": "what_you_missed_e2e",
            "suffix": suffix,
        },
        summary_text=(
            f"Summary for {suffix}."
        ),
        semantic_magnitude=magnitude,
        confidence=0.90,
        impact_score=impact,
        risk_level=(
            "high"
            if impact >= 70
            else "low"
        ),
        category="content",
        change_type="modified",
        detected_at=detected_at,
    )

    db.add(
        event
    )
    db.flush()

    return event


def _create_risk(
    db,
    *,
    organization,
    event,
    score: float,
    timestamp: datetime,
):
    assessment = RiskAssessment(
        organization_id=organization.id,
        semantic_drift_event_id=event.id,
        risk_version="deterministic_v2",
        assessed_at=timestamp,
        score=score,
        semantic_magnitude_contribution=0.20,
        legacy_impact_contribution=0.10,
        semantic_severity_contribution=0.08,
        page_context_contribution=0.08,
        confidence_contribution=0.07,
        topology_score=0.80,
        topology_contribution=0.20,
        topology_shift_component=0.80,
        topology_centrality_component=0.60,
        topology_downstream_component=0.70,
        has_topology_signal=True,
        has_previous_topology_state=True,
        created_at=timestamp,
    )

    db.add(
        assessment
    )
    db.flush()

    return assessment


def _create_topology(
    db,
    *,
    organization,
    entity,
    event,
    timestamp: datetime,
):
    row = TopologyShiftSnapshot(
        organization_id=organization.id,
        entity_id=entity.id,
        semantic_drift_event_id=event.id,
        previous_event_id=None,
        observed_at=timestamp,
        previous_observed_at=(
            timestamp
            - timedelta(
                days=1
            )
        ),
        entity_count_delta=1,
        active_edge_count_delta=3,
        in_degree_delta=1,
        out_degree_delta=2,
        degree_delta=3,
        degree_centrality_delta=0.25,
        downstream_reach_delta=4,
        downstream_reach_ratio_delta=0.35,
        classification="expanded",
        severity="high",
        shift_score=0.85,
        created_at=timestamp,
    )

    db.add(
        row
    )
    db.flush()

    return row


def _create_pattern(
    db,
    *,
    organization,
    old_event,
    missed_event,
    timestamp: datetime,
):
    row = Pattern(
        organization_id=organization.id,
        name="Repeated Drift",
        description=(
            "Repeated drift across the entity."
        ),
        pattern_type="repeated_drift",
        detection_key=uuid.uuid4().hex,
        detection_method=(
            "deterministic_repeated_drift_v1"
        ),
        event_ids=[
            str(
                old_event.id
            ),
            str(
                missed_event.id
            ),
        ],
        entities=[],
        categories=[],
        event_count=2,
        entity_count=1,
        time_span_days=2,
        significance_score=0.90,
        first_event_at=(
            old_event.detected_at
        ),
        last_event_at=(
            missed_event.detected_at
        ),
        summary=(
            "The entity changed repeatedly."
        ),
        detected_at=timestamp,
        is_active=True,
        is_archived=False,
    )

    db.add(
        row
    )
    db.flush()

    return row


def _create_anomaly(
    db,
    *,
    organization,
    event,
    timestamp: datetime,
):
    row = Anomaly(
        organization_id=organization.id,
        event_id=event.id,
        anomaly_type="activity_burst",
        detection_key=uuid.uuid4().hex,
        description=(
            "Activity exceeded its historical baseline."
        ),
        expected_value=1.0,
        actual_value=5.0,
        deviation_score=0.90,
        significance=0.95,
        baseline_days=28,
        window_days=7,
        detection_method=(
            "deterministic_activity_burst_v1"
        ),
        detected_at=timestamp,
        is_confirmed=False,
        is_dismissed=False,
        is_archived=False,
    )

    db.add(
        row
    )
    db.flush()

    return row


def test_end_to_end_read_model_ranks_and_explains_missed_events():
    db = SessionLocal()

    try:
        organization = (
            _create_organization(
                db
            )
        )

        important_entity, important_page = (
            _create_entity_and_page(
                db,
                organization=organization,
                suffix="Important",
            )
        )

        _, quiet_page = (
            _create_entity_and_page(
                db,
                organization=organization,
                suffix="Quiet",
            )
        )

        since = BASE

        old_event = _create_event(
            db,
            organization=organization,
            page=important_page,
            detected_at=(
                since
                - timedelta(
                    days=1
                )
            ),
            suffix="old",
            magnitude=0.50,
            impact=40,
        )

        important_event = _create_event(
            db,
            organization=organization,
            page=important_page,
            detected_at=(
                since
                + timedelta(
                    hours=1
                )
            ),
            suffix="important",
            magnitude=0.92,
            impact=90,
        )

        quiet_event = _create_event(
            db,
            organization=organization,
            page=quiet_page,
            detected_at=(
                since
                + timedelta(
                    hours=2
                )
            ),
            suffix="quiet",
            magnitude=0.20,
            impact=20,
        )

        until = (
            since
            + timedelta(
                hours=4
            )
        )

        risk = _create_risk(
            db,
            organization=organization,
            event=important_event,
            score=0.92,
            timestamp=(
                since
                + timedelta(
                    hours=1,
                    minutes=5,
                )
            ),
        )

        topology = _create_topology(
            db,
            organization=organization,
            entity=important_entity,
            event=important_event,
            timestamp=(
                since
                + timedelta(
                    hours=1,
                    minutes=6,
                )
            ),
        )

        pattern = _create_pattern(
            db,
            organization=organization,
            old_event=old_event,
            missed_event=important_event,
            timestamp=(
                since
                + timedelta(
                    hours=1,
                    minutes=7,
                )
            ),
        )

        anomaly = _create_anomaly(
            db,
            organization=organization,
            event=important_event,
            timestamp=(
                since
                + timedelta(
                    hours=1,
                    minutes=8,
                )
            ),
        )

        result = get_what_you_missed(
            db,
            organization_id=organization.id,
            since=since,
            until=until,
        )

        assert result.event_count == 2
        assert result.explained_event_count == 2

        assert len(
            result.items
        ) == 2

        first = result.items[0]
        second = result.items[1]

        assert (
            first.event_id
            == important_event.id
        )

        assert (
            second.event_id
            == quiet_event.id
        )

        assert (
            first.importance_score
            > second.importance_score
        )

        assert first.risk_score == 0.92

        assert (
            first.importance_level
            == "critical"
        )

        assert (
            first.headline
            == (
                "Unusual activity detected for "
                "Entity Important"
            )
        )

        reason_codes = {
            reason.code
            for reason
            in first.reasons
        }

        assert "HIGH_RISK" in reason_codes

        assert (
            "HIGH_SEMANTIC_MAGNITUDE"
            in reason_codes
        )

        assert (
            "HIGH_IMPACT"
            in reason_codes
        )

        assert (
            "TOPOLOGY_EXPANSION"
            in reason_codes
        )

        assert (
            "REPEATED_DRIFT"
            in reason_codes
        )

        assert (
            "ACTIVITY_BURST"
            in reason_codes
        )

        assert (
            pattern.id
            in first.pattern_ids
        )

        assert (
            anomaly.id
            in first.anomaly_ids
        )

        assert (
            topology.id
            in first.topology_shift_ids
        )

        assert (
            first.summary
            == "Summary for important."
        )

        assert (
            risk.semantic_drift_event_id
            == important_event.id
        )

        # The event before the left-open boundary is context for the
        # pattern but is not itself returned as a missed event.
        assert old_event.id not in {
            item.event_id
            for item
            in result.items
        }

    finally:
        db.rollback()
        db.close()


def test_end_to_end_limit_preserves_total_event_count():
    db = SessionLocal()

    try:
        organization = (
            _create_organization(
                db
            )
        )

        _, page = (
            _create_entity_and_page(
                db,
                organization=organization,
                suffix="Limit",
            )
        )

        since = BASE

        events = []

        for index in range(
            3
        ):
            events.append(
                _create_event(
                    db,
                    organization=organization,
                    page=page,
                    detected_at=(
                        since
                        + timedelta(
                            minutes=(
                                index
                                + 1
                            )
                        )
                    ),
                    suffix=(
                        f"limit-{index}"
                    ),
                    magnitude=(
                        0.20
                        + (
                            index
                            * 0.10
                        )
                    ),
                    impact=(
                        20
                        + (
                            index
                            * 10
                        )
                    ),
                )
            )

        result = get_what_you_missed(
            db,
            organization_id=organization.id,
            since=since,
            until=(
                since
                + timedelta(
                    hours=1
                )
            ),
            limit=1,
        )

        assert result.event_count == 3

        assert (
            result.explained_event_count
            == 1
        )

        assert len(
            result.items
        ) == 1

        assert (
            result.items[0].event_id
            == events[-1].id
        )

    finally:
        db.rollback()
        db.close()


def test_end_to_end_service_does_not_mutate_session():
    db = SessionLocal()

    try:
        organization = (
            _create_organization(
                db
            )
        )

        _, page = (
            _create_entity_and_page(
                db,
                organization=organization,
                suffix="ReadonlyE2E",
            )
        )

        _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=(
                BASE
                + timedelta(
                    minutes=1
                )
            ),
            suffix="readonly-e2e",
            magnitude=0.50,
            impact=50,
        )

        db.flush()

        new_before = set(
            db.new
        )

        dirty_before = set(
            db.dirty
        )

        deleted_before = set(
            db.deleted
        )

        result = get_what_you_missed(
            db,
            organization_id=organization.id,
            since=BASE,
            until=(
                BASE
                + timedelta(
                    hours=1
                )
            ),
        )

        assert result.event_count == 1

        assert (
            set(
                db.new
            )
            == new_before
        )

        assert (
            set(
                db.dirty
            )
            == dirty_before
        )

        assert (
            set(
                db.deleted
            )
            == deleted_before
        )

    finally:
        db.rollback()
        db.close()