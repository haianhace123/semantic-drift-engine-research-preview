from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete

from src.core.database import SessionLocal
from src.models.entity import Entity
from src.models.entity_graph_metric_snapshot import (
    EntityGraphMetricSnapshot,
)
from src.models.entity_relationship import EntityRelationship
from src.models.entity_relationship_observation import (
    EntityRelationshipObservation,
)
from src.models.monitored_page import MonitoredPage
from src.models.organization import Organization
from src.models.page_snapshot import PageSnapshot
from src.models.semantic_drift_event import SemanticDriftEvent
from src.services.graph_metric_snapshot_service import (
    persist_graph_metric_snapshots_for_event,
)


def _build_fixture():
    db = SessionLocal()

    suffix = uuid.uuid4().hex[:8]

    observed_at = datetime(
        2026,
        8,
        29,
        12,
        0,
        tzinfo=timezone.utc,
    )

    organization = Organization(
        id=uuid.uuid4(),
        name=f"graph-snapshot-concurrency-{suffix}",
        slug=f"graph-snapshot-concurrency-{suffix}",
        billing_email=(
            f"graph-snapshot-concurrency-{suffix}"
            "@example.com"
        ),
    )

    db.add(organization)
    db.flush()

    first = Entity(
        id=uuid.uuid4(),
        organization_id=organization.id,
        name=f"Concurrency First {suffix}",
        domain=f"first-{suffix}.example.com",
        category="test",
    )

    second = Entity(
        id=uuid.uuid4(),
        organization_id=organization.id,
        name=f"Concurrency Second {suffix}",
        domain=f"second-{suffix}.example.com",
        category="test",
    )

    third = Entity(
        id=uuid.uuid4(),
        organization_id=organization.id,
        name=f"Concurrency Third {suffix}",
        domain=f"third-{suffix}.example.com",
        category="test",
    )

    db.add_all([
        first,
        second,
        third,
    ])
    db.flush()

    page = MonitoredPage(
        id=uuid.uuid4(),
        organization_id=organization.id,
        entity_id=first.id,
        url=f"https://first-{suffix}.example.com/docs",
        page_type="docs",
        crawl_frequency="hourly",
        is_active=True,
    )

    db.add(page)
    db.flush()

    old_snapshot = PageSnapshot(
        id=uuid.uuid4(),
        organization_id=organization.id,
        monitored_page_id=page.id,
        content_hash=uuid.uuid4().hex,
        raw_content="old",
        content_size=3,
    )

    new_snapshot = PageSnapshot(
        id=uuid.uuid4(),
        organization_id=organization.id,
        monitored_page_id=page.id,
        content_hash=uuid.uuid4().hex,
        raw_content="new",
        content_size=3,
    )

    db.add_all([
        old_snapshot,
        new_snapshot,
    ])
    db.flush()

    event = SemanticDriftEvent(
        id=uuid.uuid4(),
        organization_id=organization.id,
        monitored_page_id=page.id,
        old_snapshot_id=old_snapshot.id,
        new_snapshot_id=new_snapshot.id,
        semantic_delta_json={},
        impact_score=0,
        entities_affected=[],
        related_events=[],
        detected_at=observed_at,
    )

    db.add(event)
    db.flush()

    first_edge = EntityRelationship(
        id=uuid.uuid4(),
        organization_id=organization.id,
        source_entity_id=first.id,
        target_entity_id=second.id,
        relationship_type="mentioned",
        evidence_method="test",
        evidence_count=1,
        strength=1.0,
        confidence=1.0,
        first_seen_at=observed_at,
        last_seen_at=observed_at,
        first_event_id=event.id,
        last_event_id=event.id,
        is_active=True,
    )

    second_edge = EntityRelationship(
        id=uuid.uuid4(),
        organization_id=organization.id,
        source_entity_id=second.id,
        target_entity_id=third.id,
        relationship_type="mentioned",
        evidence_method="test",
        evidence_count=1,
        strength=1.0,
        confidence=1.0,
        first_seen_at=observed_at,
        last_seen_at=observed_at,
        first_event_id=event.id,
        last_event_id=event.id,
        is_active=True,
    )

    db.add_all([
        first_edge,
        second_edge,
    ])
    db.flush()

    first_observation = EntityRelationshipObservation(
        id=uuid.uuid4(),
        organization_id=organization.id,
        relationship_id=first_edge.id,
        semantic_drift_event_id=event.id,
        source_entity_id=first.id,
        target_entity_id=second.id,
        relationship_type="mentioned",
        evidence_method="test",
        observed_at=observed_at,
        created_at=observed_at,
    )

    second_observation = EntityRelationshipObservation(
        id=uuid.uuid4(),
        organization_id=organization.id,
        relationship_id=second_edge.id,
        semantic_drift_event_id=event.id,
        source_entity_id=second.id,
        target_entity_id=third.id,
        relationship_type="mentioned",
        evidence_method="test",
        observed_at=observed_at,
        created_at=observed_at,
    )

    db.add_all([
        first_observation,
        second_observation,
    ])

    db.commit()

    result = (
        organization.id,
        event.id,
        first.id,
        second.id,
        third.id,
    )

    db.close()

    return result


