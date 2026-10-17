from __future__ import annotations

import os
import uuid

import pytest

from sqlalchemy import delete
from sqlalchemy.orm import Session

from src.api.temporal_routes import (
    _lineage_by_snapshot,
    _serialize_event,
)
from src.core.database import SessionLocal
from src.domain.lineage_observation import (
    LineageObservation,
    LineageRevisionReference,
)
from src.domain.lineage_revision import (
    LineageRevisionIdentity,
)
from src.models import (
    Entity,
    LineageObservationRecord,
    MonitoredPage,
    Organization,
    PageSnapshot,
    TemporalLineageCorrelationRecord,
)
from src.services.lineage_impact_service import (
    analyze_lineage_impact,
)
from src.services.lineage_observation_persistence_service import (
    persist_lineage_observation,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="DB integration tests are disabled",
)


def _create_organization(
    db: Session,
) -> Organization:
    suffix = uuid.uuid4().hex

    organization = Organization(
        id=uuid.uuid4(),
        name=f"Temporal lineage projection {suffix[:8]}",
        slug=f"temporal-lineage-projection-{suffix}",
        billing_email=f"temporal-lineage-{suffix}@example.com",
    )

    db.add(organization)
    db.flush()

    return organization


def _create_snapshot(
    db: Session,
    *,
    organization: Organization,
) -> PageSnapshot:
    suffix = uuid.uuid4().hex

    entity = Entity(
        id=uuid.uuid4(),
        organization_id=organization.id,
        name=f"Entity {suffix[:8]}",
        domain=f"{suffix}.example.com",
        category="test",
    )
    db.add(entity)
    db.flush()

    page = MonitoredPage(
        id=uuid.uuid4(),
        organization_id=organization.id,
        entity_id=entity.id,
        url=f"https://{suffix}.example.com/knowledge",
        page_type="docs",
    )
    db.add(page)
    db.flush()

    snapshot = PageSnapshot(
        id=uuid.uuid4(),
        organization_id=organization.id,
        monitored_page_id=page.id,
        content_hash=f"hash-{suffix}",
        raw_content="knowledge",
        content_size=9,
    )
    db.add(snapshot)
    db.flush()

    return snapshot


def _create_observation(
    db: Session,
    *,
    organization: Organization,
    source_external_id: str,
    asset_external_id: str,
    revision_external_id: str,
    sequence: int,
) -> LineageObservationRecord:
    observation = LineageObservationRecord(
        id=uuid.uuid4(),
        organization_id=organization.id,
        observation_kind="materialization",
        source_external_id=source_external_id,
        snapshot_external_id=f"snapshot-{uuid.uuid4()}",
        asset_external_id=asset_external_id,
        asset_kind="chunk",
        revision_external_id=revision_external_id,
        sequence=sequence,
    )
    db.add(observation)
    db.flush()

    return observation


def _correlate(
    db: Session,
    *,
    snapshot: PageSnapshot,
    observation: LineageObservationRecord,
) -> None:
    db.add(
        TemporalLineageCorrelationRecord(
            page_snapshot_id=snapshot.id,
            lineage_observation_id=observation.id,
        )
    )
    db.flush()


