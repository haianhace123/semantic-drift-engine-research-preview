from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

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
from src.services.graph_metrics_service import (
    calculate_organization_graph_metrics_as_of,
)


TEST_PREFIX = "graph-metrics-knowledge-time-test"


def _make_organization(
    db: Session,
    *,
    suffix: str,
) -> Organization:
    organization = Organization(
        id=uuid.uuid4(),
        name=f"{TEST_PREFIX}-{suffix}",
        slug=(
            f"{TEST_PREFIX}-{suffix}-"
            f"{uuid.uuid4().hex[:8]}"
        ),
        billing_email=(
            f"{TEST_PREFIX}-{suffix}-"
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
        name=f"{TEST_PREFIX}-{suffix}",
        domain=(
            f"{TEST_PREFIX}-{suffix}-"
            f"{uuid.uuid4().hex[:8]}"
            ".example.com"
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
            f"{uuid.uuid4().hex[:8]}"
            ".example.com/docs"
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
        content_hash=(
            uuid.uuid4().hex
            + uuid.uuid4().hex
        ),
        raw_content=f"{TEST_PREFIX}-{suffix}",
        content_size=len(
            f"{TEST_PREFIX}-{suffix}"
        ),
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
    suffix: str,
) -> SemanticDriftEvent:
    page = _make_monitored_page(
        db,
        organization_id=organization_id,
        entity_id=source_entity_id,
        suffix=suffix,
    )

    old_snapshot = _make_snapshot(
        db,
        organization_id=organization_id,
        monitored_page_id=page.id,
        suffix=f"{suffix}-old",
        crawled_at=detected_at - timedelta(minutes=1),
    )

    new_snapshot = _make_snapshot(
        db,
        organization_id=organization_id,
        monitored_page_id=page.id,
        suffix=f"{suffix}-new",
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
            "suffix": suffix,
        },
        summary_text=f"{TEST_PREFIX}-{suffix}",
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
        entities_affected=[],
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


def test_as_of_graph_excludes_observation_created_after_cursor():
    """
    Historical graph state must obey knowledge time as well as semantic time.

    A relationship observation may represent evidence whose effective
    observed_at timestamp is before the replay cursor while the observation
    itself was persisted later by a backfill.

    Such future-created evidence must not leak into a historical graph state
    that predates its creation.
    """
    db = SessionLocal()
    organization_id = None

    try:
        suffix = uuid.uuid4().hex[:8]

        organization = _make_organization(
            db,
            suffix=suffix,
        )
        organization_id = organization.id

        source = _make_entity(
            db,
            organization_id=organization.id,
            suffix=f"{suffix}-source",
        )

        target = _make_entity(
            db,
            organization_id=organization.id,
            suffix=f"{suffix}-target",
        )

        cursor = datetime(
            2026,
            1,
            15,
            12,
            0,
            0,
            tzinfo=timezone.utc,
        )

        event = _make_event(
            db,
            organization_id=organization.id,
            source_entity_id=source.id,
            detected_at=cursor - timedelta(days=2),
            suffix=f"{suffix}-event",
        )

        relationship = EntityRelationship(
            id=uuid.uuid4(),
            organization_id=organization.id,
            source_entity_id=source.id,
            target_entity_id=target.id,
            relationship_type="mentioned",
            evidence_method="test",
            evidence_count=1,
            strength=1.0,
            confidence=1.0,
            is_active=True,
        )

        db.add(relationship)
        db.flush()

        observation = EntityRelationshipObservation(
            id=uuid.uuid4(),
            organization_id=organization.id,
            relationship_id=relationship.id,
            semantic_drift_event_id=event.id,
            source_entity_id=source.id,
            target_entity_id=target.id,
            relationship_type="mentioned",
            evidence_method="test",
            observed_at=cursor - timedelta(days=1),
            created_at=cursor + timedelta(days=1),
        )

        db.add(observation)
        db.commit()

        result = calculate_organization_graph_metrics_as_of(
            db=db,
            organization_id=organization.id,
            observed_at=cursor,
            known_at=cursor,
        )

        assert result.organization_id == organization.id
        assert result.entity_count == 2

        # Critical knowledge-time invariant:
        #
        # observed_at < cursor
        # created_at > cursor
        #
        # Therefore this edge was not yet known at cursor and must not
        # appear in the historical graph.
        assert result.active_edge_count == 0

        by_entity = {
            item.entity_id: item
            for item in result.entities
        }

        assert by_entity[source.id].in_degree == 0
        assert by_entity[source.id].out_degree == 0
        assert by_entity[source.id].degree == 0
        assert by_entity[source.id].downstream_reach == 0

        assert by_entity[target.id].in_degree == 0
        assert by_entity[target.id].out_degree == 0
        assert by_entity[target.id].degree == 0
        assert by_entity[target.id].downstream_reach == 0

    finally:
        db.rollback()

        if organization_id is not None:
            try:
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

def test_event_effective_graph_includes_relationship_persisted_after_event_time():
    db = SessionLocal()
    organization_id = None

    try:
        suffix = uuid.uuid4().hex[:8]

        organization = _make_organization(
            db,
            suffix=suffix,
        )
        organization_id = organization.id

        source = _make_entity(
            db,
            organization_id=organization.id,
            suffix=f"{suffix}-source",
        )

        target = _make_entity(
            db,
            organization_id=organization.id,
            suffix=f"{suffix}-target",
        )

        detected_at = (
            datetime.now(timezone.utc)
            - timedelta(days=30)
        )

        event = _make_event(
            db,
            organization_id=organization.id,
            source_entity_id=source.id,
            detected_at=detected_at,
            suffix=f"{suffix}-event",
        )

        result = persist_event_relationships(
            db=db,
            event=event,
            source_entity_id=source.id,
            target_entity_ids=[target.id],
        )

        assert result.observations_created == 1

        observation = (
            db.query(EntityRelationshipObservation)
            .filter(
                EntityRelationshipObservation.semantic_drift_event_id
                == event.id
            )
            .one()
        )

        assert observation.observed_at == detected_at
        assert observation.created_at > detected_at

        graph = calculate_organization_graph_metrics_as_of(
            db=db,
            organization_id=organization.id,
            observed_at=detected_at,
        )

        assert graph.entity_count == 2
        assert graph.active_edge_count == 1

        by_entity = {
            item.entity_id: item
            for item in graph.entities
        }

        assert by_entity[source.id].out_degree == 1
        assert by_entity[source.id].downstream_reach == 1
        assert by_entity[target.id].in_degree == 1

    finally:
        db.rollback()

        if organization_id is not None:
            try:
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
