from __future__ import annotations

from datetime import (
    datetime,
    timedelta,
    timezone,
)
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
from src.services.what_you_missed_evidence_service import (
    load_what_you_missed_evidence,
)


pytestmark = pytest.mark.skipif(
    not __import__("os").getenv(
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
    2,
    0,
    tzinfo=timezone.utc,
)


def _slug(
    prefix: str,
) -> str:
    return (
        f"{prefix}-"
        f"{uuid.uuid4().hex}"
    )


def _create_organization(
    db,
    *,
    prefix: str,
):
    organization = Organization(
        id=uuid.uuid4(),
        name=(
            f"What You Missed "
            f"{prefix}"
        ),
        slug=_slug(
            prefix
        ),
        billing_email=(
            f"{prefix}-"
            f"{uuid.uuid4().hex[:12]}"
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
    entity = Entity(
        organization_id=organization.id,
        name=f"Entity {suffix}",
        domain=(
            f"{suffix.lower()}-"
            f"{uuid.uuid4().hex}"
            ".example.com"
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
    impact_score: int = 60,
):
    old_snapshot = PageSnapshot(
        organization_id=organization.id,
        monitored_page_id=page.id,
        content_hash=(
            f"old-{suffix}-"
            f"{uuid.uuid4().hex}"
        ),
        raw_content=(
            f"old content {suffix}"
        ),
        content_size=len(
            f"old content {suffix}"
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
            f"new-{suffix}-"
            f"{uuid.uuid4().hex}"
        ),
        raw_content=(
            f"new content {suffix}"
        ),
        content_size=len(
            f"new content {suffix}"
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
            "test": suffix,
        },
        summary_text=(
            f"Summary {suffix}"
        ),
        semantic_magnitude=0.75,
        confidence=0.90,
        impact_score=impact_score,
        risk_level="high",
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
    assessed_at: datetime,
    created_at: datetime,
    score: float = 0.82,
):
    assessment = RiskAssessment(
        organization_id=organization.id,
        semantic_drift_event_id=event.id,
        risk_version="deterministic_v2",
        assessed_at=assessed_at,
        score=score,
        semantic_magnitude_contribution=0.20,
        legacy_impact_contribution=0.10,
        semantic_severity_contribution=0.08,
        page_context_contribution=0.08,
        confidence_contribution=0.07,
        topology_score=0.72,
        topology_contribution=0.18,
        topology_shift_component=0.60,
        topology_centrality_component=0.50,
        topology_downstream_component=0.70,
        has_topology_signal=True,
        has_previous_topology_state=True,
        created_at=created_at,
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
    observed_at: datetime,
    created_at: datetime,
):
    shift = TopologyShiftSnapshot(
        organization_id=organization.id,
        entity_id=entity.id,
        semantic_drift_event_id=event.id,
        previous_event_id=None,
        observed_at=observed_at,
        previous_observed_at=(
            observed_at
            - timedelta(
                days=1
            )
        ),
        entity_count_delta=1,
        active_edge_count_delta=2,
        in_degree_delta=1,
        out_degree_delta=1,
        degree_delta=2,
        degree_centrality_delta=0.20,
        downstream_reach_delta=3,
        downstream_reach_ratio_delta=0.30,
        classification="expanded",
        severity="high",
        shift_score=0.70,
        created_at=created_at,
    )

    db.add(
        shift
    )
    db.flush()

    return shift


def _create_pattern(
    db,
    *,
    organization,
    event_ids,
    first_event_at: datetime,
    last_event_at: datetime,
    detected_at: datetime,
):
    pattern = Pattern(
        organization_id=organization.id,
        name="Repeated Drift",
        description=(
            "Repeated semantic drift."
        ),
        pattern_type="repeated_drift",
        detection_key=uuid.uuid4().hex,
        detection_method=(
            "deterministic_pattern_v1"
        ),
        event_ids=[
            str(
                event_id
            )
            for event_id
            in event_ids
        ],
        entities=[],
        categories=[],
        event_count=len(
            event_ids
        ),
        entity_count=1,
        time_span_days=3,
        significance_score=0.80,
        first_event_at=first_event_at,
        last_event_at=last_event_at,
        summary="Repeated changes detected.",
        detected_at=detected_at,
        is_active=True,
        is_archived=False,
    )

    db.add(
        pattern
    )
    db.flush()

    return pattern


def _create_anomaly(
    db,
    *,
    organization,
    event,
    detected_at: datetime,
):
    anomaly = Anomaly(
        organization_id=organization.id,
        event_id=event.id,
        anomaly_type="activity_burst",
        detection_key=uuid.uuid4().hex,
        description=(
            "Activity exceeded baseline."
        ),
        expected_value=1.5,
        actual_value=5.0,
        deviation_score=0.78,
        significance=0.82,
        baseline_days=28,
        window_days=7,
        detection_method=(
            "deterministic_activity_burst_v1"
        ),
        detected_at=detected_at,
        is_confirmed=False,
        is_dismissed=False,
        is_archived=False,
    )

    db.add(
        anomaly
    )
    db.flush()

    return anomaly


def test_event_window_is_left_open_right_closed():
    db = SessionLocal()

    try:
        organization = _create_organization(
            db,
            prefix="window",
        )

        entity, page = (
            _create_entity_and_page(
                db,
                organization=organization,
                suffix="Window",
            )
        )

        since = BASE
        until = (
            BASE
            + timedelta(
                hours=4
            )
        )

        at_since = _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=since,
            suffix="at-since",
        )

        inside = _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=(
                since
                + timedelta(
                    hours=2
                )
            ),
            suffix="inside",
        )

        at_until = _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=until,
            suffix="at-until",
        )

        after_until = _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=(
                until
                + timedelta(
                    seconds=1
                )
            ),
            suffix="after-until",
        )

        result = (
            load_what_you_missed_evidence(
                db,
                organization_id=organization.id,
                since=since,
                until=until,
            )
        )

        ids = tuple(
            row.event_id
            for row in result.events
        )

        assert at_since.id not in ids
        assert inside.id in ids
        assert at_until.id in ids
        assert after_until.id not in ids

        assert ids == (
            inside.id,
            at_until.id,
        )

        assert result.event_count == 2

        assert (
            result.events[0].entity_id
            == entity.id
        )

        assert (
            result.events[0].page_url
            == page.url
        )

    finally:
        db.rollback()
        db.close()


