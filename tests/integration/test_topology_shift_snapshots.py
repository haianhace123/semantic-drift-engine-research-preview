from __future__ import annotations

import uuid
from datetime import datetime, timezone

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Barrier, Event
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
from src.models.topology_shift_snapshot import (
    TopologyShiftSnapshot,
)
from src.services.event_evidence_lock import (
    acquire_event_evidence_lock,
)
from src.services.graph_metric_snapshot_service import (
    persist_graph_metric_snapshots_for_event,
)
from src.services.topology_shift_snapshot_service import (
    persist_topology_shift_snapshots_for_event,
)
from src.models.risk_assessment import RiskAssessment
from src.services.risk_assessment_service import (
    persist_risk_assessment_v2,
)


def _make_organization(
    db: Session,
    *,
    suffix: str,
) -> Organization:
    organization = Organization(
        id=uuid.uuid4(),
        name=f"topology-shift-{suffix}",
        slug=(
            f"topology-shift-{suffix}-"
            f"{uuid.uuid4().hex[:8]}"
        ),
        billing_email=(
            f"topology-shift-{suffix}-"
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
        name=f"Topology Shift {suffix}",
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
    raw_content = f"topology shift {suffix}"

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


def _build_fixture(
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
        suffix=f"{suffix}-a",
    )

    second = _make_entity(
        db,
        organization_id=organization.id,
        suffix=f"{suffix}-b",
    )

    third = _make_entity(
        db,
        organization_id=organization.id,
        suffix=f"{suffix}-c",
    )

    page = _make_page(
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


def _cleanup(
    db: Session,
    organization_id: uuid.UUID | None,
) -> None:
    if organization_id is None:
        return

    db.rollback()

    db.execute(
        delete(Organization).where(
            Organization.id == organization_id
        )
    )

    db.commit()


def test_persists_topology_shift_for_entire_graph():
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

        (
            organization,
            first,
            second,
            third,
            page,
        ) = _build_fixture(
            db,
            suffix=uuid.uuid4().hex[:8],
        )

        organization_id = organization.id

        first_event = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=t1,
            suffix="first",
        )

        _make_relationship(
            db,
            organization_id=organization.id,
            source_entity_id=first.id,
            target_entity_id=second.id,
            observed_at=t1,
            semantic_drift_event_id=first_event.id,
        )

        db.commit()

        persist_graph_metric_snapshots_for_event(
            db=db,
            event=first_event,
        )
        db.commit()

        # Persist baseline topology as well. This is not required for
        # comparison, but verifies a complete event pipeline.
        first_result = (
            persist_topology_shift_snapshots_for_event(
                db=db,
                event=first_event,
            )
        )
        db.commit()

        assert first_result.created == 3
        assert first_result.existing == 0
        assert first_result.previous_event_id is None

        second_event = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=t2,
            suffix="second",
        )

        _make_relationship(
            db,
            organization_id=organization.id,
            source_entity_id=second.id,
            target_entity_id=third.id,
            observed_at=t2,
            semantic_drift_event_id=second_event.id,
        )

        db.commit()

        persist_graph_metric_snapshots_for_event(
            db=db,
            event=second_event,
        )
        db.commit()

        result = (
            persist_topology_shift_snapshots_for_event(
                db=db,
                event=second_event,
            )
        )
        db.commit()

        assert result.organization_id == organization.id
        assert result.event_id == second_event.id
        assert result.observed_at == t2

        assert result.previous_event_id == first_event.id
        assert result.previous_observed_at == t1

        assert result.entity_count_delta == 0
        assert result.active_edge_count_delta == 1

        assert result.created == 3
        assert result.existing == 0
        assert len(result.snapshot_ids) == 3

        rows = (
            db.query(TopologyShiftSnapshot)
            .filter(
                TopologyShiftSnapshot.organization_id
                == organization.id,
                TopologyShiftSnapshot.semantic_drift_event_id
                == second_event.id,
            )
            .all()
        )

        assert len(rows) == 3

        by_entity = {
            row.entity_id: row
            for row in rows
        }

        assert set(by_entity) == {
            first.id,
            second.id,
            third.id,
        }

        assert all(
            row.previous_event_id == first_event.id
            for row in rows
        )

        assert all(
            row.previous_observed_at == t1
            for row in rows
        )

        assert all(
            row.observed_at == t2
            for row in rows
        )

        # A gains indirect downstream reach because B -> C appears.
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

        assert (
            by_entity[third.id].in_degree_delta
            == 1
        )

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_same_event_retry_is_idempotent():
    db = SessionLocal()
    organization_id = None

    try:
        detected_at = datetime(
            2026, 8, 30, 10, 0,
            tzinfo=timezone.utc,
        )

        (
            organization,
            first,
            second,
            _third,
            page,
        ) = _build_fixture(
            db,
            suffix=uuid.uuid4().hex[:8],
        )

        organization_id = organization.id

        event = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=detected_at,
            suffix="retry",
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

        first_result = (
            persist_topology_shift_snapshots_for_event(
                db=db,
                event=event,
            )
        )
        db.commit()

        second_result = (
            persist_topology_shift_snapshots_for_event(
                db=db,
                event=event,
            )
        )
        db.commit()

        assert first_result.created == 3
        assert first_result.existing == 0

        assert second_result.created == 0
        assert second_result.existing == 3

        count = (
            db.query(TopologyShiftSnapshot)
            .filter(
                TopologyShiftSnapshot.organization_id
                == organization.id,
                TopologyShiftSnapshot.semantic_drift_event_id
                == event.id,
            )
            .count()
        )

        assert count == 3

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_first_state_persists_empty_baseline_delta():
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
        ) = _build_fixture(
            db,
            suffix=uuid.uuid4().hex[:8],
        )

        organization_id = organization.id

        event = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=detected_at,
            suffix="baseline",
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

        result = (
            persist_topology_shift_snapshots_for_event(
                db=db,
                event=event,
            )
        )
        db.commit()

        assert result.previous_event_id is None
        assert result.previous_observed_at is None

        assert result.entity_count_delta == 3
        assert result.active_edge_count_delta == 1

        rows = (
            db.query(TopologyShiftSnapshot)
            .filter(
                TopologyShiftSnapshot.semantic_drift_event_id
                == event.id
            )
            .all()
        )

        assert len(rows) == 3

        assert all(
            row.previous_event_id is None
            for row in rows
        )

        assert all(
            row.previous_observed_at is None
            for row in rows
        )

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_backfilled_event_ignores_future_topology_state():
    db = SessionLocal()
    organization_id = None

    try:
        t1 = datetime(
            2026, 8, 30, 12, 0,
            tzinfo=timezone.utc,
        )
        t2 = datetime(
            2026, 8, 30, 13, 0,
            tzinfo=timezone.utc,
        )
        t3 = datetime(
            2026, 8, 30, 14, 0,
            tzinfo=timezone.utc,
        )

        (
            organization,
            first,
            second,
            third,
            page,
        ) = _build_fixture(
            db,
            suffix=uuid.uuid4().hex[:8],
        )

        organization_id = organization.id

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

        # T3 is physically processed before T2.
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

        result = (
            persist_topology_shift_snapshots_for_event(
                db=db,
                event=event_t2,
            )
        )
        db.commit()

        assert result.previous_event_id == event_t1.id
        assert result.previous_observed_at == t1
        assert result.active_edge_count_delta == 1

        rows = (
            db.query(TopologyShiftSnapshot)
            .filter(
                TopologyShiftSnapshot.semantic_drift_event_id
                == event_t2.id
            )
            .all()
        )

        by_entity = {
            row.entity_id: row
            for row in rows
        }

        assert len(rows) == 3

        # T3 contains C -> A. If future state leaked backwards,
        # C would have an outgoing topology delta here.
        assert (
            by_entity[third.id].out_degree_delta
            == 0
        )

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_event_deletion_preserves_topology_history():
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

        (
            organization,
            first,
            second,
            third,
            page,
        ) = _build_fixture(
            db,
            suffix=uuid.uuid4().hex[:8],
        )

        organization_id = organization.id

        first_event = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=t1,
            suffix="delete-t1",
        )

        _make_relationship(
            db,
            organization_id=organization.id,
            source_entity_id=first.id,
            target_entity_id=second.id,
            observed_at=t1,
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
            detected_at=t2,
            suffix="delete-t2",
        )

        _make_relationship(
            db,
            organization_id=organization.id,
            source_entity_id=second.id,
            target_entity_id=third.id,
            observed_at=t2,
            semantic_drift_event_id=second_event.id,
        )

        db.commit()

        persist_graph_metric_snapshots_for_event(
            db=db,
            event=second_event,
        )
        db.commit()

        persist_topology_shift_snapshots_for_event(
            db=db,
            event=second_event,
        )
        db.commit()

        first_event_id = first_event.id
        second_event_id = second_event.id

        # Delete previous event first. previous_event_id must become NULL,
        # while previous_observed_at remains useful historical metadata.
        db.execute(
            delete(SemanticDriftEvent).where(
                SemanticDriftEvent.id
                == first_event_id
            )
        )
        db.commit()

        rows = (
            db.query(TopologyShiftSnapshot)
            .filter(
                TopologyShiftSnapshot.organization_id
                == organization.id,
                TopologyShiftSnapshot.semantic_drift_event_id
                == second_event_id,
            )
            .all()
        )

        assert len(rows) == 3

        assert all(
            row.previous_event_id is None
            for row in rows
        )

        assert all(
            row.previous_observed_at == t1
            for row in rows
        )

        # Delete current event. The topology history itself must survive.
        db.execute(
            delete(SemanticDriftEvent).where(
                SemanticDriftEvent.id
                == second_event_id
            )
        )
        db.commit()

        surviving_rows = (
            db.query(TopologyShiftSnapshot)
            .filter(
                TopologyShiftSnapshot.organization_id
                == organization.id
            )
            .all()
        )

        assert len(surviving_rows) == 3

        assert all(
            row.semantic_drift_event_id is None
            for row in surviving_rows
        )

        assert all(
            row.observed_at == t2
            for row in surviving_rows
        )

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()

