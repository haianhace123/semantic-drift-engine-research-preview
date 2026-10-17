"""PostgreSQL verification of tenant-scoped, read-only confidence exposure audit."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from src.core.database import SessionLocal
from src.models.organization import Organization
from src.models.risk_assessment import RiskAssessment
from src.services.risk_confidence_exposure_service import (
    RISK_VERSION_V2,
    RISK_VERSION_V3,
    load_scoped_assessments,
    summarize_confidence_exposure,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="Database integration tests are disabled.",
)


def _assessment(org_id, version, score, confidence, assessed_at):
    return RiskAssessment(
        id=uuid.uuid4(),
        organization_id=org_id,
        semantic_drift_event_id=None,
        risk_version=version,
        assessed_at=assessed_at,
        score=score,
        semantic_magnitude_contribution=score - confidence,
        legacy_impact_contribution=0.0,
        semantic_severity_contribution=0.0,
        page_context_contribution=0.0,
        confidence_contribution=confidence,
        topology_score=0.0,
        topology_contribution=0.0,
        topology_shift_component=0.0,
        topology_centrality_component=0.0,
        topology_downstream_component=0.0,
        has_topology_signal=False,
        has_previous_topology_state=False,
    )


def test_audit_enforces_postgres_read_only_and_isolates_tenants():
    primary_org_id, other_org_id = uuid.uuid4(), uuid.uuid4()
    now = datetime.now(timezone.utc)
    creator = SessionLocal()
    try:
        for org_id in (primary_org_id, other_org_id):
            creator.add(Organization(
                id=org_id,
                name="Confidence Exposure Integration",
                slug=f"confidence-audit-{org_id.hex}",
                billing_email="confidence@example.invalid",
            ))
        creator.flush()
        creator.add_all([
            _assessment(primary_org_id, RISK_VERSION_V2, .65, .09, now),
            _assessment(primary_org_id, RISK_VERSION_V2, .48, .09, now - timedelta(minutes=1)),
            _assessment(primary_org_id, RISK_VERSION_V3, .85, .09, now),
            _assessment(other_org_id, RISK_VERSION_V2, .92, .10, now),
        ])
        creator.commit()

        with SessionLocal() as reader:
            reader.connection().exec_driver_sql("SET TRANSACTION READ ONLY")
            assert reader.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
            latest, truncated = load_scoped_assessments(
                reader, organization_id=primary_org_id,
                risk_version=RISK_VERSION_V2, limit=1,
            )
            assert truncated is True
            assert len(latest) == 1 and latest[0].score == .65
            samples_v2, truncated_v2 = load_scoped_assessments(
                reader, organization_id=primary_org_id,
                risk_version=RISK_VERSION_V2, limit=10,
            )
            samples_v3, truncated_v3 = load_scoped_assessments(
                reader, organization_id=primary_org_id,
                risk_version=RISK_VERSION_V3, limit=10,
            )
            assert truncated_v2 is False and truncated_v3 is False
            assert len(samples_v2) == 2 and len(samples_v3) == 1
            assert all(s.score != .92 for s in samples_v2)
            report = summarize_confidence_exposure(samples_v2, risk_version=RISK_VERSION_V2)
            assert report["severity_transitions"] == {"high_to_medium": 1, "medium_to_low": 1}
            reader.rollback()
    finally:
        creator.rollback()
        try:
            creator.query(RiskAssessment).filter(
                RiskAssessment.organization_id.in_([primary_org_id, other_org_id]),
            ).delete(synchronize_session=False)
            creator.query(Organization).filter(
                Organization.id.in_([primary_org_id, other_org_id]),
            ).delete(synchronize_session=False)
            creator.commit()
        finally:
            creator.close()
