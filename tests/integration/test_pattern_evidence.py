from __future__ import annotations

from datetime import (
    datetime,
    timedelta,
    timezone,
)
import uuid

import pytest

from src.core.database import SessionLocal
from src.models.entity import Entity
from src.models.monitored_page import MonitoredPage
from src.models.organization import Organization
from src.models.page_snapshot import PageSnapshot
from src.models.risk_assessment import RiskAssessment
from src.models.semantic_drift_event import SemanticDriftEvent
from src.models.topology_shift_snapshot import TopologyShiftSnapshot
from src.services.pattern_evidence_service import (
    RISK_VERSION_V2,
    load_pattern_evidence,
)


pytestmark = pytest.mark.skipif(
    not __import__("os").getenv(
        "RUN_DB_INTEGRATION_TESTS"
    ),
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


BASE = datetime(
    2026,
    8,
    1,
    12,
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
        name=f"Pattern Evidence {prefix}",
        slug=_slug(prefix),
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
            f"{uuid.uuid4().hex}.example.com"
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
            f"https://{entity.domain}/docs"
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
):
    old_snapshot = PageSnapshot(
        organization_id=organization.id,
        monitored_page_id=page.id,
        content_hash=(
            f"old-{suffix}-"
            f"{uuid.uuid4().hex}"
        ),
        raw_content=f"old {suffix}",
        content_size=len(
            f"old {suffix}"
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
        raw_content=f"new {suffix}",
        content_size=len(
            f"new {suffix}"
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
        impact_score=50,
        detected_at=detected_at,
    )

    db.add(
        event
    )
    db.flush()

    return event


def _create_risk_assessment(
    db,
    *,
    organization,
    event,
    assessed_at: datetime,
    created_at: datetime,
    score: float,
):
    assessment = RiskAssessment(
        organization_id=organization.id,
        semantic_drift_event_id=event.id,
        risk_version=RISK_VERSION_V2,
        assessed_at=assessed_at,
        score=score,
        semantic_magnitude_contribution=0.1,
        legacy_impact_contribution=0.1,
        semantic_severity_contribution=0.1,
        page_context_contribution=0.1,
        confidence_contribution=0.1,
        topology_score=0.1,
        topology_contribution=0.1,
        topology_shift_component=0.1,
        topology_centrality_component=0.1,
        topology_downstream_component=0.1,
        has_topology_signal=True,
        has_previous_topology_state=True,
        created_at=created_at,
    )

    db.add(
        assessment
    )
    db.flush()

    return assessment


def _create_topology_shift(
    db,
    *,
    organization,
    entity,
    event,
    observed_at: datetime,
    created_at: datetime,
    classification: str = "expanded",
):
    shift = TopologyShiftSnapshot(
        organization_id=organization.id,
        entity_id=entity.id,
        semantic_drift_event_id=event.id,
        previous_event_id=None,
        observed_at=observed_at,
        previous_observed_at=None,
        entity_count_delta=0,
        active_edge_count_delta=1,
        in_degree_delta=0,
        out_degree_delta=1,
        degree_delta=1,
        degree_centrality_delta=0.1,
        downstream_reach_delta=1,
        downstream_reach_ratio_delta=0.1,
        classification=classification,
        severity="low",
        shift_score=0.25,
        created_at=created_at,
    )

    db.add(
        shift
    )
    db.flush()

    return shift


def test_pattern_evidence_loads_tenant_window():
    db = SessionLocal()

    try:
        organization = _create_organization(
            db,
            prefix="pattern-window",
        )

        entity, page = (
            _create_entity_and_page(
                db,
                organization=organization,
                suffix="Window",
            )
        )

        inside_event = _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=(
                BASE
                + timedelta(
                    days=20
                )
            ),
            suffix="inside",
        )

        _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=(
                BASE
                - timedelta(
                    days=10
                )
            ),
            suffix="outside",
        )

        db.flush()

        result = load_pattern_evidence(
            db,
            organization_id=organization.id,
            as_of=(
                BASE
                + timedelta(
                    days=30
                )
            ),
            lookback_days=30,
        )

        assert [
            item.event_id
            for item in result.events
        ] == [
            inside_event.id
        ]

        assert (
            result.events[0].entity_id
            == entity.id
        )

    finally:
        db.rollback()
        db.close()


def test_pattern_evidence_is_tenant_isolated():
    db = SessionLocal()

    try:
        first_org = _create_organization(
            db,
            prefix="pattern-tenant-a",
        )

        second_org = _create_organization(
            db,
            prefix="pattern-tenant-b",
        )

        first_entity, first_page = (
            _create_entity_and_page(
                db,
                organization=first_org,
                suffix="TenantA",
            )
        )

        _, second_page = (
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
            detected_at=BASE,
            suffix="tenant-a",
        )

        _create_event(
            db,
            organization=second_org,
            page=second_page,
            detected_at=BASE,
            suffix="tenant-b",
        )

        db.flush()

        result = load_pattern_evidence(
            db,
            organization_id=first_org.id,
            as_of=(
                BASE
                + timedelta(
                    days=1
                )
            ),
            lookback_days=30,
        )

        assert [
            item.event_id
            for item in result.events
        ] == [
            first_event.id
        ]

        assert (
            result.events[0].entity_id
            == first_entity.id
        )

    finally:
        db.rollback()
        db.close()


def test_future_risk_knowledge_does_not_leak():
    db = SessionLocal()

    try:
        organization = _create_organization(
            db,
            prefix="pattern-risk-time",
        )

        entity, page = (
            _create_entity_and_page(
                db,
                organization=organization,
                suffix="RiskTime",
            )
        )

        event_time = (
            BASE
            + timedelta(
                days=5
            )
        )

        event = _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=event_time,
            suffix="risk-time",
        )

        _create_risk_assessment(
            db,
            organization=organization,
            event=event,
            assessed_at=event_time,
            created_at=(
                event_time
                + timedelta(
                    days=2
                )
            ),
            score=0.75,
        )

        db.flush()

        before_materialization = (
            load_pattern_evidence(
                db,
                organization_id=organization.id,
                as_of=(
                    event_time
                    + timedelta(
                        days=1
                    )
                ),
                lookback_days=30,
            )
        )

        assert (
            before_materialization.risk_assessments
            == ()
        )

        after_materialization = (
            load_pattern_evidence(
                db,
                organization_id=organization.id,
                as_of=(
                    event_time
                    + timedelta(
                        days=3
                    )
                ),
                lookback_days=30,
            )
        )

        assert len(
            after_materialization.risk_assessments
        ) == 1

        assert (
            after_materialization
            .risk_assessments[0]
            .event_id
            == event.id
        )

        assert (
            after_materialization
            .risk_assessments[0]
            .entity_id
            == entity.id
        )

    finally:
        db.rollback()
        db.close()