def test_concurrent_same_event_persistence_is_idempotent():
    setup_db = SessionLocal()
    organization_id = None

    try:
        detected_at = datetime(
            2026, 8, 30, 17, 0,
            tzinfo=timezone.utc,
        )

        (
            organization,
            first,
            second,
            _third,
            page,
        ) = _build_fixture(
            setup_db,
            suffix=f"concurrent-{uuid.uuid4().hex[:8]}",
        )

        organization_id = organization.id
        event_organization_id = organization.id

        event = _make_event(
            setup_db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=detected_at,
            suffix="concurrent",
        )

        _make_relationship(
            setup_db,
            organization_id=organization.id,
            source_entity_id=first.id,
            target_entity_id=second.id,
            observed_at=detected_at,
            semantic_drift_event_id=event.id,
        )

        setup_db.commit()

        event_id = event.id

        # The absolute graph state is already durable before workers race
        # to derive and persist topology deltas.
        persist_graph_metric_snapshots_for_event(
            db=setup_db,
            event=event,
        )
        setup_db.commit()

        worker_barrier = Barrier(2)

        def _worker():
            worker_db = SessionLocal()

            try:
                worker_event = (
                    worker_db.query(SemanticDriftEvent)
                    .filter(
                        SemanticDriftEvent.id
                        == event_id,
                        SemanticDriftEvent.organization_id
                        == event_organization_id,
                    )
                    .one()
                )

                # Both sessions must reach the persistence call together.
                worker_barrier.wait(timeout=10)

                result = (
                    persist_topology_shift_snapshots_for_event(
                        db=worker_db,
                        event=worker_event,
                    )
                )

                worker_db.commit()

                return (
                    result.created,
                    result.existing,
                    tuple(result.snapshot_ids),
                )

            except Exception:
                worker_db.rollback()
                raise

            finally:
                worker_db.close()

        with ThreadPoolExecutor(
            max_workers=2,
        ) as executor:
            futures = [
                executor.submit(_worker),
                executor.submit(_worker),
            ]

            results = [
                future.result(timeout=20)
                for future in futures
            ]

        created_existing = sorted(
            (created, existing)
            for created, existing, _ids
            in results
        )

        # Exactly one transaction creates the event/entity rows.
        # The competing transaction must observe the unique conflicts
        # and reuse the durable rows.
        assert created_existing == [
            (0, 3),
            (3, 0),
        ]

        verification_db = SessionLocal()

        try:
            rows = (
                verification_db
                .query(TopologyShiftSnapshot)
                .filter(
                    TopologyShiftSnapshot.organization_id
                    == event_organization_id,
                    TopologyShiftSnapshot.semantic_drift_event_id
                    == event_id,
                )
                .all()
            )

            assert len(rows) == 3

            assert len(
                {
                    row.entity_id
                    for row in rows
                }
            ) == 3

            durable_ids = {
                str(row.id)
                for row in rows
            }

            # Both callers must ultimately reference the same three
            # durable database rows.
            for (
                _created,
                _existing,
                snapshot_ids,
            ) in results:
                assert set(snapshot_ids) == durable_ids

        finally:
            verification_db.close()

    finally:
        try:
            _cleanup(
                setup_db,
                organization_id,
            )
        finally:
            setup_db.close()


