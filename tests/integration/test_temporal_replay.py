from __future__ import annotations

import uuid

from datetime import datetime, timezone

import pytest
from sqlalchemy import delete

from src.core.database import SessionLocal
from src.models.entity import Entity
from src.models.entity_graph_metric_snapshot import (
    EntityGraphMetricSnapshot,
)
from src.models.entity_relationship import (
    EntityRelationship,
)
from src.models.entity_relationship_observation import (
    EntityRelationshipObservation,
)
from src.models.monitored_page import MonitoredPage
from src.models.organization import Organization
from src.models.page_snapshot import PageSnapshot
from src.models.risk_assessment import RiskAssessment
from src.models.semantic_drift_event import (
    SemanticDriftEvent,
)
from src.models.topology_shift_snapshot import (
    TopologyShiftSnapshot,
)
from src.services.temporal_replay_service import (
    RISK_VERSION_V2,
    replay_organization_at,
)


def _utc(
    hour: int,
    minute: int = 0,
) -> datetime:
    return datetime(
        2026,
        8,
        30,
        hour,
        minute,
        tzinfo=timezone.utc,
    )


def _make_organization(
    db,
    *,
    suffix: str,
) -> Organization:
    organization = Organization(
        id=uuid.uuid4(),
        name=f"temporal-replay-{suffix}",
        slug=(
            f"temporal-replay-{suffix}-"
            f"{uuid.uuid4().hex[:8]}"
        ),
        billing_email=(
            f"temporal-replay-{suffix}-"
            f"{uuid.uuid4().hex[:8]}"
            "@example.com"
        ),
    )

    db.add(organization)
    db.flush()

    return organization


def _make_entity(
    db,
    *,
    organization_id,
    suffix: str,
) -> Entity:
    entity = Entity(
        id=uuid.uuid4(),
        organization_id=organization_id,
        name=f"Temporal Replay {suffix}",
        domain=(
            f"{suffix}-"
            f"{uuid.uuid4().hex[:8]}"
            ".example.com"
        ),
        category="test",
    )

    db.add(entity)
    db.flush()

    return entity


def _make_page(
    db,
    *,
    organization_id,
    entity_id,
    suffix: str,
) -> MonitoredPage:
    page = MonitoredPage(
        id=uuid.uuid4(),
        organization_id=organization_id,
        entity_id=entity_id,
        url=(
            f"https://{suffix}-"
            f"{uuid.uuid4().hex[:8]}"
            ".example.com/docs"
        ),
        page_type="docs",
        crawl_frequency="hourly",
        is_active=True,
    )

    db.add(page)
    db.flush()

    return page


def _make_snapshot(
    db,
    *,
    organization_id,
    monitored_page_id,
    suffix: str,
    crawled_at: datetime,
    created_at: datetime,
    snapshot_id=None,
) -> PageSnapshot:
    raw_content = f"temporal replay {suffix}"

    snapshot = PageSnapshot(
        id=snapshot_id or uuid.uuid4(),
        organization_id=organization_id,
        monitored_page_id=monitored_page_id,
        content_hash=(
            f"{suffix}-{uuid.uuid4().hex}"
        ),
        raw_content=raw_content,
        content_size=len(raw_content),
        crawled_at=crawled_at,
        created_at=created_at,
    )

    db.add(snapshot)
    db.flush()

    return snapshot


def _make_event(
    db,
    *,
    organization_id,
    monitored_page_id,
    old_snapshot_id,
    new_snapshot_id,
    detected_at: datetime,
    semantic_magnitude: float | None = None,
) -> SemanticDriftEvent:
    event = SemanticDriftEvent(
        id=uuid.uuid4(),
        organization_id=organization_id,
        monitored_page_id=monitored_page_id,
        old_snapshot_id=old_snapshot_id,
        new_snapshot_id=new_snapshot_id,
        semantic_delta_json={},
        impact_score=0,
        entities_affected=[],
        related_events=[],
        detected_at=detected_at,
        semantic_magnitude=semantic_magnitude,
    )

    db.add(event)
    db.flush()

    return event