def test_evidence_is_tenant_isolated():
    db = SessionLocal()

    try:
        first_org = _create_organization(
            db,
            prefix="tenant-a",
        )

        second_org = _create_organization(
            db,
            prefix="tenant-b",
        )

        first_entity, first_page = (
            _create_entity_and_page(
                db,
                organization=first_org,
                suffix="TenantA",
            )
        )

        second_entity, second_page = (
            _create_entity_and_page(
                db,
                organization=second_org,
                suffix="TenantB",
            )
        )

        first_event = _create_event(
            db,
            organization=first_org,
            page=first_page,
            detected_at=(
                BASE
                + timedelta(
                    hours=1
                )
            ),
            suffix="tenant-a",
        )

        second_event = _create_event(
            db,
            organization=second_org,
            page=second_page,
            detected_at=(
                BASE
                + timedelta(
                    hours=1
                )
            ),
            suffix="tenant-b",
        )

        _create_risk(
            db,
            organization=first_org,
            event=first_event,
            assessed_at=(
                BASE
                + timedelta(
                    hours=1,
                    minutes=1,
                )
            ),
            created_at=(
                BASE
                + timedelta(
                    hours=1,
                    minutes=1,
                )
            ),
        )

        _create_risk(
            db,
            organization=second_org,
            event=second_event,
            assessed_at=(
                BASE
                + timedelta(
                    hours=1,
                    minutes=1,
                )
            ),
            created_at=(
                BASE
                + timedelta(
                    hours=1,
                    minutes=1,
                )
            ),
        )

        result = (
            load_what_you_missed_evidence(
                db,
                organization_id=first_org.id,
                since=BASE,
                until=(
                    BASE
                    + timedelta(
                        hours=2
                    )
                ),
            )
        )

        assert (
            tuple(
                row.event_id
                for row in result.events
            )
            == (
                first_event.id,
            )
        )

        assert (
            tuple(
                row.event_id
                for row in result.risks
            )
            == (
                first_event.id,
            )
        )

        assert second_event.id not in {
            row.event_id
            for row in result.events
        }

        assert (
            result.events[0].entity_id
            == first_entity.id
        )

        assert (
            second_entity.id
            != first_entity.id
        )

    finally:
        db.rollback()
        db.close()


