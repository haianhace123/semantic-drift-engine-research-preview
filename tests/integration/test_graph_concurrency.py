from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete
from sqlalchemy.orm import Session

from src.core.database import SessionLocal
from src.models.entity import Entity
from src.models.entity_relationship import EntityRelationship
from src.models.entity_relationship_observation import (
    EntityRelationshipObservation,
)
from src.models.monitored_page import MonitoredPage
from src.models.organization import Organization
from src.models.page_snapshot import PageSnapshot
from src.models.semantic_drift_event import SemanticDriftEvent
from src.services.graph_service import persist_event_relationships



def _make_organization(db: Session, suffix: str) -> Organization:
    organization = Organization(
        id=uuid.uuid4(),
        name=f"graph-concurrency-{suffix}",
        slug=f"graph-concurrency-{suffix}-{uuid.uuid4().hex[:8]}",
        billing_email=(
            f"graph-concurrency-{suffix}-"
            f"{uuid.uuid4().hex[:8]}@example.com"
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
        name=f"Graph Concurrency {suffix}",
        domain=f"{suffix}-{uuid.uuid4().hex[:8]}.example.com",
        category="test",
    )
    db.add(entity)
    db.flush()
    return entity


def _make_event(
    db: Session,
    *,
    organization_id: uuid.UUID,
    source_entity_id: uuid.UUID,
    target_entity_id: uuid.UUID,
) -> SemanticDriftEvent:
    page = MonitoredPage(
        id=uuid.uuid4(),
        organization_id=organization_id,
        entity_id=source_entity_id,
        url=f"https://{uuid.uuid4().hex}.example.com/docs",
        page_type="docs",
        crawl_frequency="daily",
        is_active=True,
        custom_rules={},
    )
    db.add(page)
    db.flush()

    old_snapshot = PageSnapshot(
        id=uuid.uuid4(),
        organization_id=organization_id,
        monitored_page_id=page.id,
        content_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        raw_content="old",
        content_size=3,
        embedding_status="pending",
        embedding_attempts=0,
        crawled_at=datetime.now(timezone.utc),
    )

    new_snapshot = PageSnapshot(
        id=uuid.uuid4(),
        organization_id=organization_id,
        monitored_page_id=page.id,
        content_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        raw_content="new",
        content_size=3,
        embedding_status="pending",
        embedding_attempts=0,
        crawled_at=datetime.now(timezone.utc),
    )

    db.add_all([old_snapshot, new_snapshot])
    db.flush()

    detected_at = datetime.now(timezone.utc)

    event = SemanticDriftEvent(
        id=uuid.uuid4(),
        monitored_page_id=page.id,
        organization_id=organization_id,
        old_snapshot_id=old_snapshot.id,
        new_snapshot_id=new_snapshot.id,
        semantic_delta_json={"concurrency_test": True},
        summary_text="graph concurrency test",
        analysis_status="completed",
        analysis_attempts=0,
        vector_analysis_status="pending",
        vector_analysis_attempts=0,
        risk_level="medium",
        confidence=1.0,
        impact_score=50,
        risk_scoring_status="pending",
        risk_scoring_attempts=0,
        changes_count=1,
        chunk_count=1,
        llm_tokens_used=0,
        related_events=[],
        entities_affected=[str(target_entity_id)],
        detected_at=detected_at,
        first_seen_at=detected_at,
        is_anomaly=False,
        is_pattern_part=False,
        is_reviewed=False,
        is_archived=False,
    )

    db.add(event)
    db.flush()

    return event


def test_same_event_concurrent_persistence_is_idempotent():
    """
    Two independent transactions process the exact same event/edge.

    Expected final state:
    - one aggregate relationship;
    - one immutable observation;
    - evidence_count == 1;
    - neither worker raises an exception.
    """
    setup_db = SessionLocal()

    organization_id = None
    source_id = None
    target_id = None
    event_id = None

    try:
        suffix = uuid.uuid4().hex[:8]

        organization = _make_organization(
            setup_db,
            suffix=suffix,
        )

        source = _make_entity(
            setup_db,
            organization_id=organization.id,
            suffix=f"{suffix}-source",
        )

        target = _make_entity(
            setup_db,
            organization_id=organization.id,
            suffix=f"{suffix}-target",
        )

        event = _make_event(
            setup_db,
            organization_id=organization.id,
            source_entity_id=source.id,
            target_entity_id=target.id,
        )

        organization_id = organization.id
        source_id = source.id
        target_id = target.id
        event_id = event.id

        setup_db.commit()

    finally:
        setup_db.close()

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []
    results = []
    lock = threading.Lock()

    def worker() -> None:
        db = SessionLocal()

        try:
            event = (
                db.query(SemanticDriftEvent)
                .filter(
                    SemanticDriftEvent.id == event_id,
                    SemanticDriftEvent.organization_id
                    == organization_id,
                )
                .one()
            )

            barrier.wait(timeout=10)

            result = persist_event_relationships(
                db=db,
                event=event,
                source_entity_id=source_id,
            )

            db.commit()

            with lock:
                results.append(result)

        except BaseException as exc:
            db.rollback()

            with lock:
                errors.append(exc)

        finally:
            db.close()

    first_thread = threading.Thread(
        target=worker,
        name="graph-concurrency-1",
    )
    second_thread = threading.Thread(
        target=worker,
        name="graph-concurrency-2",
    )

    first_thread.start()
    second_thread.start()

    first_thread.join(timeout=20)
    second_thread.join(timeout=20)

    assert not first_thread.is_alive(), (
        "First graph persistence thread did not finish."
    )
    assert not second_thread.is_alive(), (
        "Second graph persistence thread did not finish."
    )

    assert errors == []
    assert len(results) == 2

    verify_db = SessionLocal()

    try:
        relationships = (
            verify_db.query(EntityRelationship)
            .filter(
                EntityRelationship.organization_id
                == organization_id,
                EntityRelationship.source_entity_id
                == source_id,
                EntityRelationship.target_entity_id
                == target_id,
            )
            .all()
        )

        assert len(relationships) == 1

        relationship = relationships[0]

        assert relationship.evidence_count == 1
        assert relationship.first_event_id == event_id
        assert relationship.last_event_id == event_id

        observations = (
            verify_db.query(EntityRelationshipObservation)
            .filter(
                EntityRelationshipObservation.relationship_id
                == relationship.id
            )
            .all()
        )

        assert len(observations) == 1
        assert (
            observations[0].semantic_drift_event_id
            == event_id
        )

    finally:
        cleanup_db = verify_db

        try:
            # Use a Core DELETE so PostgreSQL owns the cascade behavior.
            #
            # ORM instance deletion may attempt to synchronize child
            # relationships by setting monitored_pages.organization_id
            # to NULL before the database ON DELETE CASCADE executes.
            cleanup_db.execute(
                delete(Organization).where(
                    Organization.id == organization_id
                )
            )
            cleanup_db.commit()

        except Exception:
            cleanup_db.rollback()
            raise

        finally:
            cleanup_db.close()
def test_different_events_concurrently_increment_same_edge():
    """
    Two independent transactions process two different events that
    contribute evidence to the exact same graph edge.

    Expected final state:
    - one aggregate relationship;
    - two immutable observations;
    - evidence_count == 2;
    - neither worker raises an exception.
    """
    setup_db = SessionLocal()

    organization_id = None
    source_id = None
    target_id = None
    first_event_id = None
    second_event_id = None

    try:
        suffix = uuid.uuid4().hex[:8]

        organization = _make_organization(
            setup_db,
            suffix=f"{suffix}-different-events",
        )

        source = _make_entity(
            setup_db,
            organization_id=organization.id,
            suffix=f"{suffix}-different-events-source",
        )

        target = _make_entity(
            setup_db,
            organization_id=organization.id,
            suffix=f"{suffix}-different-events-target",
        )

        first_event = _make_event(
            setup_db,
            organization_id=organization.id,
            source_entity_id=source.id,
            target_entity_id=target.id,
        )

        second_event = _make_event(
            setup_db,
            organization_id=organization.id,
            source_entity_id=source.id,
            target_entity_id=target.id,
        )

        organization_id = organization.id
        source_id = source.id
        target_id = target.id
        first_event_id = first_event.id
        second_event_id = second_event.id

        setup_db.commit()

    finally:
        setup_db.close()

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []
    results = []
    lock = threading.Lock()

    def worker(event_id: uuid.UUID) -> None:
        db = SessionLocal()

        try:
            event = (
                db.query(SemanticDriftEvent)
                .filter(
                    SemanticDriftEvent.id == event_id,
                    SemanticDriftEvent.organization_id
                    == organization_id,
                )
                .one()
            )

            barrier.wait(timeout=10)

            result = persist_event_relationships(
                db=db,
                event=event,
                source_entity_id=source_id,
            )

            db.commit()

            with lock:
                results.append(result)

        except BaseException as exc:
            db.rollback()

            with lock:
                errors.append(exc)

        finally:
            db.close()

    first_thread = threading.Thread(
        target=worker,
        args=(first_event_id,),
        name="graph-concurrency-different-event-1",
    )

    second_thread = threading.Thread(
        target=worker,
        args=(second_event_id,),
        name="graph-concurrency-different-event-2",
    )

    first_thread.start()
    second_thread.start()

    first_thread.join(timeout=20)
    second_thread.join(timeout=20)

    assert not first_thread.is_alive(), (
        "First different-event graph persistence thread did not finish."
    )

    assert not second_thread.is_alive(), (
        "Second different-event graph persistence thread did not finish."
    )

    assert errors == []
    assert len(results) == 2

    verify_db = SessionLocal()

    try:
        relationships = (
            verify_db.query(EntityRelationship)
            .filter(
                EntityRelationship.organization_id
                == organization_id,
                EntityRelationship.source_entity_id
                == source_id,
                EntityRelationship.target_entity_id
                == target_id,
            )
            .all()
        )

        assert len(relationships) == 1

        relationship = relationships[0]

        assert relationship.evidence_count == 2

        observations = (
            verify_db.query(EntityRelationshipObservation)
            .filter(
                EntityRelationshipObservation.relationship_id
                == relationship.id
            )
            .order_by(
                EntityRelationshipObservation.semantic_drift_event_id.asc()
            )
            .all()
        )

        assert len(observations) == 2

        observed_event_ids = {
            observation.semantic_drift_event_id
            for observation in observations
        }

        assert observed_event_ids == {
            first_event_id,
            second_event_id,
        }

        assert relationship.first_event_id in {
            first_event_id,
            second_event_id,
        }

        assert relationship.last_event_id in {
            first_event_id,
            second_event_id,
        }

    finally:
        cleanup_db = verify_db

        try:
            cleanup_db.execute(
                delete(Organization).where(
                    Organization.id == organization_id
                )
            )
            cleanup_db.commit()

        except Exception:
            cleanup_db.rollback()
            raise

        finally:
            cleanup_db.close()