def _make_graph_state(
    db,
    *,
    organization_id,
    event_id,
    observed_at: datetime,
    created_at: datetime,
    entity_ids,
    active_edge_count: int,
) -> list[EntityGraphMetricSnapshot]:
    entity_ids = list(entity_ids)
    entity_count = len(entity_ids)

    rows = []

    for index, entity_id in enumerate(entity_ids):
        row = EntityGraphMetricSnapshot(
            id=uuid.uuid4(),
            organization_id=organization_id,
            entity_id=entity_id,
            semantic_drift_event_id=event_id,
            observed_at=observed_at,
            entity_count=entity_count,
            active_edge_count=active_edge_count,
            in_degree=0 if index == 0 else 1,
            out_degree=1 if index == 0 else 0,
            degree=1,
            in_degree_centrality=(
                0.0 if index == 0 else 1.0
            ),
            out_degree_centrality=(
                1.0 if index == 0 else 0.0
            ),
            degree_centrality=0.5,
            downstream_reach=(
                1 if index == 0 else 0
            ),
            downstream_reach_ratio=(
                1.0 if index == 0 else 0.0
            ),
            created_at=created_at,
        )

        db.add(row)
        rows.append(row)

    db.flush()

    return rows


def _make_topology_state(
    db,
    *,
    organization_id,
    event_id,
    observed_at: datetime,
    previous_observed_at,
    created_at: datetime,
    entity_ids,
) -> list[TopologyShiftSnapshot]:
    rows = []

    for index, entity_id in enumerate(entity_ids):
        row = TopologyShiftSnapshot(
            id=uuid.uuid4(),
            organization_id=organization_id,
            entity_id=entity_id,
            semantic_drift_event_id=event_id,
            previous_event_id=None,
            observed_at=observed_at,
            previous_observed_at=(
                previous_observed_at
            ),
            entity_count_delta=0,
            active_edge_count_delta=1,
            in_degree_delta=(
                0 if index == 0 else 1
            ),
            out_degree_delta=(
                1 if index == 0 else 0
            ),
            degree_delta=1,
            degree_centrality_delta=0.5,
            downstream_reach_delta=(
                1 if index == 0 else 0
            ),
            downstream_reach_ratio_delta=(
                1.0 if index == 0 else 0.0
            ),
            classification=(
                "downstream_gain"
                if index == 0
                else "centrality_gain"
            ),
            severity="high",
            shift_score=0.6,
            created_at=created_at,
        )

        db.add(row)
        rows.append(row)

    db.flush()

    return rows


def _make_risk_assessment(
    db,
    *,
    organization_id,
    event_id,
    assessed_at: datetime,
    created_at: datetime,
) -> RiskAssessment:
    assessment = RiskAssessment(
        id=uuid.uuid4(),
        organization_id=organization_id,
        semantic_drift_event_id=event_id,
        risk_version=RISK_VERSION_V2,
        assessed_at=assessed_at,
        score=0.72,
        semantic_magnitude_contribution=0.18,
        legacy_impact_contribution=0.10,
        semantic_severity_contribution=0.08,
        page_context_contribution=0.08,
        confidence_contribution=0.07,
        topology_score=0.84,
        topology_contribution=0.21,
        topology_shift_component=0.60,
        topology_centrality_component=0.50,
        topology_downstream_component=1.0,
        has_topology_signal=True,
        has_previous_topology_state=True,
        created_at=created_at,
    )

    db.add(assessment)
    db.flush()

    return assessment


def _cleanup(
    db,
    *,
    organization_ids,
) -> None:
    organization_ids = list(
        organization_ids
    )

    db.execute(
        delete(RiskAssessment).where(
            RiskAssessment.organization_id.in_(
                organization_ids
            )
        )
    )

    db.execute(
        delete(TopologyShiftSnapshot).where(
            TopologyShiftSnapshot.organization_id.in_(
                organization_ids
            )
        )
    )

    db.execute(
        delete(EntityGraphMetricSnapshot).where(
            EntityGraphMetricSnapshot.organization_id.in_(
                organization_ids
            )
        )
    )

    db.execute(
        delete(EntityRelationshipObservation).where(
            EntityRelationshipObservation.organization_id.in_(
                organization_ids
            )
        )
    )

    db.execute(
        delete(EntityRelationship).where(
            EntityRelationship.organization_id.in_(
                organization_ids
            )
        )
    )

    db.execute(
        delete(SemanticDriftEvent).where(
            SemanticDriftEvent.organization_id.in_(
                organization_ids
            )
        )
    )

    db.execute(
        delete(PageSnapshot).where(
            PageSnapshot.organization_id.in_(
                organization_ids
            )
        )
    )

    db.execute(
        delete(MonitoredPage).where(
            MonitoredPage.organization_id.in_(
                organization_ids
            )
        )
    )

    db.execute(
        delete(Entity).where(
            Entity.organization_id.in_(
                organization_ids
            )
        )
    )

    db.execute(
        delete(Organization).where(
            Organization.id.in_(
                organization_ids
            )
        )
    )

    db.commit()


