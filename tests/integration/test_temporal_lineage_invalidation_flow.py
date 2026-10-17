from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import delete

from src.core.database import SessionLocal
from src.domain.lineage_observation import (
    LineageObservation,
    LineageRevisionReference,
)
from src.models.entity import Entity
from src.models.lineage_invalidation_decision import (
    LineageInvalidationDecisionRecord,
)
from src.models.lineage_observation import LineageObservationRecord
from src.models.monitored_page import MonitoredPage
from src.models.organization import Organization
from src.models.page_snapshot import PageSnapshot
from src.models.temporal_lineage_correlation import (
    TemporalLineageCorrelationRecord,
)
from src.models.semantic_drift_event import (
    SemanticDriftEvent,
    VECTOR_ANALYSIS_STATUS_COMPLETED,
)
from src.services.lineage_invalidation_policy_service import (
    DEFAULT_LINEAGE_INVALIDATION_POLICY,
)
from src.services.lineage_observation_persistence_service import (
    persist_lineage_observation,
)
from src.services.temporal_lineage_correlation_service import (
    persist_temporal_lineage_correlation,
)
from src.services.temporal_lineage_invalidation_service import (
    analyze_temporal_lineage_invalidation,
)


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="RUN_DB_INTEGRATION_TESTS is not enabled",
)


def _create_organization(db) -> Organization:
    suffix = uuid.uuid4().hex[:12]
    organization = Organization(
        name=f"temporal-lineage-invalidation-{suffix}",
        slug=f"temporal-lineage-invalidation-{suffix}",
        billing_email=f"{suffix}@example.test",
        subscription_tier="free",
        quota_urls=10,
        quota_requests=1000,
        is_active=True,
    )
    db.add(organization)
    db.flush()
    return organization


def _create_page(
    db,
    *,
    organization: Organization,
) -> MonitoredPage:
    suffix = uuid.uuid4().hex

    entity = Entity(
        id=uuid.uuid4(),
        organization_id=organization.id,
        name=f"Entity {suffix[:8]}",
        domain=f"{suffix}.example.test",
        category="test",
    )
    db.add(entity)
    db.flush()

    page = MonitoredPage(
        id=uuid.uuid4(),
        organization_id=organization.id,
        entity_id=entity.id,
        url=f"https://example.test/{suffix}",
        page_type="docs",
        crawl_frequency="daily",
        is_active=True,
    )
    db.add(page)
    db.flush()
    return page


def _create_snapshot(
    db,
    *,
    organization: Organization,
    page: MonitoredPage,
    raw_content: str,
) -> PageSnapshot:
    snapshot = PageSnapshot(
        id=uuid.uuid4(),
        monitored_page_id=page.id,
        organization_id=organization.id,
        content_hash=uuid.uuid4().hex,
        raw_content=raw_content,
        content_size=len(raw_content),
    )
    db.add(snapshot)
    db.flush()
    return snapshot


def _root_observation() -> LineageObservation:
    return LineageObservation(
        observation_kind="materialization",
        source_external_id="policy-source",
        snapshot_external_id="external-snapshot-v1",
        asset_external_id="policy-chunk",
        asset_kind="chunk",
        revision_external_id="policy-chunk@v1",
        sequence=1,
    )


def _index_observation() -> LineageObservation:
    return LineageObservation(
        observation_kind="materialization",
        source_external_id="policy-source",
        snapshot_external_id="external-snapshot-v1",
        asset_external_id="policy-index",
        asset_kind="index",
        revision_external_id="policy-index@v1",
        sequence=1,
        dependencies=(
            LineageRevisionReference(
                asset_external_id="policy-chunk",
                revision_external_id="policy-chunk@v1",
                dependency_type="indexed_from",
            ),
        ),
    )


def _delete_organization(
    organization_id: uuid.UUID,
) -> None:
    with SessionLocal() as db:
        db.rollback()

        snapshot_ids = db.query(PageSnapshot.id).filter(
            PageSnapshot.organization_id == organization_id,
        )

        db.execute(
            delete(TemporalLineageCorrelationRecord).where(
                TemporalLineageCorrelationRecord.page_snapshot_id.in_(
                    snapshot_ids
                )
            )
        )
        db.execute(
            delete(LineageInvalidationDecisionRecord).where(
                LineageInvalidationDecisionRecord.organization_id
                == organization_id
            )
        )
        db.execute(
            delete(LineageObservationRecord).where(
                LineageObservationRecord.organization_id
                == organization_id
            )
        )
        db.execute(
            delete(SemanticDriftEvent).where(
                SemanticDriftEvent.organization_id
                == organization_id
            )
        )
        db.execute(
            delete(PageSnapshot).where(
                PageSnapshot.organization_id
                == organization_id
            )
        )
        db.execute(
            delete(MonitoredPage).where(
                MonitoredPage.organization_id
                == organization_id
            )
        )
        db.execute(
            delete(Entity).where(
                Entity.organization_id
                == organization_id
            )
        )
        db.execute(
            delete(Organization).where(
                Organization.id == organization_id
            )
        )
        db.commit()


