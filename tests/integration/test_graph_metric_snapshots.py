from __future__ import annotations

import uuid
from datetime import (
    datetime,
    timezone,
)

from sqlalchemy import delete
from sqlalchemy.orm import Session

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
from src.models.semantic_drift_event import (
    SemanticDriftEvent,
)
from src.services.graph_metric_snapshot_service import (
    persist_graph_metric_snapshots_for_event,
)


def _make_organization(
    db: Session,
    *,
    suffix: str,
) -> Organization:
    organization = Organization(
        id=uuid.uuid4(),
        name=f"graph-snapshot-{suffix}",
        slug=(
            f"graph-snapshot-{suffix}-"
            f"{uuid.uuid4().hex[:8]}"
        ),
        billing_email=(
            f"graph-snapshot-{suffix}-"
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
        name=f"Graph Snapshot {suffix}",
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
    raw_content = f"snapshot {suffix}"

    snapshot = PageSnapshot(
        id=uuid.uuid4(),
        organization_id=organization_id,
        monitored_page_id=monitored_page_id,
        content_hash=(
            f"{suffix}-"
            f"{uuid.uuid4().hex}"
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
    old_snapshot_id: uuid.UUID,
    new_snapshot_id: uuid.UUID,
    detected_at: datetime,
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


def _build_event_fixture(
    db: Session,
    *,
    suffix: str,
    detected_at: datetime,
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

    old_snapshot = _make_page_snapshot(
        db,
        organization_id=organization.id,
        monitored_page_id=page.id,
        suffix=f"{suffix}-old",
    )

    new_snapshot = _make_page_snapshot(
        db,
        organization_id=organization.id,
        monitored_page_id=page.id,
        suffix=f"{suffix}-new",
    )

    event = _make_event(
        db,
        organization_id=organization.id,
        monitored_page_id=page.id,
        old_snapshot_id=old_snapshot.id,
        new_snapshot_id=new_snapshot.id,
        detected_at=detected_at,
    )

    return (
        organization,
        first,
        second,
        third,
        event,
    )


def test_persist_snapshots_for_entire_active_graph():
    db = SessionLocal()
    organization_id = None

    try:
        detected_at = datetime(
            2026,
            8,
            29,
            10,
            30,
            tzinfo=timezone.utc,
        )

        (
            organization,
            first,
            second,
            third,
            event,
        ) = _build_event_fixture(
            db,
            suffix=uuid.uuid4().hex[:8],
            detected_at=detected_at,
        )

        organization_id = organization.id

        _make_relationship(
            db,
            organization_id=organization.id,
            source_entity_id=first.id,
            target_entity_id=second.id,
            observed_at=detected_at,
            semantic_drift_event_id=event.id,
        )

        _make_relationship(
            db,
            organization_id=organization.id,
            source_entity_id=second.id,
            target_entity_id=third.id,
            observed_at=detected_at,
            semantic_drift_event_id=event.id,
        )

        db.commit()

        result = (
            persist_graph_metric_snapshots_for_event(
                db=db,
                event=event,
            )
        )

        db.commit()

        assert result.entity_count == 3
        assert result.active_edge_count == 2
        assert result.created == 3
        assert result.existing == 0
        assert len(result.snapshot_ids) == 3

        snapshots = (
            db.query(
                EntityGraphMetricSnapshot
            )
            .filter(
                EntityGraphMetricSnapshot.organization_id
                == organization.id,
                EntityGraphMetricSnapshot.semantic_drift_event_id
                == event.id,
            )
            .all()
        )

        assert len(snapshots) == 3

        by_entity = {
            snapshot.entity_id: snapshot
            for snapshot in snapshots
        }

        assert set(by_entity) == {
            first.id,
            second.id,
            third.id,
        }

        assert (
            by_entity[first.id].observed_at
            == detected_at
        )

        assert (
            by_entity[first.id].downstream_reach
            == 2
        )

        assert (
            by_entity[second.id].downstream_reach
            == 1
        )

        assert (
            by_entity[third.id].downstream_reach
            == 0
        )

    finally:
        if organization_id is not None:
            try:
                db.rollback()

                db.execute(
                    delete(Organization).where(
                        Organization.id
                        == organization_id
                    )
                )

                db.commit()

            except Exception:
                db.rollback()
                raise

        db.close()


def test_same_event_retry_is_idempotent():
    db = SessionLocal()
    organization_id = None

    try:
        detected_at = datetime(
            2026,
            8,
            29,
            11,
            0,
            tzinfo=timezone.utc,
        )

        (
            organization,
            first,
            second,
            _third,
            event,
        ) = _build_event_fixture(
            db,
            suffix=uuid.uuid4().hex[:8],
            detected_at=detected_at,
        )

        organization_id = organization.id

        _make_relationship(
            db,
            organization_id=organization.id,
            source_entity_id=first.id,
            target_entity_id=second.id,
            observed_at=detected_at,
            semantic_drift_event_id=event.id,
        )

        db.commit()

        first_result = (
            persist_graph_metric_snapshots_for_event(
                db=db,
                event=event,
            )
        )

        db.commit()

        second_result = (
            persist_graph_metric_snapshots_for_event(
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
            db.query(
                EntityGraphMetricSnapshot
            )
            .filter(
                EntityGraphMetricSnapshot.organization_id
                == organization.id,
                EntityGraphMetricSnapshot.semantic_drift_event_id
                == event.id,
            )
            .count()
        )

        assert count == 3

    finally:
        if organization_id is not None:
            try:
                db.rollback()

                db.execute(
                    delete(Organization).where(
                        Organization.id
                        == organization_id
                    )
                )

                db.commit()

            except Exception:
                db.rollback()
                raise

        db.close()


def test_snapshots_are_tenant_isolated():
    db = SessionLocal()

    first_organization_id = None
    second_organization_id = None

    try:
        detected_at = datetime(
            2026,
            8,
            29,
            11,
            15,
            tzinfo=timezone.utc,
        )

        (
            first_organization,
            first_a,
            first_b,
            _first_c,
            first_event,
        ) = _build_event_fixture(
            db,
            suffix=f"a-{uuid.uuid4().hex[:8]}",
            detected_at=detected_at,
        )

        (
            second_organization,
            second_a,
            second_b,
            _second_c,
            second_event,
        ) = _build_event_fixture(
            db,
            suffix=f"b-{uuid.uuid4().hex[:8]}",
            detected_at=detected_at,
        )

        first_organization_id = (
            first_organization.id
        )
        second_organization_id = (
            second_organization.id
        )

        _make_relationship(
            db,
            organization_id=first_organization.id,
            source_entity_id=first_a.id,
            target_entity_id=first_b.id,
            observed_at=detected_at,
            semantic_drift_event_id=first_event.id,
        )

        _make_relationship(
            db,
            organization_id=second_organization.id,
            source_entity_id=second_a.id,
            target_entity_id=second_b.id,
            observed_at=detected_at,
            semantic_drift_event_id=second_event.id,
        )

        db.commit()

        result = (
            persist_graph_metric_snapshots_for_event(
                db=db,
                event=first_event,
            )
        )

        db.commit()

        assert result.entity_count == 3
        assert result.created == 3

        snapshots = (
            db.query(
                EntityGraphMetricSnapshot
            )
            .filter(
                EntityGraphMetricSnapshot.semantic_drift_event_id
                == first_event.id
            )
            .all()
        )

        assert len(snapshots) == 3

        assert {
            snapshot.organization_id
            for snapshot in snapshots
        } == {
            first_organization.id
        }

        assert {
            snapshot.entity_id
            for snapshot in snapshots
        }.isdisjoint(
            {
                second_a.id,
                second_b.id,
            }
        )

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


def test_deleting_event_preserves_metric_snapshots():
    db = SessionLocal()
    organization_id = None

    try:
        detected_at = datetime(
            2026,
            8,
            29,
            11,
            30,
            tzinfo=timezone.utc,
        )

        (
            organization,
            first,
            second,
            _third,
            event,
        ) = _build_event_fixture(
            db,
            suffix=uuid.uuid4().hex[:8],
            detected_at=detected_at,
        )

        organization_id = organization.id

        _make_relationship(
            db,
            organization_id=organization.id,
            source_entity_id=first.id,
            target_entity_id=second.id,
            observed_at=detected_at,
            semantic_drift_event_id=event.id,
        )

        db.commit()

        event_id = event.id

        result = (
            persist_graph_metric_snapshots_for_event(
                db=db,
                event=event,
            )
        )

        db.commit()

        assert result.created == 3

        db.execute(
            delete(SemanticDriftEvent).where(
                SemanticDriftEvent.id
                == event_id
            )
        )

        db.commit()

        snapshots = (
            db.query(
                EntityGraphMetricSnapshot
            )
            .filter(
                EntityGraphMetricSnapshot.organization_id
                == organization.id
            )
            .all()
        )

        assert len(snapshots) == 3

        assert all(
            snapshot.semantic_drift_event_id
            is None
            for snapshot in snapshots
        )

        assert all(
            snapshot.observed_at
            == detected_at
            for snapshot in snapshots
        )

    finally:
        if organization_id is not None:
            try:
                db.rollback()

                db.execute(
                    delete(Organization).where(
                        Organization.id
                        == organization_id
                    )
                )

                db.commit()

            except Exception:
                db.rollback()
                raise

        db.close()

def test_future_relationship_evidence_does_not_leak_into_past_snapshot():
    db = SessionLocal()
    organization_id = None

    try:
        past_time = datetime(
            2026,
            8,
            29,
            10,
            0,
            tzinfo=timezone.utc,
        )

        future_time = datetime(
            2026,
            8,
            29,
            12,
            0,
            tzinfo=timezone.utc,
        )

        (
            organization,
            first,
            second,
            third,
            past_event,
        ) = _build_event_fixture(
            db,
            suffix=f"backfill-{uuid.uuid4().hex[:8]}",
            detected_at=past_time,
        )

        organization_id = organization.id

        # Evidence that genuinely existed at the historical cutoff.
        _make_relationship(
            db,
            organization_id=organization.id,
            source_entity_id=first.id,
            target_entity_id=second.id,
            observed_at=past_time,
            semantic_drift_event_id=past_event.id,
        )

        # We need a separate real event to own the future observation.
        future_page = _make_monitored_page(
            db,
            organization_id=organization.id,
            entity_id=second.id,
            suffix=f"future-{uuid.uuid4().hex[:8]}",
        )

        future_old_snapshot = _make_page_snapshot(
            db,
            organization_id=organization.id,
            monitored_page_id=future_page.id,
            suffix=f"future-old-{uuid.uuid4().hex[:8]}",
        )

        future_new_snapshot = _make_page_snapshot(
            db,
            organization_id=organization.id,
            monitored_page_id=future_page.id,
            suffix=f"future-new-{uuid.uuid4().hex[:8]}",
        )

        future_event = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=future_page.id,
            old_snapshot_id=future_old_snapshot.id,
            new_snapshot_id=future_new_snapshot.id,
            detected_at=future_time,
        )

        # This edge exists in the database before we execute the backfilled
        # snapshot, but its semantic observation time is in the future.
        _make_relationship(
            db,
            organization_id=organization.id,
            source_entity_id=second.id,
            target_entity_id=third.id,
            observed_at=future_time,
            semantic_drift_event_id=future_event.id,
        )

        db.commit()

        # Simulate processing/backfilling the earlier event after later
        # evidence has already been persisted.
        result = persist_graph_metric_snapshots_for_event(
            db=db,
            event=past_event,
        )

        db.commit()

        assert result.entity_count == 3

        # Critical temporal assertion:
        # A -> B existed at 10:00.
        # B -> C only exists at 12:00 and MUST NOT leak into 10:00.
        assert result.active_edge_count == 1
        assert result.created == 3
        assert result.existing == 0

        snapshots = (
            db.query(EntityGraphMetricSnapshot)
            .filter(
                EntityGraphMetricSnapshot.organization_id
                == organization.id,
                EntityGraphMetricSnapshot.semantic_drift_event_id
                == past_event.id,
            )
            .all()
        )

        assert len(snapshots) == 3

        by_entity = {
            snapshot.entity_id: snapshot
            for snapshot in snapshots
        }

        assert set(by_entity) == {
            first.id,
            second.id,
            third.id,
        }

        assert by_entity[first.id].out_degree == 1
        assert by_entity[first.id].in_degree == 0
        assert by_entity[first.id].downstream_reach == 1

        assert by_entity[second.id].in_degree == 1
        assert by_entity[second.id].out_degree == 0
        assert by_entity[second.id].downstream_reach == 0

        assert by_entity[third.id].in_degree == 0
        assert by_entity[third.id].out_degree == 0
        assert by_entity[third.id].downstream_reach == 0

        assert all(
            snapshot.active_edge_count == 1
            for snapshot in snapshots
        )

        assert all(
            snapshot.observed_at == past_time
            for snapshot in snapshots
        )

    finally:
        if organization_id is not None:
            try:
                db.rollback()

                db.execute(
                    delete(Organization).where(
                        Organization.id
                        == organization_id
                    )
                )

                db.commit()

            except Exception:
                db.rollback()
                raise

        db.close()