def test_temporal_replay_prevents_future_knowledge_leakage():
    db = SessionLocal()

    organization_ids = []

    try:
        organization = _make_organization(
            db,
            suffix="chronology",
        )
        organization_ids.append(
            organization.id
        )

        first = _make_entity(
            db,
            organization_id=organization.id,
            suffix="chronology-first",
        )

        second = _make_entity(
            db,
            organization_id=organization.id,
            suffix="chronology-second",
        )

        page = _make_page(
            db,
            organization_id=organization.id,
            entity_id=first.id,
            suffix="chronology",
        )

        snapshot_a = _make_snapshot(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            suffix="snapshot-a",
            crawled_at=_utc(1),
            created_at=_utc(1),
        )

        snapshot_b = _make_snapshot(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            suffix="snapshot-b",
            crawled_at=_utc(2),
            created_at=_utc(2),
        )

        event = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            old_snapshot_id=snapshot_a.id,
            new_snapshot_id=snapshot_b.id,
            detected_at=_utc(2),
            semantic_magnitude=0.8,
        )

        _make_graph_state(
            db,
            organization_id=organization.id,
            event_id=event.id,
            observed_at=_utc(2),
            created_at=_utc(3),
            entity_ids=[
                first.id,
                second.id,
            ],
            active_edge_count=1,
        )

        _make_topology_state(
            db,
            organization_id=organization.id,
            event_id=event.id,
            observed_at=_utc(2),
            previous_observed_at=None,
            created_at=_utc(3),
            entity_ids=[
                first.id,
                second.id,
            ],
        )

        _make_risk_assessment(
            db,
            organization_id=organization.id,
            event_id=event.id,
            assessed_at=_utc(2),
            created_at=_utc(4),
        )

        db.commit()

        replay_t1 = replay_organization_at(
            db,
            organization_id=organization.id,
            cursor=_utc(1, 30),
        )

        assert len(
            replay_t1.snapshots
        ) == 1
        assert (
            replay_t1.snapshots[0].snapshot_id
            == snapshot_a.id
        )
        assert replay_t1.events == ()
        assert replay_t1.graph is None
        assert replay_t1.topology is None
        assert (
            replay_t1.risk_assessments
            == ()
        )

        replay_t2 = replay_organization_at(
            db,
            organization_id=organization.id,
            cursor=_utc(2, 30),
        )

        assert len(
            replay_t2.snapshots
        ) == 1
        assert (
            replay_t2.snapshots[0].snapshot_id
            == snapshot_b.id
        )

        assert [
            row.event_id
            for row in replay_t2.events
        ] == [
            event.id
        ]

        assert replay_t2.graph is None
        assert replay_t2.topology is None
        assert (
            replay_t2.risk_assessments
            == ()
        )

        replay_t3 = replay_organization_at(
            db,
            organization_id=organization.id,
            cursor=_utc(3, 30),
        )

        assert replay_t3.graph is not None
        assert (
            replay_t3.graph.observed_at
            == _utc(2)
        )
        assert (
            replay_t3.graph.created_at
            == _utc(3)
        )
        assert (
            replay_t3.graph.entity_count
            == 2
        )

        assert (
            replay_t3.topology
            is not None
        )
        assert (
            replay_t3.topology.observed_at
            == _utc(2)
        )
        assert (
            replay_t3.topology.created_at
            == _utc(3)
        )

        assert (
            replay_t3.risk_assessments
            == ()
        )

        replay_t4 = replay_organization_at(
            db,
            organization_id=organization.id,
            cursor=_utc(4, 30),
        )

        assert replay_t4.graph is not None
        assert (
            replay_t4.topology
            is not None
        )

        assert len(
            replay_t4.risk_assessments
        ) == 1

        assessment = (
            replay_t4.risk_assessments[0]
        )

        assert (
            assessment.semantic_drift_event_id
            == event.id
        )
        assert (
            assessment.assessed_at
            == _utc(2)
        )
        assert (
            assessment.created_at
            == _utc(4)
        )
        assert assessment.score == 0.72

    finally:
        db.rollback()

        if organization_ids:
            _cleanup(
                db,
                organization_ids=(
                    organization_ids
                ),
            )

        db.close()