def test_future_risk_and_topology_knowledge_do_not_leak():
    db = SessionLocal()

    try:
        organization = _create_organization(
            db,
            prefix="future",
        )

        entity, page = (
            _create_entity_and_page(
                db,
                organization=organization,
                suffix="Future",
            )
        )

        visible_event_time = (
            BASE
            + timedelta(
                hours=1
            )
        )

        hidden_event_time = (
            BASE
            + timedelta(
                hours=2
            )
        )

        until = (
            BASE
            + timedelta(
                hours=3
            )
        )

        visible_event = _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=visible_event_time,
            suffix="future-visible",
        )

        hidden_event = _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=hidden_event_time,
            suffix="future-hidden",
        )

        visible_risk = _create_risk(
            db,
            organization=organization,
            event=visible_event,
            assessed_at=(
                visible_event_time
                + timedelta(
                    minutes=5
                )
            ),
            created_at=(
                visible_event_time
                + timedelta(
                    minutes=5
                )
            ),
            score=0.60,
        )

        hidden_risk = _create_risk(
            db,
            organization=organization,
            event=hidden_event,
            assessed_at=(
                hidden_event_time
                + timedelta(
                    minutes=5
                )
            ),
            created_at=(
                until
                + timedelta(
                    minutes=1
                )
            ),
            score=0.95,
        )

        visible_topology = (
            _create_topology(
                db,
                organization=organization,
                entity=entity,
                event=visible_event,
                observed_at=visible_event_time,
                created_at=(
                    visible_event_time
                    + timedelta(
                        minutes=5
                    )
                ),
            )
        )

        hidden_topology = (
            _create_topology(
                db,
                organization=organization,
                entity=entity,
                event=hidden_event,
                observed_at=hidden_event_time,
                created_at=(
                    until
                    + timedelta(
                        minutes=1
                    )
                ),
            )
        )

        result = (
            load_what_you_missed_evidence(
                db,
                organization_id=organization.id,
                since=BASE,
                until=until,
            )
        )

        event_ids = {
            row.event_id
            for row in result.events
        }

        risk_ids = {
            row.assessment_id
            for row in result.risks
        }

        topology_ids = {
            row.topology_shift_id
            for row in result.topology
        }

        # Both semantic events belong to the missed-event window.
        assert visible_event.id in event_ids
        assert hidden_event.id in event_ids

        # Risk knowledge created before `until` is visible.
        assert visible_risk.id in risk_ids

        # Risk assessed in time but persisted after `until`
        # must not leak into historical explanation.
        assert hidden_risk.id not in risk_ids

        # Same knowledge-time rule for topology evidence.
        assert (
            visible_topology.id
            in topology_ids
        )

        assert (
            hidden_topology.id
            not in topology_ids
        )

    finally:
        db.rollback()
        db.close()