def test_future_topology_knowledge_does_not_leak():
    db = SessionLocal()

    try:
        organization = _create_organization(
            db,
            prefix="pattern-topology-time",
        )

        entity, page = (
            _create_entity_and_page(
                db,
                organization=organization,
                suffix="TopologyTime",
            )
        )

        event_time = (
            BASE
            + timedelta(
                days=5
            )
        )

        event = _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=event_time,
            suffix="topology-time",
        )

        _create_topology_shift(
            db,
            organization=organization,
            entity=entity,
            event=event,
            observed_at=event_time,
            created_at=(
                event_time
                + timedelta(
                    days=2
                )
            ),
        )

        db.flush()

        before_materialization = (
            load_pattern_evidence(
                db,
                organization_id=organization.id,
                as_of=(
                    event_time
                    + timedelta(
                        days=1
                    )
                ),
                lookback_days=30,
            )
        )

        assert (
            before_materialization.topology_shifts
            == ()
        )

        after_materialization = (
            load_pattern_evidence(
                db,
                organization_id=organization.id,
                as_of=(
                    event_time
                    + timedelta(
                        days=3
                    )
                ),
                lookback_days=30,
            )
        )

        assert len(
            after_materialization.topology_shifts
        ) == 1

        assert (
            after_materialization
            .topology_shifts[0]
            .event_id
            == event.id
        )

        assert (
            after_materialization
            .topology_shifts[0]
            .entity_id
            == entity.id
        )

    finally:
        db.rollback()
        db.close()

def test_risk_backfill_is_visible_with_separate_knowledge_cutoff():
    db = SessionLocal()

    try:
        organization = _create_organization(
            db,
            prefix="pattern-risk-backfill",
        )

        entity, page = (
            _create_entity_and_page(
                db,
                organization=organization,
                suffix="RiskBackfill",
            )
        )

        event_time = (
            BASE
            + timedelta(
                days=5
            )
        )

        temporal_as_of = (
            event_time
            + timedelta(
                days=1
            )
        )

        materialized_at = (
            event_time
            + timedelta(
                days=10
            )
        )

        event = _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=event_time,
            suffix="risk-backfill",
        )

        _create_risk_assessment(
            db,
            organization=organization,
            event=event,
            assessed_at=event_time,
            created_at=materialized_at,
            score=0.81,
        )

        db.flush()

        without_separate_cutoff = (
            load_pattern_evidence(
                db,
                organization_id=organization.id,
                as_of=temporal_as_of,
                lookback_days=30,
            )
        )

        assert (
            without_separate_cutoff.risk_assessments
            == ()
        )

        with_separate_cutoff = (
            load_pattern_evidence(
                db,
                organization_id=organization.id,
                as_of=temporal_as_of,
                knowledge_cutoff=materialized_at,
                lookback_days=30,
            )
        )

        assert len(
            with_separate_cutoff.risk_assessments
        ) == 1

        assert (
            with_separate_cutoff
            .risk_assessments[0]
            .event_id
            == event.id
        )

        assert (
            with_separate_cutoff
            .risk_assessments[0]
            .entity_id
            == entity.id
        )

        assert (
            with_separate_cutoff
            .risk_assessments[0]
            .occurred_at
            == event_time
        )

    finally:
        db.rollback()
        db.close()