def test_temporal_replay_snapshot_tie_break_matches_api():
    db = SessionLocal()
    organization_ids = []

    try:
        organization = _make_organization(
            db,
            suffix="tie-break",
        )
        organization_ids.append(
            organization.id
        )

        entity = _make_entity(
            db,
            organization_id=organization.id,
            suffix="tie-break",
        )

        page = _make_page(
            db,
            organization_id=organization.id,
            entity_id=entity.id,
            suffix="tie-break",
        )

        lower_id = uuid.UUID(
            "00000000-0000-0000-0000-000000000001"
        )

        higher_id = uuid.UUID(
            "ffffffff-ffff-ffff-ffff-ffffffffffff"
        )

        _make_snapshot(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            suffix="lower",
            crawled_at=_utc(5),
            created_at=_utc(5),
            snapshot_id=lower_id,
        )

        higher = _make_snapshot(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            suffix="higher",
            crawled_at=_utc(5),
            created_at=_utc(5),
            snapshot_id=higher_id,
        )

        db.commit()

        replay = replay_organization_at(
            db,
            organization_id=organization.id,
            cursor=_utc(5, 30),
        )

        assert len(replay.snapshots) == 1

        assert (
            replay.snapshots[0].snapshot_id
            == higher.id
        )

    finally:
        db.rollback()

        if organization_ids:
            _cleanup(
                db,
                organization_ids=(
                    organization_ids
                ),
            )

        db.close()


def test_temporal_replay_is_tenant_isolated():
    db = SessionLocal()
    organization_ids = []

    try:
        organization_a = _make_organization(
            db,
            suffix="tenant-a",
        )

        organization_b = _make_organization(
            db,
            suffix="tenant-b",
        )

        organization_ids.extend(
            [
                organization_a.id,
                organization_b.id,
            ]
        )

        entity_a = _make_entity(
            db,
            organization_id=organization_a.id,
            suffix="tenant-a",
        )

        entity_b = _make_entity(
            db,
            organization_id=organization_b.id,
            suffix="tenant-b",
        )

        page_a = _make_page(
            db,
            organization_id=organization_a.id,
            entity_id=entity_a.id,
            suffix="tenant-a",
        )

        page_b = _make_page(
            db,
            organization_id=organization_b.id,
            entity_id=entity_b.id,
            suffix="tenant-b",
        )

        snapshot_a1 = _make_snapshot(
            db,
            organization_id=organization_a.id,
            monitored_page_id=page_a.id,
            suffix="tenant-a-1",
            crawled_at=_utc(1),
            created_at=_utc(1),
        )

        snapshot_a2 = _make_snapshot(
            db,
            organization_id=organization_a.id,
            monitored_page_id=page_a.id,
            suffix="tenant-a-2",
            crawled_at=_utc(2),
            created_at=_utc(2),
        )

        event_a = _make_event(
            db,
            organization_id=organization_a.id,
            monitored_page_id=page_a.id,
            old_snapshot_id=snapshot_a1.id,
            new_snapshot_id=snapshot_a2.id,
            detected_at=_utc(2),
        )

        snapshot_b1 = _make_snapshot(
            db,
            organization_id=organization_b.id,
            monitored_page_id=page_b.id,
            suffix="tenant-b-1",
            crawled_at=_utc(1),
            created_at=_utc(1),
        )

        snapshot_b2 = _make_snapshot(
            db,
            organization_id=organization_b.id,
            monitored_page_id=page_b.id,
            suffix="tenant-b-2",
            crawled_at=_utc(2),
            created_at=_utc(2),
        )

        event_b = _make_event(
            db,
            organization_id=organization_b.id,
            monitored_page_id=page_b.id,
            old_snapshot_id=snapshot_b1.id,
            new_snapshot_id=snapshot_b2.id,
            detected_at=_utc(2),
        )

        db.commit()

        replay = replay_organization_at(
            db,
            organization_id=organization_a.id,
            cursor=_utc(10),
        )

        assert {
            row.snapshot_id
            for row in replay.snapshots
        } == {
            snapshot_a2.id
        }

        assert {
            row.event_id
            for row in replay.events
        } == {
            event_a.id
        }

        assert (
            event_b.id
            not in {
                row.event_id
                for row in replay.events
            }
        )

    finally:
        db.rollback()

        if organization_ids:
            _cleanup(
                db,
                organization_ids=(
                    organization_ids
                ),
            )

        db.close()