def test_pattern_is_included_only_when_it_intersects_missed_events():
    db = SessionLocal()

    try:
        organization = _create_organization(
            db,
            prefix="pattern",
        )

        entity, page = (
            _create_entity_and_page(
                db,
                organization=organization,
                suffix="Pattern",
            )
        )

        before = _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=(
                BASE
                - timedelta(
                    hours=1
                )
            ),
            suffix="before",
        )

        inside = _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=(
                BASE
                + timedelta(
                    hours=1
                )
            ),
            suffix="inside",
        )

        unrelated = _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=(
                BASE
                - timedelta(
                    hours=2
                )
            ),
            suffix="unrelated",
        )

        matching_pattern = (
            _create_pattern(
                db,
                organization=organization,
                event_ids=[
                    before.id,
                    inside.id,
                ],
                first_event_at=(
                    BASE
                    - timedelta(
                        hours=1
                    )
                ),
                last_event_at=(
                    BASE
                    + timedelta(
                        hours=1
                    )
                ),
                detected_at=(
                    BASE
                    + timedelta(
                        hours=1,
                        minutes=10,
                    )
                ),
            )
        )

        unrelated_pattern = (
            _create_pattern(
                db,
                organization=organization,
                event_ids=[
                    unrelated.id,
                ],
                first_event_at=(
                    BASE
                    - timedelta(
                        hours=2
                    )
                ),
                last_event_at=(
                    BASE
                    + timedelta(
                        minutes=30
                    )
                ),
                detected_at=(
                    BASE
                    + timedelta(
                        hours=1,
                        minutes=15,
                    )
                ),
            )
        )

        result = (
            load_what_you_missed_evidence(
                db,
                organization_id=organization.id,
                since=BASE,
                until=(
                    BASE
                    + timedelta(
                        hours=2
                    )
                ),
            )
        )

        pattern_ids = {
            row.pattern_id
            for row in result.patterns
        }

        assert (
            matching_pattern.id
            in pattern_ids
        )

        assert (
            unrelated_pattern.id
            not in pattern_ids
        )

        row = next(
            row
            for row in result.patterns
            if (
                row.pattern_id
                == matching_pattern.id
            )
        )

        assert (
            row.matched_event_ids
            == (
                inside.id,
            )
        )

        assert row.event_ids == (
            before.id,
            inside.id,
        )

        assert (
            result.events[0].entity_id
            == entity.id
        )

    finally:
        db.rollback()
        db.close()


def test_future_pattern_detection_does_not_leak():
    db = SessionLocal()

    try:
        organization = _create_organization(
            db,
            prefix="pattern-future",
        )

        _, page = (
            _create_entity_and_page(
                db,
                organization=organization,
                suffix="PatternFuture",
            )
        )

        event = _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=(
                BASE
                + timedelta(
                    hours=1
                )
            ),
            suffix="pattern-future",
        )

        pattern = _create_pattern(
            db,
            organization=organization,
            event_ids=[
                event.id,
            ],
            first_event_at=event.detected_at,
            last_event_at=event.detected_at,
            detected_at=(
                BASE
                + timedelta(
                    hours=5
                )
            ),
        )

        result = (
            load_what_you_missed_evidence(
                db,
                organization_id=organization.id,
                since=BASE,
                until=(
                    BASE
                    + timedelta(
                        hours=2
                    )
                ),
            )
        )

        assert result.event_count == 1

        assert pattern.id not in {
            row.pattern_id
            for row in result.patterns
        }

    finally:
        db.rollback()
        db.close()


