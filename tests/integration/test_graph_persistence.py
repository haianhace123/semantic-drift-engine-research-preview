from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
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

TEST_PREFIX = "graph-persistence-test"


def _make_organization(
    db: Session,
    *,
    suffix: str,
) -> Organization:
    organization = Organization(
        id=uuid.uuid4(),
        name=f"{TEST_PREFIX}-{suffix}",
        slug=f"{TEST_PREFIX}-{suffix}-{uuid.uuid4().hex[:8]}",
        billing_email=(
            f"{TEST_PREFIX}-{suffix}-{uuid.uuid4().hex[:8]}"
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
        name=f"{TEST_PREFIX}-{suffix}",
        domain=(
            f"{TEST_PREFIX}-{suffix}-"
            f"{uuid.uuid4().hex[:8]}.example.com"
        ),
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
            f"https://{TEST_PREFIX}-{suffix}-"
            f"{uuid.uuid4().hex[:8]}.example.com/docs"
        ),
        page_type="docs",
        crawl_frequency="daily",
        is_active=True,
        custom_rules={},
    )

    db.add(page)
    db.flush()

    return page


def _make_snapshot(
    db: Session,
    *,
    organization_id: uuid.UUID,
    monitored_page_id: uuid.UUID,
    suffix: str,
    crawled_at: datetime,
) -> PageSnapshot:
    snapshot = PageSnapshot(
        id=uuid.uuid4(),
        organization_id=organization_id,
        monitored_page_id=monitored_page_id,
        content_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        raw_content=f"{TEST_PREFIX}-{suffix}",
        content_size=len(f"{TEST_PREFIX}-{suffix}"),
        embedding_status="pending",
        embedding_attempts=0,
        crawled_at=crawled_at,
    )

    db.add(snapshot)
    db.flush()

    return snapshot


