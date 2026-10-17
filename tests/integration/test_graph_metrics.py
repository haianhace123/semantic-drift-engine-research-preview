from __future__ import annotations

import uuid

from sqlalchemy import delete
from sqlalchemy.orm import Session

from src.core.database import SessionLocal
from src.models.entity import Entity
from src.models.entity_relationship import EntityRelationship
from src.models.organization import Organization
from src.services.graph_metrics_service import (
    calculate_organization_graph_metrics,
)


def _make_organization(
    db: Session,
    *,
    suffix: str,
) -> Organization:
    organization = Organization(
        id=uuid.uuid4(),
        name=f"graph-metrics-{suffix}",
        slug=(
            f"graph-metrics-{suffix}-"
            f"{uuid.uuid4().hex[:8]}"
        ),
        billing_email=(
            f"graph-metrics-{suffix}-"
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
        name=f"Graph Metrics {suffix}",
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


def _make_relationship(
    db: Session,
    *,
    organization_id: uuid.UUID,
    source_entity_id: uuid.UUID,
    target_entity_id: uuid.UUID,
    is_active: bool = True,
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
        is_active=is_active,
    )

    db.add(relationship)
    db.flush()

    return relationship


def test_organization_metrics_use_only_active_edges():
    db = SessionLocal()
    organization_id = None

    try:
        suffix = uuid.uuid4().hex[:8]

        organization = _make_organization(
            db,
            suffix=suffix,
        )

        organization_id = organization.id

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

        _make_relationship(
            db,
            organization_id=organization.id,
            source_entity_id=first.id,
            target_entity_id=second.id,
            is_active=True,
        )

        _make_relationship(
            db,
            organization_id=organization.id,
            source_entity_id=second.id,
            target_entity_id=third.id,
            is_active=False,
        )

        db.commit()

        result = (
            calculate_organization_graph_metrics(
                db=db,
                organization_id=organization.id,
            )
        )

        assert result.entity_count == 3
        assert result.active_edge_count == 1

        by_entity = {
            item.entity_id: item
            for item in result.entities
        }

        assert by_entity[first.id].out_degree == 1
        assert by_entity[first.id].downstream_reach == 1

        assert by_entity[second.id].in_degree == 1
        assert by_entity[second.id].out_degree == 0
        assert by_entity[second.id].downstream_reach == 0

        assert by_entity[third.id].degree == 0
        assert by_entity[third.id].downstream_reach == 0

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


def test_organization_metrics_are_tenant_isolated():
    db = SessionLocal()

    first_organization_id = None
    second_organization_id = None

    try:
        suffix = uuid.uuid4().hex[:8]

        first_organization = _make_organization(
            db,
            suffix=f"{suffix}-tenant-a",
        )

        second_organization = _make_organization(
            db,
            suffix=f"{suffix}-tenant-b",
        )

        first_organization_id = (
            first_organization.id
        )

        second_organization_id = (
            second_organization.id
        )

        a1 = _make_entity(
            db,
            organization_id=first_organization.id,
            suffix=f"{suffix}-a1",
        )

        a2 = _make_entity(
            db,
            organization_id=first_organization.id,
            suffix=f"{suffix}-a2",
        )

        b1 = _make_entity(
            db,
            organization_id=second_organization.id,
            suffix=f"{suffix}-b1",
        )

        b2 = _make_entity(
            db,
            organization_id=second_organization.id,
            suffix=f"{suffix}-b2",
        )

        b3 = _make_entity(
            db,
            organization_id=second_organization.id,
            suffix=f"{suffix}-b3",
        )

        _make_relationship(
            db,
            organization_id=first_organization.id,
            source_entity_id=a1.id,
            target_entity_id=a2.id,
        )

        _make_relationship(
            db,
            organization_id=second_organization.id,
            source_entity_id=b1.id,
            target_entity_id=b2.id,
        )

        _make_relationship(
            db,
            organization_id=second_organization.id,
            source_entity_id=b2.id,
            target_entity_id=b3.id,
        )

        db.commit()

        result = (
            calculate_organization_graph_metrics(
                db=db,
                organization_id=first_organization.id,
            )
        )

        assert (
            result.organization_id
            == first_organization.id
        )

        assert result.entity_count == 2
        assert result.active_edge_count == 1

        returned_entity_ids = {
            item.entity_id
            for item in result.entities
        }

        assert returned_entity_ids == {
            a1.id,
            a2.id,
        }

        assert b1.id not in returned_entity_ids
        assert b2.id not in returned_entity_ids
        assert b3.id not in returned_entity_ids

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


def test_empty_organization_returns_empty_graph():
    db = SessionLocal()
    organization_id = None

    try:
        suffix = uuid.uuid4().hex[:8]

        organization = _make_organization(
            db,
            suffix=f"{suffix}-empty",
        )

        organization_id = organization.id

        db.commit()

        result = (
            calculate_organization_graph_metrics(
                db=db,
                organization_id=organization.id,
            )
        )

        assert result.entity_count == 0
        assert result.active_edge_count == 0
        assert result.entities == ()

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