def test_temporal_replay_rejects_incomplete_graph_state():
    db = SessionLocal()
    organization_ids = []

    try:
        organization = _make_organization(
            db,
            suffix="incomplete-graph",
        )
        organization_ids.append(
            organization.id
        )

        first = _make_entity(
            db,
            organization_id=organization.id,
            suffix="incomplete-first",
        )

        second = _make_entity(
            db,
            organization_id=organization.id,
            suffix="incomplete-second",
        )

        page = _make_page(
            db,
            organization_id=organization.id,
            entity_id=first.id,
            suffix="incomplete",
        )

        old_snapshot = _make_snapshot(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            suffix="incomplete-old",
            crawled_at=_utc(1),
            created_at=_utc(1),
        )

        new_snapshot = _make_snapshot(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            suffix="incomplete-new",
            crawled_at=_utc(2),
            created_at=_utc(2),
        )

        event = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            old_snapshot_id=old_snapshot.id,
            new_snapshot_id=new_snapshot.id,
            detected_at=_utc(2),
        )

        row = EntityGraphMetricSnapshot(
            id=uuid.uuid4(),
            organization_id=organization.id,
            entity_id=first.id,
            semantic_drift_event_id=event.id,
            observed_at=_utc(2),
            entity_count=2,
            active_edge_count=1,
            in_degree=0,
            out_degree=1,
            degree=1,
            in_degree_centrality=0.0,
            out_degree_centrality=1.0,
            degree_centrality=0.5,
            downstream_reach=1,
            downstream_reach_ratio=1.0,
            created_at=_utc(3),
        )

        db.add(row)
        db.commit()

        with pytest.raises(
            ValueError,
            match=(
                "Persisted graph state "
                "is incomplete"
            ),
        ):
            replay_organization_at(
                db,
                organization_id=organization.id,
                cursor=_utc(4),
            )

    finally:
        db.rollback()

        if organization_ids:
            _cleanup(
                db,
                organization_ids=(
                    organization_ids
                ),
            )

        db.close()


def test_temporal_replay_is_read_only():
    db = SessionLocal()
    organization_ids = []

    try:
        organization = _make_organization(
            db,
            suffix="read-only",
        )
        organization_ids.append(
            organization.id
        )

        entity = _make_entity(
            db,
            organization_id=organization.id,
            suffix="read-only",
        )

        page = _make_page(
            db,
            organization_id=organization.id,
            entity_id=entity.id,
            suffix="read-only",
        )

        _make_snapshot(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            suffix="read-only",
            crawled_at=_utc(1),
            created_at=_utc(1),
        )

        db.commit()

        before_new = len(db.new)
        before_dirty = len(db.dirty)
        before_deleted = len(db.deleted)

        replay_organization_at(
            db,
            organization_id=organization.id,
            cursor=_utc(10),
        )

        assert len(db.new) == before_new
        assert len(db.dirty) == before_dirty
        assert (
            len(db.deleted)
            == before_deleted
        )

    finally:
        db.rollback()

        if organization_ids:
            _cleanup(
                db,
                organization_ids=(
                    organization_ids
                ),
            )

        db.close()