def test_late_backfill_repairs_persisted_direct_successor_topology():
    db = SessionLocal()
    organization_id = None

    try:
        t1 = datetime(
            2026, 8, 30, 18, 0,
            tzinfo=timezone.utc,
        )
        t2 = datetime(
            2026, 8, 30, 19, 0,
            tzinfo=timezone.utc,
        )
        t3 = datetime(
            2026, 8, 30, 20, 0,
            tzinfo=timezone.utc,
        )

        (
            organization,
            first,
            second,
            third,
            page,
        ) = _build_fixture(
            db,
            suffix=f"late-repair-{uuid.uuid4().hex[:8]}",
        )

        organization_id = organization.id

        event_t1 = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=t1,
            suffix="late-repair-t1",
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
        persist_topology_shift_snapshots_for_event(
            db=db,
            event=event_t1,
        )
        db.commit()

        # T3 becomes durable before T2 exists.
        event_t3 = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=t3,
            suffix="late-repair-t3",
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

        initial_t3 = persist_topology_shift_snapshots_for_event(
            db=db,
            event=event_t3,
        )
        db.commit()

        assert initial_t3.previous_event_id == event_t1.id
        assert initial_t3.previous_observed_at == t1

        # T2 arrives after T3 topology has already been materialized.
        event_t2 = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=t2,
            suffix="late-repair-t2",
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

        result_t2 = persist_topology_shift_snapshots_for_event(
            db=db,
            event=event_t2,
        )
        db.commit()

        assert result_t2.previous_event_id == event_t1.id

        repaired_t3_rows = (
            db.query(TopologyShiftSnapshot)
            .filter(
                TopologyShiftSnapshot.organization_id
                == organization.id,
                TopologyShiftSnapshot.semantic_drift_event_id
                == event_t3.id,
            )
            .all()
        )

        assert len(repaired_t3_rows) == 3

        assert all(
            row.previous_event_id == event_t2.id
            for row in repaired_t3_rows
        )

        assert all(
            row.previous_observed_at == t2
            for row in repaired_t3_rows
        )

        by_entity = {
            row.entity_id: row
            for row in repaired_t3_rows
        }

        # T3 adds C -> A relative to the now-correct T2 state.
        assert by_entity[third.id].out_degree_delta == 1
        assert by_entity[first.id].in_degree_delta == 1

        repaired_ids = {
            row.id
            for row in repaired_t3_rows
        }

        retry_t2 = persist_topology_shift_snapshots_for_event(
            db=db,
            event=event_t2,
        )
        db.commit()

        assert retry_t2.created == 0
        assert retry_t2.existing == 3

        retry_t3_rows = (
            db.query(TopologyShiftSnapshot)
            .filter(
                TopologyShiftSnapshot.organization_id
                == organization.id,
                TopologyShiftSnapshot.semantic_drift_event_id
                == event_t3.id,
            )
            .all()
        )

        assert len(retry_t3_rows) == 3

        assert {
            row.id
            for row in retry_t3_rows
        } == repaired_ids

        assert all(
            row.previous_event_id == event_t2.id
            for row in retry_t3_rows
        )

        assert all(
            row.previous_observed_at == t2
            for row in retry_t3_rows
        )

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_late_backfill_does_not_rewrite_assessed_successor_topology():
    db = SessionLocal()
    organization_id = None

    try:
        t1 = datetime(
            2026, 8, 30, 21, 0,
            tzinfo=timezone.utc,
        )
        t2 = datetime(
            2026, 8, 30, 22, 0,
            tzinfo=timezone.utc,
        )
        t3 = datetime(
            2026, 8, 30, 23, 0,
            tzinfo=timezone.utc,
        )

        (
            organization,
            first,
            second,
            third,
            page,
        ) = _build_fixture(
            db,
            suffix=f"late-assessed-{uuid.uuid4().hex[:8]}",
        )

        organization_id = organization.id

        event_t1 = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=t1,
            suffix="late-assessed-t1",
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
        persist_topology_shift_snapshots_for_event(
            db=db,
            event=event_t1,
        )
        db.commit()

        event_t3 = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=t3,
            suffix="late-assessed-t3",
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
        persist_topology_shift_snapshots_for_event(
            db=db,
            event=event_t3,
        )

        persist_risk_assessment_v2(
            db,
            event=event_t3,
            semantic_magnitude=0.5,
            legacy_impact=0.5,
            semantic_severity=0.5,
            page_context=0.5,
            confidence=0.5,
        )
        db.commit()

        original_t3_rows = (
            db.query(TopologyShiftSnapshot)
            .filter(
                TopologyShiftSnapshot.organization_id
                == organization.id,
                TopologyShiftSnapshot.semantic_drift_event_id
                == event_t3.id,
            )
            .all()
        )

        original_t3_ids = {
            row.id
            for row in original_t3_rows
        }

        assert all(
            row.previous_event_id == event_t1.id
            for row in original_t3_rows
        )

        assert (
            db.query(RiskAssessment)
            .filter(
                RiskAssessment.organization_id
                == organization.id,
                RiskAssessment.semantic_drift_event_id
                == event_t3.id,
            )
            .count()
            == 1
        )

        event_t2 = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=t2,
            suffix="late-assessed-t2",
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

        try:
            persist_topology_shift_snapshots_for_event(
                db=db,
                event=event_t2,
            )
        except RuntimeError as exc:
            assert (
                str(exc)
                == "Cannot repair stale topology for a successor event "
                "with persisted risk assessment evidence."
            )
        else:
            raise AssertionError(
                "late topology repair should fail closed when "
                "the successor has persisted risk evidence"
            )

        db.rollback()

        surviving_t3_rows = (
            db.query(TopologyShiftSnapshot)
            .filter(
                TopologyShiftSnapshot.organization_id
                == organization.id,
                TopologyShiftSnapshot.semantic_drift_event_id
                == event_t3.id,
            )
            .all()
        )

        assert len(surviving_t3_rows) == 3

        assert {
            row.id
            for row in surviving_t3_rows
        } == original_t3_ids

        assert all(
            row.previous_event_id == event_t1.id
            for row in surviving_t3_rows
        )

        assert all(
            row.previous_observed_at == t1
            for row in surviving_t3_rows
        )

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()