def _worker(
    *,
    event_id,
    barrier,
    results,
    errors,
):
    db = SessionLocal()

    try:
        event = (
            db.query(SemanticDriftEvent)
            .filter(
                SemanticDriftEvent.id
                == event_id
            )
            .one()
        )

        barrier.wait()

        result = (
            persist_graph_metric_snapshots_for_event(
                db=db,
                event=event,
            )
        )

        db.commit()

        results.append(result)

    except Exception as exc:
        db.rollback()
        errors.append(exc)

    finally:
        db.close()


def test_same_event_concurrent_snapshot_persistence():
    (
        organization_id,
        event_id,
        first_entity_id,
        second_entity_id,
        third_entity_id,
    ) = _build_fixture()

    try:
        barrier = threading.Barrier(2)

        results = []
        errors = []

        first_thread = threading.Thread(
            target=_worker,
            kwargs={
                "event_id": event_id,
                "barrier": barrier,
                "results": results,
                "errors": errors,
            },
        )

        second_thread = threading.Thread(
            target=_worker,
            kwargs={
                "event_id": event_id,
                "barrier": barrier,
                "results": results,
                "errors": errors,
            },
        )

        first_thread.start()
        second_thread.start()

        first_thread.join()
        second_thread.join()

        assert errors == []
        assert len(results) == 2

        db = SessionLocal()

        try:
            snapshots = (
                db.query(
                    EntityGraphMetricSnapshot
                )
                .filter(
                    EntityGraphMetricSnapshot.organization_id
                    == organization_id,
                    EntityGraphMetricSnapshot.semantic_drift_event_id
                    == event_id,
                )
                .all()
            )

            assert len(snapshots) == 3

            assert len({
                snapshot.entity_id
                for snapshot in snapshots
            }) == 3

            # Both workers must have calculated the real A -> B -> C
            # evidence graph, not an empty aggregate-only fixture.
            assert all(
                result.entity_count == 3
                for result in results
            )

            assert all(
                result.active_edge_count == 2
                for result in results
            )

            assert sum(
                result.created
                for result in results
            ) == 3

            assert sum(
                result.existing
                for result in results
            ) == 3

            by_entity = {
                snapshot.entity_id: snapshot
                for snapshot in snapshots
            }

            assert (
                by_entity[first_entity_id].downstream_reach
                == 2
            )

            assert (
                by_entity[second_entity_id].downstream_reach
                == 1
            )

            assert (
                by_entity[third_entity_id].downstream_reach
                == 0
            )

            assert all(
                snapshot.active_edge_count == 2
                for snapshot in snapshots
            )

        finally:
            db.close()

    finally:
        cleanup_db = SessionLocal()

        try:
            cleanup_db.execute(
                delete(Organization).where(
                    Organization.id
                    == organization_id
                )
            )

            cleanup_db.commit()

        except Exception:
            cleanup_db.rollback()
            raise

        finally:
            cleanup_db.close()