def test_temporal_change_invalidates_exact_old_snapshot_dependency() -> None:
    organization_id = None

    try:
        with SessionLocal() as db:
            organization = _create_organization(db)
            organization_id = organization.id

            page = _create_page(
                db,
                organization=organization,
            )

            old_snapshot = _create_snapshot(
                db,
                organization=organization,
                page=page,
                raw_content="Refund period is 30 days.",
            )
            new_snapshot = _create_snapshot(
                db,
                organization=organization,
                page=page,
                raw_content="Refund period is 14 days.",
            )

            root = persist_lineage_observation(
                db,
                organization_id=organization.id,
                observation=_root_observation(),
            )
            persist_lineage_observation(
                db,
                organization_id=organization.id,
                observation=_index_observation(),
            )

            persist_temporal_lineage_correlation(
                db,
                organization_id=organization.id,
                page_snapshot_id=old_snapshot.id,
                lineage_observation_id=root.record.id,
            )

            event = SemanticDriftEvent(
                id=uuid.uuid4(),
                monitored_page_id=page.id,
                organization_id=organization.id,
                old_snapshot_id=old_snapshot.id,
                new_snapshot_id=new_snapshot.id,
                semantic_delta_json={
                    "changes": [
                        {
                            "before": "30 days",
                            "after": "14 days",
                        }
                    ]
                },
                analysis_status="completed",
                vector_analysis_status=(
                    VECTOR_ANALYSIS_STATUS_COMPLETED
                ),
                risk_level="medium",
                confidence=0.95,
                impact_score=50,
                changes_count=1,
            )
            db.add(event)
            db.commit()

            event_id = event.id
            old_snapshot_id = old_snapshot.id
            new_snapshot_id = new_snapshot.id

        with SessionLocal() as db:
            result = analyze_temporal_lineage_invalidation(
                db,
                organization_id=organization_id,
                event_id=event_id,
                policy_identity=(
                    DEFAULT_LINEAGE_INVALIDATION_POLICY
                ),
            )

            assert result.event_id == event_id
            assert result.old_snapshot_id == old_snapshot_id
            assert result.new_snapshot_id == new_snapshot_id

            assert len(result.roots) == 1
            assert (
                result.roots[0].source_external_id
                == "policy-source"
            )
            assert (
                result.roots[0].asset_external_id
                == "policy-chunk"
            )
            assert (
                result.roots[0].revision_external_id
                == "policy-chunk@v1"
            )

            assert len(result.analyses) == 1
            analysis = result.analyses[0]

            assert len(
                analysis.impact.impacted_revisions
            ) == 1

            impacted = (
                analysis.impact.impacted_revisions[0]
            )
            assert (
                impacted.asset_external_id
                == "policy-index"
            )
            assert (
                impacted.revision_external_id
                == "policy-index@v1"
            )

            assert len(analysis.decisions) == 1
            persisted = analysis.decisions[0]
            assert persisted.created is True

            decision = persisted.record
            assert decision.disposition == "invalidate"
            assert (
                decision.reason_code
                == "causal_dependency_changed"
            )

            db.commit()

        with SessionLocal() as db:
            decisions = (
                db.query(
                    LineageInvalidationDecisionRecord
                )
                .filter(
                    LineageInvalidationDecisionRecord.organization_id
                    == organization_id,
                )
                .all()
            )

            assert len(decisions) == 1
            assert (
                decisions[0].root_asset_external_id
                == "policy-chunk"
            )
            assert (
                decisions[0].root_revision_external_id
                == "policy-chunk@v1"
            )
            assert (
                decisions[0].target_asset_external_id
                == "policy-index"
            )
            assert (
                decisions[0].target_revision_external_id
                == "policy-index@v1"
            )
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)


def test_semantically_equivalent_edit_does_not_invalidate_correlated_index() -> None:
    """Real correlation and index dependency must not cause false blocks."""
    organization_id = None
    try:
        with SessionLocal() as db:
            organization = _create_organization(db)
            organization_id = organization.id
            page = _create_page(db, organization=organization)
            old_snapshot = _create_snapshot(
                db, organization=organization, page=page,
                raw_content="Refund requests are accepted within 30 days.",
            )
            new_snapshot = _create_snapshot(
                db, organization=organization, page=page,
                raw_content="You can request a refund up to 30 days after purchase.",
            )
            root = persist_lineage_observation(
                db, organization_id=organization.id,
                observation=_root_observation(),
            )
            persist_lineage_observation(
                db, organization_id=organization.id,
                observation=_index_observation(),
            )
            persist_temporal_lineage_correlation(
                db, organization_id=organization.id,
                page_snapshot_id=old_snapshot.id,
                lineage_observation_id=root.record.id,
            )
            event = SemanticDriftEvent(
                id=uuid.uuid4(), monitored_page_id=page.id,
                organization_id=organization.id,
                old_snapshot_id=old_snapshot.id,
                new_snapshot_id=new_snapshot.id,
                semantic_delta_json={"semantic_analysis": {"changes": []}},
                analysis_status="completed",
                vector_analysis_status=VECTOR_ANALYSIS_STATUS_COMPLETED,
                risk_level="low", confidence=0.95, impact_score=0,
                changes_count=0,
            )
            db.add(event)
            db.commit()
            event_id = event.id
            old_snapshot_id = old_snapshot.id

        with SessionLocal() as db:
            result = analyze_temporal_lineage_invalidation(
                db, organization_id=organization_id,
                event_id=event_id,
                policy_identity=DEFAULT_LINEAGE_INVALIDATION_POLICY,
            )
            assert result.roots == ()
            assert result.analyses == ()
            db.commit()

        with SessionLocal() as db:
            assert db.query(LineageInvalidationDecisionRecord).filter(
                LineageInvalidationDecisionRecord.organization_id == organization_id,
            ).count() == 0
            # The observation and correlation still exist: lack of a decision
            # is caused by nonmateriality, not missing lineage coverage.
            assert db.query(TemporalLineageCorrelationRecord).filter(
                TemporalLineageCorrelationRecord.page_snapshot_id == old_snapshot_id,
            ).count() == 1
    finally:
        if organization_id is not None:
            _delete_organization(organization_id)
