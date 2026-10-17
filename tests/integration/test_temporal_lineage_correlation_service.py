from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import delete

from src.core.database import SessionLocal
from src.domain.lineage_observation import LineageObservation
from src.models.entity import Entity
from src.models.lineage_observation import LineageObservationRecord
from src.models.monitored_page import MonitoredPage
from src.models.organization import Organization
from src.models.page_snapshot import PageSnapshot
from src.models.temporal_lineage_correlation import (
    TemporalLineageCorrelationRecord,
)
from src.services.lineage_observation_persistence_service import (
    persist_lineage_observation,
)
from src.services.temporal_lineage_correlation_service import (
    TemporalLineageCorrelationNotFoundError,
    persist_temporal_lineage_correlation,
    resolve_temporal_lineage_roots,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


def _make_organization(
    db,
) -> Organization:
    suffix = uuid.uuid4().hex[:12]
    organization = Organization(
        id=uuid.uuid4(),
        name=f"temporal-lineage-{suffix}",
        slug=f"temporal-lineage-{suffix}",
        billing_email=f"{suffix}@example.com",
    )
    db.add(organization)
    db.flush()
    return organization


def _make_snapshot(
    db,
    *,
    organization: Organization,
) -> PageSnapshot:
    entity_suffix = uuid.uuid4().hex

    entity = Entity(
        id=uuid.uuid4(),
        organization_id=organization.id,
        name=f"Entity {entity_suffix[:8]}",
        domain=f"{entity_suffix}.example.com",
        category="test",
    )
    db.add(entity)
    db.flush()

    page = MonitoredPage(
        id=uuid.uuid4(),
        organization_id=organization.id,
        entity_id=entity.id,
        url=f"https://example.com/{uuid.uuid4().hex}",
        page_type="docs",
        crawl_frequency="daily",
        is_active=True,
    )
    db.add(page)
    db.flush()

    snapshot = PageSnapshot(
        id=uuid.uuid4(),
        monitored_page_id=page.id,
        organization_id=organization.id,
        content_hash=uuid.uuid4().hex,
        raw_content="correlation test",
        content_size=16,
    )
    db.add(snapshot)
    db.flush()

    return snapshot


def _persist_observation(
    db,
    *,
    organization_id: uuid.UUID,
    asset_external_id: str,
    revision_external_id: str,
    sequence: int,
) -> LineageObservationRecord:
    result = persist_lineage_observation(
        db,
        organization_id=organization_id,
        observation=LineageObservation(
            observation_kind="materialization",
            source_external_id="policy-source",
            snapshot_external_id=f"snapshot-{sequence}",
            asset_external_id=asset_external_id,
            asset_kind="index",
            revision_external_id=revision_external_id,
            sequence=sequence,
        ),
    )
    return result.record


def _cleanup(
    organization_ids: list[uuid.UUID],
) -> None:
    if not organization_ids:
        return

    db = SessionLocal()

    try:
        db.rollback()

        db.execute(
            delete(TemporalLineageCorrelationRecord).where(
                TemporalLineageCorrelationRecord.page_snapshot_id.in_(
                    db.query(PageSnapshot.id).filter(
                        PageSnapshot.organization_id.in_(
                            organization_ids
                        )
                    )
                )
            )
        )
        db.execute(
            delete(LineageObservationRecord).where(
                LineageObservationRecord.organization_id.in_(
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
    finally:
        db.close()


def test_correlation_persists_and_replay_is_idempotent() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        snapshot = _make_snapshot(
            db,
            organization=organization,
        )
        observation = _persist_observation(
            db,
            organization_id=organization.id,
            asset_external_id="policy-index",
            revision_external_id="index-v1",
            sequence=1,
        )

        first = persist_temporal_lineage_correlation(
            db,
            organization_id=organization.id,
            page_snapshot_id=snapshot.id,
            lineage_observation_id=observation.id,
        )
        second = persist_temporal_lineage_correlation(
            db,
            organization_id=organization.id,
            page_snapshot_id=snapshot.id,
            lineage_observation_id=observation.id,
        )

        assert first.created is True
        assert second.created is False
        assert (
            first.record.page_snapshot_id
            == second.record.page_snapshot_id
        )
        assert (
            first.record.lineage_observation_id
            == second.record.lineage_observation_id
        )

        db.commit()
    finally:
        db.close()
        _cleanup(organization_ids)


def test_cross_tenant_snapshot_fails_closed() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        first_organization = _make_organization(db)
        second_organization = _make_organization(db)
        organization_ids.extend(
            [
                first_organization.id,
                second_organization.id,
            ]
        )

        snapshot = _make_snapshot(
            db,
            organization=first_organization,
        )
        observation = _persist_observation(
            db,
            organization_id=second_organization.id,
            asset_external_id="policy-index",
            revision_external_id="index-v1",
            sequence=1,
        )

        with pytest.raises(
            TemporalLineageCorrelationNotFoundError
        ):
            persist_temporal_lineage_correlation(
                db,
                organization_id=second_organization.id,
                page_snapshot_id=snapshot.id,
                lineage_observation_id=observation.id,
            )

        db.rollback()
    finally:
        db.close()
        _cleanup(organization_ids)


def test_cross_tenant_observation_fails_closed() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        first_organization = _make_organization(db)
        second_organization = _make_organization(db)
        organization_ids.extend(
            [
                first_organization.id,
                second_organization.id,
            ]
        )

        snapshot = _make_snapshot(
            db,
            organization=first_organization,
        )
        observation = _persist_observation(
            db,
            organization_id=second_organization.id,
            asset_external_id="policy-index",
            revision_external_id="index-v1",
            sequence=1,
        )

        with pytest.raises(
            TemporalLineageCorrelationNotFoundError
        ):
            persist_temporal_lineage_correlation(
                db,
                organization_id=first_organization.id,
                page_snapshot_id=snapshot.id,
                lineage_observation_id=observation.id,
            )

        db.rollback()
    finally:
        db.close()
        _cleanup(organization_ids)


def test_resolver_returns_empty_when_correlation_is_unavailable() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        snapshot = _make_snapshot(
            db,
            organization=organization,
        )

        roots = resolve_temporal_lineage_roots(
            db,
            organization_id=organization.id,
            page_snapshot_id=snapshot.id,
        )

        assert roots == ()

        db.rollback()
    finally:
        db.close()
        _cleanup(organization_ids)


def test_resolver_returns_deterministic_deduplicated_roots() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        organization = _make_organization(db)
        organization_ids.append(organization.id)

        snapshot = _make_snapshot(
            db,
            organization=organization,
        )

        observations = [
            _persist_observation(
                db,
                organization_id=organization.id,
                asset_external_id="z-index",
                revision_external_id="z-v1",
                sequence=1,
            ),
            _persist_observation(
                db,
                organization_id=organization.id,
                asset_external_id="a-index",
                revision_external_id="a-v1",
                sequence=1,
            ),
            _persist_observation(
                db,
                organization_id=organization.id,
                asset_external_id="a-index",
                revision_external_id="a-v1",
                sequence=2,
            ),
        ]

        for observation in observations:
            persist_temporal_lineage_correlation(
                db,
                organization_id=organization.id,
                page_snapshot_id=snapshot.id,
                lineage_observation_id=observation.id,
            )

        roots = resolve_temporal_lineage_roots(
            db,
            organization_id=organization.id,
            page_snapshot_id=snapshot.id,
        )

        assert [
            (
                root.source_external_id,
                root.asset_external_id,
                root.revision_external_id,
            )
            for root in roots
        ] == [
            (
                "policy-source",
                "a-index",
                "a-v1",
            ),
            (
                "policy-source",
                "z-index",
                "z-v1",
            ),
        ]

        db.rollback()
    finally:
        db.close()
        _cleanup(organization_ids)


def test_resolver_rejects_snapshot_from_another_tenant() -> None:
    db = SessionLocal()
    organization_ids: list[uuid.UUID] = []

    try:
        first_organization = _make_organization(db)
        second_organization = _make_organization(db)
        organization_ids.extend(
            [
                first_organization.id,
                second_organization.id,
            ]
        )

        snapshot = _make_snapshot(
            db,
            organization=first_organization,
        )

        with pytest.raises(
            TemporalLineageCorrelationNotFoundError
        ):
            resolve_temporal_lineage_roots(
                db,
                organization_id=second_organization.id,
                page_snapshot_id=snapshot.id,
            )

        db.rollback()
    finally:
        db.close()
        _cleanup(organization_ids)