def test_risk_knowledge_cutoff_uses_uuid_tiebreaker():
    db = SessionLocal()

    try:
        organization = _create_organization(
            db,
            prefix="pattern-risk-cutoff-id",
        )

        entity, page = (
            _create_entity_and_page(
                db,
                organization=organization,
                suffix="RiskCutoffId",
            )
        )

        event_time = (
            BASE
            + timedelta(
                days=5
            )
        )

        temporal_as_of = (
            event_time
            + timedelta(
                days=1
            )
        )

        materialized_at = (
            event_time
            + timedelta(
                days=10
            )
        )

        low_id = uuid.UUID(
            "00000000-0000-0000-0000-000000000001"
        )
        cutoff_id = uuid.UUID(
            "80000000-0000-0000-0000-000000000000"
        )
        high_id = uuid.UUID(
            "ffffffff-ffff-ffff-ffff-ffffffffffff"
        )

        low_event = _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=event_time,
            suffix="risk-cutoff-low",
        )

        cutoff_event = _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=event_time,
            suffix="risk-cutoff-exact",
        )

        high_event = _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=event_time,
            suffix="risk-cutoff-high",
        )

        low_risk = _create_risk_assessment(
            db,
            organization=organization,
            event=low_event,
            assessed_at=event_time,
            created_at=materialized_at,
            score=0.61,
        )
        low_risk.id = low_id

        cutoff_risk = _create_risk_assessment(
            db,
            organization=organization,
            event=cutoff_event,
            assessed_at=event_time,
            created_at=materialized_at,
            score=0.71,
        )
        cutoff_risk.id = cutoff_id

        high_risk = _create_risk_assessment(
            db,
            organization=organization,
            event=high_event,
            assessed_at=event_time,
            created_at=materialized_at,
            score=0.81,
        )
        high_risk.id = high_id

        db.flush()

        result = load_pattern_evidence(
            db,
            organization_id=organization.id,
            as_of=temporal_as_of,
            knowledge_cutoff=materialized_at,
            knowledge_cutoff_record_id=cutoff_id,
            lookback_days=30,
        )

        visible_event_ids = {
            item.event_id
            for item in result.risk_assessments
        }

        assert visible_event_ids == {
            low_event.id,
            cutoff_event.id,
        }

        assert (
            high_event.id
            not in visible_event_ids
        )

        assert all(
            item.entity_id == entity.id
            for item in result.risk_assessments
        )

    finally:
        db.rollback()
        db.close()

def test_pattern_evidence_loader_is_read_only():
    db = SessionLocal()

    try:
        organization = _create_organization(
            db,
            prefix="pattern-read-only",
        )

        _, page = (
            _create_entity_and_page(
                db,
                organization=organization,
                suffix="ReadOnly",
            )
        )

        _create_event(
            db,
            organization=organization,
            page=page,
            detected_at=BASE,
            suffix="read-only",
        )

        db.flush()

        assert not db.new
        assert not db.dirty
        assert not db.deleted

        load_pattern_evidence(
            db,
            organization_id=organization.id,
            as_of=(
                BASE
                + timedelta(
                    days=1
                )
            ),
            lookback_days=30,
        )

        assert not db.new
        assert not db.dirty
        assert not db.deleted

    finally:
        db.rollback()
        db.close()

def test_pattern_risk_evidence_excludes_unqualified_historical_assessments():
    """Legacy assessments must not become new risk escalation evidence."""
    db = SessionLocal()

    try:
        organization = _create_organization(
            db, prefix="pattern-qualified-risk"
        )
        _, page = _create_entity_and_page(
            db, organization=organization, suffix="QualifiedRisk"
        )
        at = BASE + timedelta(days=4)
        expected_event_ids = set()
        all_event_ids = set()

        cases = (
            ("completed", "completed", True),
            ("pending", "completed", False),
            ("failed", "completed", False),
            ("completed", "pending", False),
            ("completed", "failed", False),
        )
        for number, (analysis, vector, qualified) in enumerate(cases):
            event = _create_event(
                db,
                organization=organization,
                page=page,
                detected_at=at + timedelta(minutes=number),
                suffix=f"risk-status-{number}",
            )
            event.analysis_status = analysis
            event.vector_analysis_status = vector
            all_event_ids.add(event.id)
            if qualified:
                expected_event_ids.add(event.id)
            _create_risk_assessment(
                db,
                organization=organization,
                event=event,
                assessed_at=at + timedelta(minutes=number),
                created_at=at + timedelta(minutes=number),
                score=0.60 + 0.05 * number,
            )

        db.flush()
        result = load_pattern_evidence(
            db,
            organization_id=organization.id,
            as_of=at + timedelta(days=1),
            lookback_days=30,
        )

        assert {
            item.event_id for item in result.risk_assessments
        } == expected_event_ids
        # A risk-evidence qualification gate must not erase audit events.
        assert all_event_ids.issubset(
            {item.event_id for item in result.events}
        )
    finally:
        db.rollback()
        db.close()