def _cleanup(
    db: Session,
    organization_ids: tuple[uuid.UUID, ...],
) -> None:
    db.rollback()

    snapshot_ids = (
        db.query(PageSnapshot.id)
        .filter(
            PageSnapshot.organization_id.in_(
                organization_ids
            )
        )
    )

    db.execute(
        delete(
            TemporalLineageCorrelationRecord
        ).where(
            TemporalLineageCorrelationRecord.page_snapshot_id.in_(
                snapshot_ids
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


def test_lineage_projection_is_empty_without_correlation():
    db = SessionLocal()
    organization = None

    try:
        organization = _create_organization(db)
        snapshot = _create_snapshot(
            db,
            organization=organization,
        )
        db.commit()

        result = _lineage_by_snapshot(
            db,
            organization.id,
            (snapshot.id,),
        )

        assert result == {}
    finally:
        if organization is not None:
            _cleanup(
                db,
                (organization.id,),
            )
        db.close()


def test_lineage_projection_is_deterministic_and_deduplicated():
    db = SessionLocal()
    organization = None

    try:
        organization = _create_organization(db)
        snapshot = _create_snapshot(
            db,
            organization=organization,
        )

        observation_b = _create_observation(
            db,
            organization=organization,
            source_external_id="source",
            asset_external_id="asset-b",
            revision_external_id="revision-b",
            sequence=2,
        )
        observation_a = _create_observation(
            db,
            organization=organization,
            source_external_id="source",
            asset_external_id="asset-a",
            revision_external_id="revision-a",
            sequence=1,
        )
        observation_a_duplicate = _create_observation(
            db,
            organization=organization,
            source_external_id="source",
            asset_external_id="asset-a",
            revision_external_id="revision-a",
            sequence=3,
        )

        _correlate(
            db,
            snapshot=snapshot,
            observation=observation_b,
        )
        _correlate(
            db,
            snapshot=snapshot,
            observation=observation_a,
        )
        _correlate(
            db,
            snapshot=snapshot,
            observation=observation_a_duplicate,
        )
        db.commit()

        result = _lineage_by_snapshot(
            db,
            organization.id,
            (snapshot.id,),
        )

        assert result[str(snapshot.id)] == (
            {
                "source_external_id": "source",
                "asset_external_id": "asset-a",
                "revision_external_id": "revision-a",
            },
            {
                "source_external_id": "source",
                "asset_external_id": "asset-b",
                "revision_external_id": "revision-b",
            },
        )
    finally:
        if organization is not None:
            _cleanup(
                db,
                (organization.id,),
            )
        db.close()


def test_temporal_lineage_projection_composes_with_exact_impact():
    db = SessionLocal()
    organization = None

    try:
        organization = _create_organization(db)
        snapshot = _create_snapshot(
            db,
            organization=organization,
        )

        root_observation = LineageObservation(
            observation_kind="materialization",
            source_external_id="source",
            snapshot_external_id="snapshot-root",
            asset_external_id="chunk-a",
            asset_kind="chunk",
            revision_external_id="chunk-a@v1",
            sequence=1,
        )
        persist_lineage_observation(
            db,
            organization_id=organization.id,
            observation=root_observation,
        )

        persisted_root = (
            db.query(LineageObservationRecord)
            .filter(
                LineageObservationRecord.organization_id
                == organization.id,
                LineageObservationRecord.source_external_id
                == "source",
                LineageObservationRecord.asset_external_id
                == "chunk-a",
                LineageObservationRecord.revision_external_id
                == "chunk-a@v1",
            )
            .one()
        )

        persist_lineage_observation(
            db,
            organization_id=organization.id,
            observation=LineageObservation(
                observation_kind="materialization",
                source_external_id="source",
                snapshot_external_id="snapshot-index",
                asset_external_id="index-a",
                asset_kind="index",
                revision_external_id="index-a@v1",
                sequence=1,
                dependencies=(
                    LineageRevisionReference(
                        asset_external_id="chunk-a",
                        revision_external_id="chunk-a@v1",
                        dependency_type="derived_from",
                    ),
                ),
            ),
        )

        persist_lineage_observation(
            db,
            organization_id=organization.id,
            observation=LineageObservation(
                observation_kind="consumption",
                source_external_id="source",
                snapshot_external_id="snapshot-agent",
                asset_external_id="agent-a",
                asset_kind="agent",
                revision_external_id="agent-a@v1",
                sequence=1,
                dependencies=(
                    LineageRevisionReference(
                        asset_external_id="index-a",
                        revision_external_id="index-a@v1",
                        dependency_type="retrieves_from",
                    ),
                ),
            ),
        )

        _correlate(
            db,
            snapshot=snapshot,
            observation=persisted_root,
        )
        db.commit()

        lineage_by_snapshot = _lineage_by_snapshot(
            db,
            organization.id,
            (snapshot.id,),
        )

        roots = lineage_by_snapshot[
            str(snapshot.id)
        ]

        assert roots == (
            {
                "source_external_id": "source",
                "asset_external_id": "chunk-a",
                "revision_external_id": "chunk-a@v1",
            },
        )

        projected_root = roots[0]
        root = LineageRevisionIdentity(
            source_external_id=(
                projected_root["source_external_id"]
            ),
            asset_external_id=(
                projected_root["asset_external_id"]
            ),
            revision_external_id=(
                projected_root["revision_external_id"]
            ),
        )

        impact = analyze_lineage_impact(
            db,
            organization_id=organization.id,
            root=root,
        )

        assert impact.root == root
        assert set(impact.impacted_revisions) == {
            LineageRevisionIdentity(
                source_external_id="source",
                asset_external_id="index-a",
                revision_external_id="index-a@v1",
            ),
            LineageRevisionIdentity(
                source_external_id="source",
                asset_external_id="agent-a",
                revision_external_id="agent-a@v1",
            ),
        }

        assert {
            path.revisions
            for path in impact.paths
        } == {
            (
                root,
                LineageRevisionIdentity(
                    source_external_id="source",
                    asset_external_id="index-a",
                    revision_external_id="index-a@v1",
                ),
            ),
            (
                root,
                LineageRevisionIdentity(
                    source_external_id="source",
                    asset_external_id="index-a",
                    revision_external_id="index-a@v1",
                ),
                LineageRevisionIdentity(
                    source_external_id="source",
                    asset_external_id="agent-a",
                    revision_external_id="agent-a@v1",
                ),
            ),
        }
    finally:
        if organization is not None:
            _cleanup(
                db,
                (organization.id,),
            )

        db.close()


def test_lineage_projection_does_not_cross_tenant_boundary():
    db = SessionLocal()
    organization_a = None
    organization_b = None

    try:
        organization_a = _create_organization(db)
        organization_b = _create_organization(db)

        snapshot_a = _create_snapshot(
            db,
            organization=organization_a,
        )
        observation_a = _create_observation(
            db,
            organization=organization_a,
            source_external_id="source-a",
            asset_external_id="asset-a",
            revision_external_id="revision-a",
            sequence=1,
        )

        _correlate(
            db,
            snapshot=snapshot_a,
            observation=observation_a,
        )
        db.commit()

        result = _lineage_by_snapshot(
            db,
            organization_b.id,
            (snapshot_a.id,),
        )

        assert result == {}
    finally:
        organization_ids = tuple(
            organization.id
            for organization in (
                organization_a,
                organization_b,
            )
            if organization is not None
        )

        if organization_ids:
            _cleanup(
                db,
                organization_ids,
            )

        db.close()


def test_event_serialization_marks_lineage_unavailable_without_roots():
    event = type(
        "Event",
        (),
        {
            "id": uuid.uuid4(),
            "organization_id": uuid.uuid4(),
            "category": "content",
            "old_snapshot_id": uuid.uuid4(),
            "new_snapshot_id": uuid.uuid4(),
            "summary_text": "Knowledge changed",
            "risk_level": "medium",
            "confidence": 0.9,
            "impact_score": 0.5,
            "change_type": "semantic",
            "changes_count": 1,
            "is_anomaly": False,
            "is_pattern_part": False,
            "analysis_status": "completed",
            "semantic_delta_json": {},
            "detected_at": None,
            "first_seen_at": None,
            "last_seen_at": None,
        },
    )()

    page = type(
        "Page",
        (),
        {
            "id": uuid.uuid4(),
            "url": "https://example.com/docs",
            "page_type": "docs",
        },
    )()

    entity = type(
        "EntityRecord",
        (),
        {
            "id": uuid.uuid4(),
            "name": "Example",
            "domain": "example.com",
        },
    )()

    payload = _serialize_event(
        event,
        page,
        entity,
    )

    assert payload["lineage"] == {
        "status": "unavailable",
        "roots": [],
    }


def test_event_serialization_marks_exact_lineage_available():
    event = type(
        "Event",
        (),
        {
            "id": uuid.uuid4(),
            "organization_id": uuid.uuid4(),
            "category": "content",
            "old_snapshot_id": uuid.uuid4(),
            "new_snapshot_id": uuid.uuid4(),
            "summary_text": "Knowledge changed",
            "risk_level": "medium",
            "confidence": 0.9,
            "impact_score": 0.5,
            "change_type": "semantic",
            "changes_count": 1,
            "is_anomaly": False,
            "is_pattern_part": False,
            "analysis_status": "completed",
            "semantic_delta_json": {},
            "detected_at": None,
            "first_seen_at": None,
            "last_seen_at": None,
        },
    )()

    page = type(
        "Page",
        (),
        {
            "id": uuid.uuid4(),
            "url": "https://example.com/docs",
            "page_type": "docs",
        },
    )()

    entity = type(
        "EntityRecord",
        (),
        {
            "id": uuid.uuid4(),
            "name": "Example",
            "domain": "example.com",
        },
    )()

    lineage_roots = (
        {
            "source_external_id": "source",
            "asset_external_id": "asset",
            "revision_external_id": "revision",
        },
    )

    payload = _serialize_event(
        event,
        page,
        entity,
        lineage_roots=lineage_roots,
    )

    assert payload["lineage"] == {
        "status": "available",
        "roots": [
            {
                "source_external_id": "source",
                "asset_external_id": "asset",
                "revision_external_id": "revision",
            },
        ],
    }
