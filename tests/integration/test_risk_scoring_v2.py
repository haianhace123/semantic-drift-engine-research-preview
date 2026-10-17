from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
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
from src.services.event_topology_risk_service import (
    load_event_topology_risk_signal,
)
from src.services.graph_metric_snapshot_service import (
    persist_graph_metric_snapshots_for_event,
)
from src.services.risk_scoring_v2_service import (
    RiskScoreV2Input,
    calculate_risk_score_v2,
)
from src.services.topology_shift_snapshot_service import (
    persist_topology_shift_snapshots_for_event,
)


def _make_organization(
    db: Session,
    *,
    suffix: str,
) -> Organization:
    organization = Organization(
        id=uuid.uuid4(),
        name=f"risk-v2-{suffix}",
        slug=(
            f"risk-v2-{suffix}-"
            f"{uuid.uuid4().hex[:8]}"
        ),
        billing_email=(
            f"risk-v2-{suffix}-"
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
        name=f"Risk V2 {suffix}",
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
    raw_content = f"risk v2 {suffix}"

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
        evidence_method="risk_v2_test",
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
        evidence_method="risk_v2_test",
        observed_at=observed_at,
        created_at=observed_at,
    )

    db.add(observation)
    db.flush()

    return relationship


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


def test_persisted_topology_drives_risk_v2():
    db = SessionLocal()
    organization_id = None

    try:
        t1 = datetime(
            2026,
            8,
            30,
            8,
            0,
            tzinfo=timezone.utc,
        )

        t2 = datetime(
            2026,
            8,
            30,
            9,
            0,
            tzinfo=timezone.utc,
        )

        suffix = uuid.uuid4().hex[:8]

        organization = _make_organization(
            db,
            suffix=suffix,
        )

        organization_id = organization.id

        entity_a = _make_entity(
            db,
            organization_id=organization.id,
            suffix=f"{suffix}-a",
        )

        entity_b = _make_entity(
            db,
            organization_id=organization.id,
            suffix=f"{suffix}-b",
        )

        entity_c = _make_entity(
            db,
            organization_id=organization.id,
            suffix=f"{suffix}-c",
        )

        page = _make_page(
            db,
            organization_id=organization.id,
            entity_id=entity_a.id,
            suffix=suffix,
        )

        event_t1 = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=t1,
            suffix="risk-v2-t1",
        )

        _make_relationship(
            db,
            organization_id=organization.id,
            source_entity_id=entity_a.id,
            target_entity_id=entity_b.id,
            observed_at=t1,
            semantic_drift_event_id=event_t1.id,
        )

        db.commit()

        persist_graph_metric_snapshots_for_event(
            db=db,
            event=event_t1,
        )
        db.commit()

        persist_topology_shift_snapshots_for_event(
            db=db,
            event=event_t1,
        )
        db.commit()

        event_t2 = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=t2,
            suffix="risk-v2-t2",
        )

        _make_relationship(
            db,
            organization_id=organization.id,
            source_entity_id=entity_b.id,
            target_entity_id=entity_c.id,
            observed_at=t2,
            semantic_drift_event_id=event_t2.id,
        )

        db.commit()

        persist_graph_metric_snapshots_for_event(
            db=db,
            event=event_t2,
        )
        db.commit()

        persist_topology_shift_snapshots_for_event(
            db=db,
            event=event_t2,
        )
        db.commit()

        topology_signal = (
            load_event_topology_risk_signal(
                db,
                event_t2,
            )
        )

        assert topology_signal is not None

        assert (
            topology_signal.organization_id
            == organization.id
        )

        assert topology_signal.event_id == event_t2.id
        assert topology_signal.entity_count == 3

        assert (
            topology_signal.has_previous_state
            is True
        )

        # A -> B becomes A -> B -> C.
        # At least one entity gains downstream reach.
        assert topology_signal.shift_score > 0.0

        assert (
            topology_signal
            .downstream_reach_ratio_delta
            > 0.0
        )

        result = calculate_risk_score_v2(
            RiskScoreV2Input(
                semantic_magnitude=0.8,
                legacy_impact=0.6,
                semantic_severity=0.7,
                page_context=0.5,
                confidence=0.9,
                topology=topology_signal.risk_input,
            )
        )

        # Semantic-only subtotal for these inputs is 0.54.
        assert result.score > 0.54

        assert result.has_topology_signal is True
        assert (
            result.has_previous_topology_state
            is True
        )

        assert result.topology_score > 0.0
        assert result.topology_contribution > 0.0

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_first_topology_state_does_not_inflate_risk():
    db = SessionLocal()
    organization_id = None

    try:
        detected_at = datetime(
            2026,
            8,
            30,
            10,
            0,
            tzinfo=timezone.utc,
        )

        suffix = uuid.uuid4().hex[:8]

        organization = _make_organization(
            db,
            suffix=suffix,
        )

        organization_id = organization.id

        entity_a = _make_entity(
            db,
            organization_id=organization.id,
            suffix=f"{suffix}-a",
        )

        entity_b = _make_entity(
            db,
            organization_id=organization.id,
            suffix=f"{suffix}-b",
        )

        _make_entity(
            db,
            organization_id=organization.id,
            suffix=f"{suffix}-c",
        )

        page = _make_page(
            db,
            organization_id=organization.id,
            entity_id=entity_a.id,
            suffix=suffix,
        )

        event = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=detected_at,
            suffix="risk-v2-baseline",
        )

        _make_relationship(
            db,
            organization_id=organization.id,
            source_entity_id=entity_a.id,
            target_entity_id=entity_b.id,
            observed_at=detected_at,
            semantic_drift_event_id=event.id,
        )

        db.commit()

        persist_graph_metric_snapshots_for_event(
            db=db,
            event=event,
        )
        db.commit()

        persist_topology_shift_snapshots_for_event(
            db=db,
            event=event,
        )
        db.commit()

        topology_signal = (
            load_event_topology_risk_signal(
                db,
                event,
            )
        )

        assert topology_signal is not None

        # The baseline can have large deltas versus an empty graph.
        assert topology_signal.shift_score >= 0.0

        # But it must be marked as having no previous state.
        assert (
            topology_signal.has_previous_state
            is False
        )

        result = calculate_risk_score_v2(
            RiskScoreV2Input(
                semantic_magnitude=0.8,
                legacy_impact=0.6,
                semantic_severity=0.7,
                page_context=0.5,
                confidence=0.9,
                topology=topology_signal.risk_input,
            )
        )

        assert (
            result.has_topology_signal
            is True
        )

        assert (
            result.has_previous_topology_state
            is False
        )

        assert result.topology_score == 0.0
        assert result.topology_contribution == 0.0

        assert result.score == pytest.approx(
            0.54,
            abs=1e-6,
        )

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()


