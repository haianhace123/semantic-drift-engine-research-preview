from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from src.core.database import SessionLocal
from src.models.entity import Entity
from src.models.entity_relationship import (
    EntityRelationship,
)
from src.models.entity_relationship_observation import (
    EntityRelationshipObservation,
)
from src.models.monitored_page import MonitoredPage
from src.models.organization import Organization
from src.models.page_snapshot import PageSnapshot
from src.models.semantic_drift_event import (
    SemanticDriftEvent,
)
from src.services.graph_metric_snapshot_service import (
    persist_graph_metric_snapshots_for_event,
)
from src.services.temporal_topology_service import (
    compare_event_topology,
)


def _make_organization(
    db: Session,
    *,
    suffix: str,
) -> Organization:
    organization = Organization(
        id=uuid.uuid4(),
        name=f"temporal-topology-{suffix}",
        slug=(
            f"temporal-topology-{suffix}-"
            f"{uuid.uuid4().hex[:8]}"
        ),
        billing_email=(
            f"temporal-topology-{suffix}-"
            f"{uuid.uuid4().hex[:8]}"
            "@example.com"
        ),
    )

    db.add(organization)
    db.flush()

    return organization


def _make_entity(
    db: Session,
    *,
    organization_id: uuid.UUID,
    suffix: str,
) -> Entity:
    entity = Entity(
        id=uuid.uuid4(),
        organization_id=organization_id,
        name=f"Temporal Topology {suffix}",
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


def _make_monitored_page(
    db: Session,
    *,
    organization_id: uuid.UUID,
    entity_id: uuid.UUID,
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


def _make_page_snapshot(
    db: Session,
    *,
    organization_id: uuid.UUID,
    monitored_page_id: uuid.UUID,
    suffix: str,
) -> PageSnapshot:
    raw_content = f"temporal topology {suffix}"

    snapshot = PageSnapshot(
        id=uuid.uuid4(),
        organization_id=organization_id,
        monitored_page_id=monitored_page_id,
        content_hash=(
            f"{suffix}-{uuid.uuid4().hex}"
        ),
        raw_content=raw_content,
        content_size=len(raw_content),
    )

    db.add(snapshot)
    db.flush()

    return snapshot


def _make_event(
    db: Session,
    *,
    organization_id: uuid.UUID,
    monitored_page_id: uuid.UUID,
    detected_at: datetime,
    suffix: str,
) -> SemanticDriftEvent:
    old_snapshot = _make_page_snapshot(
        db,
        organization_id=organization_id,
        monitored_page_id=monitored_page_id,
        suffix=f"{suffix}-old",
    )

    new_snapshot = _make_page_snapshot(
        db,
        organization_id=organization_id,
        monitored_page_id=monitored_page_id,
        suffix=f"{suffix}-new",
    )

    event = SemanticDriftEvent(
        id=uuid.uuid4(),
        organization_id=organization_id,
        monitored_page_id=monitored_page_id,
        old_snapshot_id=old_snapshot.id,
        new_snapshot_id=new_snapshot.id,
        semantic_delta_json={},
        impact_score=0,
        entities_affected=[],
        related_events=[],
        detected_at=detected_at,
    )

    db.add(event)
    db.flush()

    return event


def _make_relationship(
    db: Session,
    *,
    organization_id: uuid.UUID,
    source_entity_id: uuid.UUID,
    target_entity_id: uuid.UUID,
    observed_at: datetime,
    semantic_drift_event_id: uuid.UUID,
) -> EntityRelationship:
    relationship = EntityRelationship(
        id=uuid.uuid4(),
        organization_id=organization_id,
        source_entity_id=source_entity_id,
        target_entity_id=target_entity_id,
        relationship_type="mentioned",
        evidence_method="test",
        evidence_count=1,
        strength=1.0,
        confidence=1.0,
        first_seen_at=observed_at,
        last_seen_at=observed_at,
        first_event_id=semantic_drift_event_id,
        last_event_id=semantic_drift_event_id,
        is_active=True,
    )

    db.add(relationship)
    db.flush()

    observation = EntityRelationshipObservation(
        id=uuid.uuid4(),
        organization_id=organization_id,
        relationship_id=relationship.id,
        semantic_drift_event_id=semantic_drift_event_id,
        source_entity_id=source_entity_id,
        target_entity_id=target_entity_id,
        relationship_type="mentioned",
        evidence_method="test",
        observed_at=observed_at,
        created_at=observed_at,
    )

    db.add(observation)
    db.flush()

    return relationship


def _build_base_fixture(
    db: Session,
    *,
    suffix: str,
):
    organization = _make_organization(
        db,
        suffix=suffix,
    )

    first = _make_entity(
        db,
        organization_id=organization.id,
        suffix=f"{suffix}-first",
    )

    second = _make_entity(
        db,
        organization_id=organization.id,
        suffix=f"{suffix}-second",
    )

    third = _make_entity(
        db,
        organization_id=organization.id,
        suffix=f"{suffix}-third",
    )

    page = _make_monitored_page(
        db,
        organization_id=organization.id,
        entity_id=first.id,
        suffix=suffix,
    )

    return (
        organization,
        first,
        second,
        third,
        page,
    )


def _cleanup_organization(
    db: Session,
    organization_id: uuid.UUID | None,
) -> None:
    if organization_id is None:
        return

    db.rollback()

    db.execute(
        delete(Organization).where(
            Organization.id
            == organization_id
        )
    )

    db.commit()


def test_temporal_topology_uses_nearest_previous_state():
    db = SessionLocal()
    organization_id = None

    try:
        t1 = datetime(
            2026, 8, 30, 8, 0,
            tzinfo=timezone.utc,
        )
        t2 = datetime(
            2026, 8, 30, 9, 0,
            tzinfo=timezone.utc,
        )
        t3 = datetime(
            2026, 8, 30, 10, 0,
            tzinfo=timezone.utc,
        )

        (
            organization,
            first,
            second,
            third,
            page,
        ) = _build_base_fixture(
            db,
            suffix=uuid.uuid4().hex[:8],
        )

        organization_id = organization.id

        event_t1 = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=t1,
            suffix="t1",
        )

        _make_relationship(
            db,
            organization_id=organization.id,
            source_entity_id=first.id,
            target_entity_id=second.id,
            observed_at=t1,
            semantic_drift_event_id=event_t1.id,
        )

        db.commit()

        persist_graph_metric_snapshots_for_event(
            db=db,
            event=event_t1,
        )
        db.commit()

        event_t2 = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=t2,
            suffix="t2",
        )

        _make_relationship(
            db,
            organization_id=organization.id,
            source_entity_id=second.id,
            target_entity_id=third.id,
            observed_at=t2,
            semantic_drift_event_id=event_t2.id,
        )

        db.commit()

        persist_graph_metric_snapshots_for_event(
            db=db,
            event=event_t2,
        )
        db.commit()

        # Persist a future state before comparing T2.
        # It must never leak backwards.
        event_t3 = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=t3,
            suffix="t3",
        )

        _make_relationship(
            db,
            organization_id=organization.id,
            source_entity_id=third.id,
            target_entity_id=first.id,
            observed_at=t3,
            semantic_drift_event_id=event_t3.id,
        )

        db.commit()

        persist_graph_metric_snapshots_for_event(
            db=db,
            event=event_t3,
        )
        db.commit()

        result = compare_event_topology(
            db=db,
            event=event_t2,
        )

        assert result.has_previous_state is True
        assert result.previous_event_id == event_t1.id
        assert result.previous_observed_at == t1
        assert result.current_event_id == event_t2.id
        assert result.current_observed_at == t2

        assert (
            result.shift.previous_active_edge_count
            == 1
        )
        assert (
            result.shift.current_active_edge_count
            == 2
        )
        assert (
            result.shift.active_edge_count_delta
            == 1
        )

        by_entity = {
            shift.entity_id: shift
            for shift in result.shift.entities
        }

        # T2 adds B -> C.
        # A therefore gains indirect downstream reach.
        assert (
            by_entity[first.id].downstream_reach_delta
            == 1
        )

        assert (
            by_entity[first.id].classification
            == "downstream_gain"
        )

        assert (
            by_entity[second.id].out_degree_delta
            == 1
        )

        # If T3 leaked into T2, the graph would already contain
        # C -> A and this assertion would fail.
        assert (
            by_entity[third.id].out_degree_delta
            == 0
        )

    finally:
        try:
            _cleanup_organization(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_first_temporal_state_uses_empty_baseline():
    db = SessionLocal()
    organization_id = None

    try:
        detected_at = datetime(
            2026, 8, 30, 11, 0,
            tzinfo=timezone.utc,
        )

        (
            organization,
            first,
            second,
            _third,
            page,
        ) = _build_base_fixture(
            db,
            suffix=uuid.uuid4().hex[:8],
        )

        organization_id = organization.id

        event = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=detected_at,
            suffix="first",
        )

        _make_relationship(
            db,
            organization_id=organization.id,
            source_entity_id=first.id,
            target_entity_id=second.id,
            observed_at=detected_at,
            semantic_drift_event_id=event.id,
        )

        db.commit()

        persist_graph_metric_snapshots_for_event(
            db=db,
            event=event,
        )
        db.commit()

        result = compare_event_topology(
            db=db,
            event=event,
        )

        assert result.has_previous_state is False
        assert result.previous_event_id is None
        assert result.previous_observed_at is None

        assert result.shift.previous_entity_count == 0
        assert result.shift.current_entity_count == 3
        assert result.shift.previous_active_edge_count == 0
        assert result.shift.current_active_edge_count == 1

    finally:
        try:
            _cleanup_organization(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_same_timestamp_does_not_create_fake_chronology():
    db = SessionLocal()
    organization_id = None

    try:
        same_time = datetime(
            2026, 8, 30, 12, 0,
            tzinfo=timezone.utc,
        )

        (
            organization,
            first,
            second,
            third,
            page,
        ) = _build_base_fixture(
            db,
            suffix=uuid.uuid4().hex[:8],
        )

        organization_id = organization.id

        first_event = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=same_time,
            suffix="same-a",
        )

        _make_relationship(
            db,
            organization_id=organization.id,
            source_entity_id=first.id,
            target_entity_id=second.id,
            observed_at=same_time,
            semantic_drift_event_id=first_event.id,
        )

        db.commit()

        persist_graph_metric_snapshots_for_event(
            db=db,
            event=first_event,
        )
        db.commit()

        second_event = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=same_time,
            suffix="same-b",
        )

        _make_relationship(
            db,
            organization_id=organization.id,
            source_entity_id=second.id,
            target_entity_id=third.id,
            observed_at=same_time,
            semantic_drift_event_id=second_event.id,
        )

        db.commit()

        persist_graph_metric_snapshots_for_event(
            db=db,
            event=second_event,
        )
        db.commit()

        result = compare_event_topology(
            db=db,
            event=second_event,
        )

        assert result.has_previous_state is False
        assert result.previous_event_id is None
        assert result.previous_observed_at is None

    finally:
        try:
            _cleanup_organization(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_temporal_topology_is_tenant_isolated():
    db = SessionLocal()

    first_organization_id = None
    second_organization_id = None

    try:
        earlier = datetime(
            2026, 8, 30, 13, 0,
            tzinfo=timezone.utc,
        )
        current = datetime(
            2026, 8, 30, 14, 0,
            tzinfo=timezone.utc,
        )

        (
            first_org,
            first_a,
            first_b,
            _first_c,
            first_page,
        ) = _build_base_fixture(
            db,
            suffix=f"a-{uuid.uuid4().hex[:8]}",
        )

        first_organization_id = first_org.id

        (
            second_org,
            second_a,
            second_b,
            _second_c,
            second_page,
        ) = _build_base_fixture(
            db,
            suffix=f"b-{uuid.uuid4().hex[:8]}",
        )

        second_organization_id = second_org.id

        # Other tenant gets an earlier temporal state.
        other_event = _make_event(
            db,
            organization_id=second_org.id,
            monitored_page_id=second_page.id,
            detected_at=earlier,
            suffix="other-earlier",
        )

        _make_relationship(
            db,
            organization_id=second_org.id,
            source_entity_id=second_a.id,
            target_entity_id=second_b.id,
            observed_at=earlier,
            semantic_drift_event_id=other_event.id,
        )

        db.commit()

        persist_graph_metric_snapshots_for_event(
            db=db,
            event=other_event,
        )
        db.commit()

        # First tenant has only a current state.
        first_event = _make_event(
            db,
            organization_id=first_org.id,
            monitored_page_id=first_page.id,
            detected_at=current,
            suffix="first-current",
        )

        _make_relationship(
            db,
            organization_id=first_org.id,
            source_entity_id=first_a.id,
            target_entity_id=first_b.id,
            observed_at=current,
            semantic_drift_event_id=first_event.id,
        )

        db.commit()

        persist_graph_metric_snapshots_for_event(
            db=db,
            event=first_event,
        )
        db.commit()

        result = compare_event_topology(
            db=db,
            event=first_event,
        )

        assert result.organization_id == first_org.id
        assert result.has_previous_state is False
        assert result.previous_event_id is None

    finally:
        db.rollback()

        try:
            if first_organization_id is not None:
                db.execute(
                    delete(Organization).where(
                        Organization.id
                        == first_organization_id
                    )
                )

            if second_organization_id is not None:
                db.execute(
                    delete(Organization).where(
                        Organization.id
                        == second_organization_id
                    )
                )

            db.commit()

        except Exception:
            db.rollback()
            raise

        finally:
            db.close()


def test_backfilled_event_uses_semantic_chronology():
    db = SessionLocal()
    organization_id = None

    try:
        t1 = datetime(
            2026, 8, 30, 15, 0,
            tzinfo=timezone.utc,
        )
        t2 = datetime(
            2026, 8, 30, 16, 0,
            tzinfo=timezone.utc,
        )
        t3 = datetime(
            2026, 8, 30, 17, 0,
            tzinfo=timezone.utc,
        )

        (
            organization,
            first,
            second,
            third,
            page,
        ) = _build_base_fixture(
            db,
            suffix=uuid.uuid4().hex[:8],
        )

        organization_id = organization.id

        # Persist T1.
        event_t1 = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=t1,
            suffix="backfill-t1",
        )

        _make_relationship(
            db,
            organization_id=organization.id,
            source_entity_id=first.id,
            target_entity_id=second.id,
            observed_at=t1,
            semantic_drift_event_id=event_t1.id,
        )

        db.commit()

        persist_graph_metric_snapshots_for_event(
            db=db,
            event=event_t1,
        )
        db.commit()

        # Persist T3 before T2.
        event_t3 = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=t3,
            suffix="backfill-t3",
        )

        _make_relationship(
            db,
            organization_id=organization.id,
            source_entity_id=third.id,
            target_entity_id=first.id,
            observed_at=t3,
            semantic_drift_event_id=event_t3.id,
        )

        db.commit()

        persist_graph_metric_snapshots_for_event(
            db=db,
            event=event_t3,
        )
        db.commit()

        # T2 arrives late.
        event_t2 = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=t2,
            suffix="backfill-t2",
        )

        _make_relationship(
            db,
            organization_id=organization.id,
            source_entity_id=second.id,
            target_entity_id=third.id,
            observed_at=t2,
            semantic_drift_event_id=event_t2.id,
        )

        db.commit()

        persist_graph_metric_snapshots_for_event(
            db=db,
            event=event_t2,
        )
        db.commit()

        result = compare_event_topology(
            db=db,
            event=event_t2,
        )

        assert result.has_previous_state is True
        assert result.previous_event_id == event_t1.id
        assert result.previous_observed_at == t1

        assert (
            result.shift.previous_active_edge_count
            == 1
        )

        assert (
            result.shift.current_active_edge_count
            == 2
        )

        assert (
            result.shift.active_edge_count_delta
            == 1
        )

        by_entity = {
            shift.entity_id: shift
            for shift in result.shift.entities
        }

        # T3 was physically persisted before T2 but is semantically later.
        # It must not appear in T2 topology.
        assert (
            by_entity[third.id].out_degree_delta
            == 0
        )

    finally:
        try:
            _cleanup_organization(
                db,
                organization_id,
            )
        finally:
            db.close()