def test_late_backfill_repairs_all_same_timestamp_successors():
    db = SessionLocal()
    organization_id = None
    try:
        t1 = datetime(2026, 8, 31, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 8, 31, 1, 0, tzinfo=timezone.utc)
        t3 = datetime(2026, 8, 31, 2, 0, tzinfo=timezone.utc)
        organization, first, second, third, page = _build_fixture(
            db, suffix=f"late-peer-{uuid.uuid4().hex[:8]}"
        )
        organization_id = organization.id

        event_t1 = _make_event(
            db, organization_id=organization.id,
            monitored_page_id=page.id, detected_at=t1, suffix="late-peer-t1",
        )
        _make_relationship(
            db, organization_id=organization.id,
            source_entity_id=first.id, target_entity_id=second.id,
            observed_at=t1, semantic_drift_event_id=event_t1.id,
        )
        db.commit()
        persist_graph_metric_snapshots_for_event(db=db, event=event_t1)
        persist_topology_shift_snapshots_for_event(db=db, event=event_t1)
        db.commit()

        event_t3a = _make_event(
            db, organization_id=organization.id,
            monitored_page_id=page.id, detected_at=t3, suffix="late-peer-t3a",
        )
        _make_relationship(
            db, organization_id=organization.id,
            source_entity_id=third.id, target_entity_id=first.id,
            observed_at=t3, semantic_drift_event_id=event_t3a.id,
        )
        event_t3b = _make_event(
            db, organization_id=organization.id,
            monitored_page_id=page.id, detected_at=t3, suffix="late-peer-t3b",
        )
        _make_relationship(
            db, organization_id=organization.id,
            source_entity_id=second.id, target_entity_id=third.id,
            observed_at=t3, semantic_drift_event_id=event_t3b.id,
        )
        db.commit()

        for event in (event_t3a, event_t3b):
            persist_graph_metric_snapshots_for_event(db=db, event=event)
            persist_topology_shift_snapshots_for_event(db=db, event=event)
            db.commit()

        for event in (event_t3a, event_t3b):
            rows = db.query(TopologyShiftSnapshot).filter(
                TopologyShiftSnapshot.organization_id == organization.id,
                TopologyShiftSnapshot.semantic_drift_event_id == event.id,
            ).all()
            assert len(rows) == 3
            assert all(row.previous_event_id == event_t1.id for row in rows)

        event_t2 = _make_event(
            db, organization_id=organization.id,
            monitored_page_id=page.id, detected_at=t2, suffix="late-peer-t2",
        )
        _make_relationship(
            db, organization_id=organization.id,
            source_entity_id=first.id, target_entity_id=third.id,
            observed_at=t2, semantic_drift_event_id=event_t2.id,
        )
        db.commit()
        persist_graph_metric_snapshots_for_event(db=db, event=event_t2)
        persist_topology_shift_snapshots_for_event(db=db, event=event_t2)
        db.commit()

        for event in (event_t3a, event_t3b):
            rows = db.query(TopologyShiftSnapshot).filter(
                TopologyShiftSnapshot.organization_id == organization.id,
                TopologyShiftSnapshot.semantic_drift_event_id == event.id,
            ).all()
            assert len(rows) == 3
            assert all(row.previous_event_id == event_t2.id for row in rows)
            assert all(row.previous_observed_at == t2 for row in rows)
    finally:
        try:
            _cleanup(db, organization_id)
        finally:
            db.close()