def _make_event(
    db: Session,
    *,
    organization_id: uuid.UUID,
    source_entity_id: uuid.UUID,
    detected_at: datetime,
    entities_affected: list[str] | None = None,
    suffix: str | None = None,
) -> SemanticDriftEvent:
    event_suffix = suffix or uuid.uuid4().hex[:8]

    page = _make_monitored_page(
        db,
        organization_id=organization_id,
        entity_id=source_entity_id,
        suffix=event_suffix,
    )

    old_snapshot = _make_snapshot(
        db,
        organization_id=organization_id,
        monitored_page_id=page.id,
        suffix=f"{event_suffix}-old",
        crawled_at=detected_at - timedelta(minutes=1),
    )

    new_snapshot = _make_snapshot(
        db,
        organization_id=organization_id,
        monitored_page_id=page.id,
        suffix=f"{event_suffix}-new",
        crawled_at=detected_at,
    )

    event = SemanticDriftEvent(
        id=uuid.uuid4(),
        monitored_page_id=page.id,
        organization_id=organization_id,
        old_snapshot_id=old_snapshot.id,
        new_snapshot_id=new_snapshot.id,
        semantic_delta_json={
            "test": True,
            "suffix": event_suffix,
        },
        summary_text=f"{TEST_PREFIX}-{event_suffix}",
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
        entities_affected=entities_affected or [],
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


@pytest.fixture()
def db():
    session = SessionLocal()

    try:
        yield session
    finally:
        session.rollback()
        session.close()


def test_persist_event_relationship_creates_edge(
    db: Session,
):
    organization = _make_organization(
        db,
        suffix="create",
    )

    source = _make_entity(
        db,
        organization_id=organization.id,
        suffix="create-source",
    )

    target = _make_entity(
        db,
        organization_id=organization.id,
        suffix="create-target",
    )

    detected_at = datetime.now(timezone.utc)

    event = _make_event(
        db,
        organization_id=organization.id,
        source_entity_id=source.id,
        detected_at=detected_at,
        entities_affected=[
            str(target.id),
        ],
        suffix="create-event",
    )

    result = persist_event_relationships(
        db=db,
        event=event,
        source_entity_id=source.id,
    )

    assert result.created == 1
    assert result.updated == 0
    assert result.skipped == 0
    assert len(result.relationship_ids) == 1

    relationship = (
        db.query(EntityRelationship)
        .filter(
            EntityRelationship.id
            == uuid.UUID(result.relationship_ids[0])
        )
        .one()
    )

    assert relationship.organization_id == organization.id
    assert relationship.source_entity_id == source.id
    assert relationship.target_entity_id == target.id
    assert relationship.relationship_type == "mentioned"
    assert (
        relationship.evidence_method
        == "deterministic_name_domain_v1"
    )
    assert relationship.evidence_count == 1
    assert relationship.first_event_id == event.id
    assert relationship.last_event_id == event.id
    assert relationship.first_seen_at == detected_at
    assert relationship.last_seen_at == detected_at
    assert relationship.is_active is True


def test_repeated_evidence_updates_existing_edge(
    db: Session,
):
    organization = _make_organization(
        db,
        suffix="repeat",
    )

    source = _make_entity(
        db,
        organization_id=organization.id,
        suffix="repeat-source",
    )

    target = _make_entity(
        db,
        organization_id=organization.id,
        suffix="repeat-target",
    )

    first_time = datetime.now(timezone.utc)
    second_time = first_time + timedelta(minutes=5)

    first_event = _make_event(
        db,
        organization_id=organization.id,
        source_entity_id=source.id,
        detected_at=first_time,
        entities_affected=[
            str(target.id),
        ],
        suffix="repeat-first",
    )

    second_event = _make_event(
        db,
        organization_id=organization.id,
        source_entity_id=source.id,
        detected_at=second_time,
        entities_affected=[
            str(target.id),
        ],
        suffix="repeat-second",
    )

    first_result = persist_event_relationships(
        db=db,
        event=first_event,
        source_entity_id=source.id,
    )

    second_result = persist_event_relationships(
        db=db,
        event=second_event,
        source_entity_id=source.id,
    )

    assert first_result.created == 1
    assert first_result.updated == 0

    assert second_result.created == 0
    assert second_result.updated == 1

    relationships = (
        db.query(EntityRelationship)
        .filter(
            EntityRelationship.organization_id
            == organization.id,
            EntityRelationship.source_entity_id
            == source.id,
            EntityRelationship.target_entity_id
            == target.id,
        )
        .all()
    )

    assert len(relationships) == 1

    relationship = relationships[0]

    assert relationship.evidence_count == 2
    assert relationship.first_event_id == first_event.id
    assert relationship.last_event_id == second_event.id
    assert relationship.first_seen_at == first_time
    assert relationship.last_seen_at == second_time


def test_earlier_evidence_moves_first_seen_backwards(
    db: Session,
):
    organization = _make_organization(
        db,
        suffix="earlier",
    )

    source = _make_entity(
        db,
        organization_id=organization.id,
        suffix="earlier-source",
    )

    target = _make_entity(
        db,
        organization_id=organization.id,
        suffix="earlier-target",
    )

    later_time = datetime.now(timezone.utc)
    earlier_time = later_time - timedelta(days=1)

    later_event = _make_event(
        db,
        organization_id=organization.id,
        source_entity_id=source.id,
        detected_at=later_time,
        entities_affected=[
            str(target.id),
        ],
        suffix="earlier-later-event",
    )

    earlier_event = _make_event(
        db,
        organization_id=organization.id,
        source_entity_id=source.id,
        detected_at=earlier_time,
        entities_affected=[
            str(target.id),
        ],
        suffix="earlier-earlier-event",
    )

    persist_event_relationships(
        db=db,
        event=later_event,
        source_entity_id=source.id,
    )

    persist_event_relationships(
        db=db,
        event=earlier_event,
        source_entity_id=source.id,
    )

    relationship = (
        db.query(EntityRelationship)
        .filter(
            EntityRelationship.organization_id
            == organization.id,
            EntityRelationship.source_entity_id
            == source.id,
            EntityRelationship.target_entity_id
            == target.id,
        )
        .one()
    )

    assert relationship.evidence_count == 2
    assert relationship.first_seen_at == earlier_time
    assert relationship.first_event_id == earlier_event.id
    assert relationship.last_seen_at == later_time
    assert relationship.last_event_id == later_event.id


def test_self_edge_and_invalid_target_are_skipped(
    db: Session,
):
    organization = _make_organization(
        db,
        suffix="skip",
    )

    source = _make_entity(
        db,
        organization_id=organization.id,
        suffix="skip-source",
    )

    event = _make_event(
        db,
        organization_id=organization.id,
        source_entity_id=source.id,
        detected_at=datetime.now(timezone.utc),
        entities_affected=[
            str(source.id),
            "not-a-uuid",
            str(uuid.uuid4()),
        ],
        suffix="skip-event",
    )

    result = persist_event_relationships(
        db=db,
        event=event,
        source_entity_id=source.id,
    )

    assert result.created == 0
    assert result.updated == 0
    assert result.skipped == 3
    assert result.relationship_ids == ()

    count = (
        db.query(EntityRelationship)
        .filter(
            EntityRelationship.organization_id
            == organization.id
        )
        .count()
    )

    assert count == 0


def test_cross_tenant_target_is_skipped(
    db: Session,
):
    organization_a = _make_organization(
        db,
        suffix="tenant-a",
    )

    organization_b = _make_organization(
        db,
        suffix="tenant-b",
    )

    source = _make_entity(
        db,
        organization_id=organization_a.id,
        suffix="tenant-source-a",
    )

    foreign_target = _make_entity(
        db,
        organization_id=organization_b.id,
        suffix="tenant-target-b",
    )

    event = _make_event(
        db,
        organization_id=organization_a.id,
        source_entity_id=source.id,
        detected_at=datetime.now(timezone.utc),
        entities_affected=[
            str(foreign_target.id),
        ],
        suffix="tenant-event",
    )

    result = persist_event_relationships(
        db=db,
        event=event,
        source_entity_id=source.id,
    )

    assert result.created == 0
    assert result.updated == 0
    assert result.skipped == 1
    assert result.relationship_ids == ()

    count = (
        db.query(EntityRelationship)
        .filter(
            EntityRelationship.organization_id
            == organization_a.id
        )
        .count()
    )

    assert count == 0


def test_cross_tenant_source_is_rejected(
    db: Session,
):
    organization_a = _make_organization(
        db,
        suffix="source-event-org",
    )

    organization_b = _make_organization(
        db,
        suffix="source-foreign-org",
    )

    event_source = _make_entity(
        db,
        organization_id=organization_a.id,
        suffix="source-event-source",
    )

    foreign_source = _make_entity(
        db,
        organization_id=organization_b.id,
        suffix="source-foreign-source",
    )

    event = _make_event(
        db,
        organization_id=organization_a.id,
        source_entity_id=event_source.id,
        detected_at=datetime.now(timezone.utc),
        suffix="source-event",
    )

    with pytest.raises(
        ValueError,
        match="Source entity does not belong",
    ):
        persist_event_relationships(
            db=db,
            event=event,
            source_entity_id=foreign_source.id,
        )


def test_duplicate_target_ids_are_deduplicated(
    db: Session,
):
    organization = _make_organization(
        db,
        suffix="dedupe",
    )

    source = _make_entity(
        db,
        organization_id=organization.id,
        suffix="dedupe-source",
    )

    target = _make_entity(
        db,
        organization_id=organization.id,
        suffix="dedupe-target",
    )

    event = _make_event(
        db,
        organization_id=organization.id,
        source_entity_id=source.id,
        detected_at=datetime.now(timezone.utc),
        entities_affected=[
            str(target.id),
            str(target.id),
            str(target.id),
        ],
        suffix="dedupe-event",
    )

    result = persist_event_relationships(
        db=db,
        event=event,
        source_entity_id=source.id,
    )

    assert result.created == 1
    assert result.updated == 0
    assert result.skipped == 0

    relationship = (
        db.query(EntityRelationship)
        .filter(
            EntityRelationship.organization_id
            == organization.id,
            EntityRelationship.source_entity_id
            == source.id,
            EntityRelationship.target_entity_id
            == target.id,
        )
        .one()
    )

    assert relationship.evidence_count == 1

def test_same_event_retry_is_idempotent(
    db: Session,
):
    organization = _make_organization(
        db,
        suffix="idempotent",
    )

    source = _make_entity(
        db,
        organization_id=organization.id,
        suffix="idempotent-source",
    )

    target = _make_entity(
        db,
        organization_id=organization.id,
        suffix="idempotent-target",
    )

    event = _make_event(
        db,
        organization_id=organization.id,
        source_entity_id=source.id,
        detected_at=datetime.now(timezone.utc),
        entities_affected=[
            str(target.id),
        ],
        suffix="idempotent-event",
    )

    first_result = persist_event_relationships(
        db=db,
        event=event,
        source_entity_id=source.id,
    )

    second_result = persist_event_relationships(
        db=db,
        event=event,
        source_entity_id=source.id,
    )

    assert first_result.created == 1
    assert first_result.updated == 0
    assert first_result.observations_created == 1
    assert first_result.observations_existing == 0

    assert second_result.created == 0
    assert second_result.updated == 0
    assert second_result.observations_created == 0
    assert second_result.observations_existing == 1

    relationships = (
        db.query(EntityRelationship)
        .filter(
            EntityRelationship.organization_id
            == organization.id,
            EntityRelationship.source_entity_id
            == source.id,
            EntityRelationship.target_entity_id
            == target.id,
        )
        .all()
    )

    assert len(relationships) == 1

    relationship = relationships[0]

    assert relationship.evidence_count == 1
    assert relationship.first_event_id == event.id
    assert relationship.last_event_id == event.id

    observations = (
        db.query(EntityRelationshipObservation)
        .filter(
            EntityRelationshipObservation.relationship_id
            == relationship.id
        )
        .all()
    )

    assert len(observations) == 1
    assert observations[0].semantic_drift_event_id == event.id


def test_different_events_create_temporal_observations(
    db: Session,
):
    organization = _make_organization(
        db,
        suffix="observations",
    )

    source = _make_entity(
        db,
        organization_id=organization.id,
        suffix="observations-source",
    )

    target = _make_entity(
        db,
        organization_id=organization.id,
        suffix="observations-target",
    )

    first_time = datetime.now(timezone.utc)
    second_time = first_time + timedelta(minutes=10)

    first_event = _make_event(
        db,
        organization_id=organization.id,
        source_entity_id=source.id,
        detected_at=first_time,
        entities_affected=[
            str(target.id),
        ],
        suffix="observations-first",
    )

    second_event = _make_event(
        db,
        organization_id=organization.id,
        source_entity_id=source.id,
        detected_at=second_time,
        entities_affected=[
            str(target.id),
        ],
        suffix="observations-second",
    )

    first_result = persist_event_relationships(
        db=db,
        event=first_event,
        source_entity_id=source.id,
    )

    second_result = persist_event_relationships(
        db=db,
        event=second_event,
        source_entity_id=source.id,
    )

    assert first_result.created == 1
    assert first_result.observations_created == 1

    assert second_result.created == 0
    assert second_result.updated == 1
    assert second_result.observations_created == 1
    assert second_result.observations_existing == 0

    relationship = (
        db.query(EntityRelationship)
        .filter(
            EntityRelationship.organization_id
            == organization.id,
            EntityRelationship.source_entity_id
            == source.id,
            EntityRelationship.target_entity_id
            == target.id,
        )
        .one()
    )

    assert relationship.evidence_count == 2
    assert relationship.first_event_id == first_event.id
    assert relationship.last_event_id == second_event.id
    assert relationship.first_seen_at == first_time
    assert relationship.last_seen_at == second_time

    observations = (
        db.query(EntityRelationshipObservation)
        .filter(
            EntityRelationshipObservation.relationship_id
            == relationship.id
        )
        .order_by(
            EntityRelationshipObservation.observed_at.asc()
        )
        .all()
    )

    assert len(observations) == 2

    assert (
        observations[0].semantic_drift_event_id
        == first_event.id
    )
    assert observations[0].observed_at == first_time

    assert (
        observations[1].semantic_drift_event_id
        == second_event.id
    )
    assert observations[1].observed_at == second_time

def test_deleting_event_preserves_aggregate_relationship(
    db: Session,
):
    """
    Deleting an event referenced by first_event_id/last_event_id must
    not delete the aggregate graph edge.

    Event-level observations belong to the deleted event and therefore
    may cascade away, while the aggregate relationship survives with
    nullable event pointers.
    """
    organization = _make_organization(
        db,
        suffix="event-lifecycle",
    )

    source = _make_entity(
        db,
        organization_id=organization.id,
        suffix="event-lifecycle-source",
    )

    target = _make_entity(
        db,
        organization_id=organization.id,
        suffix="event-lifecycle-target",
    )

    event = _make_event(
        db,
        organization_id=organization.id,
        source_entity_id=source.id,
        detected_at=datetime.now(timezone.utc),
        entities_affected=[
            str(target.id),
        ],
        suffix="event-lifecycle-event",
    )

    result = persist_event_relationships(
        db=db,
        event=event,
        source_entity_id=source.id,
    )

    assert result.created == 1
    assert result.observations_created == 1

    relationship = (
        db.query(EntityRelationship)
        .filter(
            EntityRelationship.organization_id
            == organization.id,
            EntityRelationship.source_entity_id
            == source.id,
            EntityRelationship.target_entity_id
            == target.id,
        )
        .one()
    )

    relationship_id = relationship.id
    event_id = event.id

    assert relationship.first_event_id == event_id
    assert relationship.last_event_id == event_id

    assert (
        db.query(EntityRelationshipObservation)
        .filter(
            EntityRelationshipObservation.relationship_id
            == relationship_id
        )
        .count()
        == 1
    )

    # Use a Core DELETE so the database FK lifecycle behavior itself
    # is tested instead of SQLAlchemy ORM relationship synchronization.
    db.execute(
        delete(SemanticDriftEvent).where(
            SemanticDriftEvent.id == event_id
        )
    )
    db.flush()

    db.expire_all()

    surviving_relationship = (
        db.query(EntityRelationship)
        .filter(
            EntityRelationship.id == relationship_id
        )
        .one()
    )

    assert surviving_relationship.first_event_id is None
    assert surviving_relationship.last_event_id is None
    assert surviving_relationship.evidence_count == 1

    # Observation is event-level evidence, so deleting its source event
    # currently removes that observation through ON DELETE CASCADE.
    assert (
        db.query(EntityRelationshipObservation)
        .filter(
            EntityRelationshipObservation.relationship_id
            == relationship_id
        )
        .count()
        == 0
    )