def test_anomaly_requires_anchor_event_in_missed_window():
    db = SessionLocal()

    try:
        organization = _create_organization(
            db,
            prefix="anomaly",
        )

        _, page = (
            _create_entity_and_page(
                db,
                organization=organization,
                suffix="Anomaly",
            )
        )

        before_event = _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=(
                BASE
                - timedelta(
                    minutes=1
                )
            ),
            suffix="anomaly-before",
        )

        inside_event = _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=(
                BASE
                + timedelta(
                    hours=1
                )
            ),
            suffix="anomaly-inside",
        )

        before_anomaly = _create_anomaly(
            db,
            organization=organization,
            event=before_event,
            detected_at=(
                BASE
                + timedelta(
                    hours=1
                )
            ),
        )

        inside_anomaly = _create_anomaly(
            db,
            organization=organization,
            event=inside_event,
            detected_at=(
                BASE
                + timedelta(
                    hours=1,
                    minutes=5,
                )
            ),
        )

        result = (
            load_what_you_missed_evidence(
                db,
                organization_id=organization.id,
                since=BASE,
                until=(
                    BASE
                    + timedelta(
                        hours=2
                    )
                ),
            )
        )

        anomaly_ids = {
            row.anomaly_id
            for row in result.anomalies
        }

        assert (
            inside_anomaly.id
            in anomaly_ids
        )

        assert (
            before_anomaly.id
            not in anomaly_ids
        )

        row = result.anomalies[0]

        assert (
            row.event_id
            == inside_event.id
        )

        assert row.baseline_days == 28
        assert row.window_days == 7

    finally:
        db.rollback()
        db.close()


def test_future_anomaly_detection_does_not_leak():
    db = SessionLocal()

    try:
        organization = _create_organization(
            db,
            prefix="anomaly-future",
        )

        _, page = (
            _create_entity_and_page(
                db,
                organization=organization,
                suffix="AnomalyFuture",
            )
        )

        event = _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=(
                BASE
                + timedelta(
                    hours=1
                )
            ),
            suffix="anomaly-future",
        )

        anomaly = _create_anomaly(
            db,
            organization=organization,
            event=event,
            detected_at=(
                BASE
                + timedelta(
                    hours=4
                )
            ),
        )

        result = (
            load_what_you_missed_evidence(
                db,
                organization_id=organization.id,
                since=BASE,
                until=(
                    BASE
                    + timedelta(
                        hours=2
                    )
                ),
            )
        )

        assert result.event_count == 1

        assert anomaly.id not in {
            row.anomaly_id
            for row in result.anomalies
        }

    finally:
        db.rollback()
        db.close()


def test_loader_is_read_only():
    db = SessionLocal()

    try:
        organization = _create_organization(
            db,
            prefix="readonly",
        )

        _, page = (
            _create_entity_and_page(
                db,
                organization=organization,
                suffix="Readonly",
            )
        )

        _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=(
                BASE
                + timedelta(
                    hours=1
                )
            ),
            suffix="readonly",
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

        result = (
            load_what_you_missed_evidence(
                db,
                organization_id=organization.id,
                since=BASE,
                until=(
                    BASE
                    + timedelta(
                        hours=2
                    )
                ),
            )
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

def test_missed_risk_evidence_requires_completed_upstream_lifecycle():
    """Retain raw event history without promoting legacy risk scores."""
    db = SessionLocal()

    try:
        organization = _create_organization(
            db, prefix="missed-qualified-risk"
        )
        _, page = _create_entity_and_page(
            db, organization=organization, suffix="QualifiedRisk"
        )
        at = BASE + timedelta(hours=1)
        event_ids = set()
        qualified_id = None
        cases = (
            ("completed", "completed"),
            ("pending", "completed"),
            ("failed", "completed"),
            ("completed", "pending"),
            ("completed", "failed"),
        )
        for number, (analysis, vector) in enumerate(cases):
            detected_at = at + timedelta(minutes=number)
            event = _create_event(
                db,
                organization=organization,
                page=page,
                detected_at=detected_at,
                suffix=f"risk-status-{number}",
            )
            event.analysis_status = analysis
            event.vector_analysis_status = vector
            event_ids.add(event.id)
            if number == 0:
                qualified_id = event.id
            _create_risk(
                db,
                organization=organization,
                event=event,
                assessed_at=detected_at,
                created_at=detected_at,
                score=0.60 + number * 0.05,
            )

        db.flush()
        result = load_what_you_missed_evidence(
            db,
            organization_id=organization.id,
            since=BASE,
            until=BASE + timedelta(hours=2),
        )

        assert {item.event_id for item in result.events} == event_ids
        assert {item.event_id for item in result.risks} == {qualified_id}
    finally:
        db.rollback()
        db.close()