def test_risk_persistence_waits_for_successor_topology_repair():
    setup_db = SessionLocal()
    organization_id = None
    try:
        t1 = datetime(2026, 8, 31, 3, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 8, 31, 4, 0, tzinfo=timezone.utc)
        t3 = datetime(2026, 8, 31, 5, 0, tzinfo=timezone.utc)
        organization, first, second, third, page = _build_fixture(
            setup_db, suffix=f"late-race-{uuid.uuid4().hex[:8]}"
        )
        organization_id = organization.id

        event_t1 = _make_event(
            setup_db, organization_id=organization.id,
            monitored_page_id=page.id, detected_at=t1, suffix="late-race-t1",
        )
        _make_relationship(
            setup_db, organization_id=organization.id,
            source_entity_id=first.id, target_entity_id=second.id,
            observed_at=t1, semantic_drift_event_id=event_t1.id,
        )
        setup_db.commit()
        persist_graph_metric_snapshots_for_event(db=setup_db, event=event_t1)
        persist_topology_shift_snapshots_for_event(db=setup_db, event=event_t1)
        setup_db.commit()

        event_t3 = _make_event(
            setup_db, organization_id=organization.id,
            monitored_page_id=page.id, detected_at=t3, suffix="late-race-t3",
        )
        _make_relationship(
            setup_db, organization_id=organization.id,
            source_entity_id=third.id, target_entity_id=first.id,
            observed_at=t3, semantic_drift_event_id=event_t3.id,
        )
        setup_db.commit()
        persist_graph_metric_snapshots_for_event(db=setup_db, event=event_t3)
        persist_topology_shift_snapshots_for_event(db=setup_db, event=event_t3)
        setup_db.commit()

        event_t2 = _make_event(
            setup_db, organization_id=organization.id,
            monitored_page_id=page.id, detected_at=t2, suffix="late-race-t2",
        )
        _make_relationship(
            setup_db, organization_id=organization.id,
            source_entity_id=second.id, target_entity_id=third.id,
            observed_at=t2, semantic_drift_event_id=event_t2.id,
        )
        setup_db.commit()
        persist_graph_metric_snapshots_for_event(db=setup_db, event=event_t2)
        setup_db.commit()

        organization_uuid = organization.id
        t2_id = event_t2.id
        t3_id = event_t3.id
        repair_db = SessionLocal()
        worker_started = Event()
        try:
            acquire_event_evidence_lock(
                repair_db, organization_id=organization_uuid, event_id=t3_id
            )

            def _persist_risk():
                worker_db = SessionLocal()
                try:
                    worker_event = worker_db.query(SemanticDriftEvent).filter(
                        SemanticDriftEvent.organization_id == organization_uuid,
                        SemanticDriftEvent.id == t3_id,
                    ).one()
                    worker_started.set()
                    result = persist_risk_assessment_v2(
                        worker_db, event=worker_event,
                        semantic_magnitude=0.5, legacy_impact=0.5,
                        semantic_severity=0.5, page_context=0.5, confidence=0.5,
                    )
                    worker_db.commit()
                    return result
                except Exception:
                    worker_db.rollback()
                    raise
                finally:
                    worker_db.close()

            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_persist_risk)
                assert worker_started.wait(timeout=5)
                try:
                    future.result(timeout=0.5)
                except TimeoutError:
                    pass
                else:
                    raise AssertionError(
                        "risk persistence crossed the event evidence lock"
                    )

                repair_event = repair_db.query(SemanticDriftEvent).filter(
                    SemanticDriftEvent.organization_id == organization_uuid,
                    SemanticDriftEvent.id == t2_id,
                ).one()
                persist_topology_shift_snapshots_for_event(
                    db=repair_db, event=repair_event
                )
                repair_db.commit()
                risk_result = future.result(timeout=10)

            assert risk_result.created is True
            assert risk_result.has_previous_topology_state is True
        finally:
            repair_db.rollback()
            repair_db.close()

        verification_db = SessionLocal()
        try:
            repaired_rows = verification_db.query(TopologyShiftSnapshot).filter(
                TopologyShiftSnapshot.organization_id == organization_uuid,
                TopologyShiftSnapshot.semantic_drift_event_id == t3_id,
            ).all()
            assert len(repaired_rows) == 3
            assert all(row.previous_event_id == t2_id for row in repaired_rows)
            assessment = verification_db.query(RiskAssessment).filter(
                RiskAssessment.organization_id == organization_uuid,
                RiskAssessment.semantic_drift_event_id == t3_id,
            ).one()
            assert assessment.has_previous_topology_state is True
        finally:
            verification_db.close()
    finally:
        try:
            _cleanup(setup_db, organization_id)
        finally:
            setup_db.close()