def test_missing_topology_history_falls_back_cleanly():
    db = SessionLocal()
    organization_id = None

    try:
        detected_at = datetime(
            2026,
            8,
            30,
            11,
            0,
            tzinfo=timezone.utc,
        )

        suffix = uuid.uuid4().hex[:8]

        organization = _make_organization(
            db,
            suffix=suffix,
        )

        organization_id = organization.id

        entity = _make_entity(
            db,
            organization_id=organization.id,
            suffix=f"{suffix}-a",
        )

        page = _make_page(
            db,
            organization_id=organization.id,
            entity_id=entity.id,
            suffix=suffix,
        )

        event = _make_event(
            db,
            organization_id=organization.id,
            monitored_page_id=page.id,
            detected_at=detected_at,
            suffix="risk-v2-no-topology",
        )

        db.commit()

        topology_signal = (
            load_event_topology_risk_signal(
                db,
                event,
            )
        )

        assert topology_signal is None

        result = calculate_risk_score_v2(
            RiskScoreV2Input(
                semantic_magnitude=0.8,
                legacy_impact=0.6,
                semantic_severity=0.7,
                page_context=0.5,
                confidence=0.9,
                topology=None,
            )
        )

        assert result.has_topology_signal is False
        assert result.topology_score == 0.0
        assert result.topology_contribution == 0.0

        assert result.score == pytest.approx(
            0.54,
            abs=1e-6,
        )

    finally:
        try:
            _cleanup(
                db,
                organization_id,
            )
        finally:
            db.close()