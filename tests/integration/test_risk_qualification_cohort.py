"""Risk qualification reads paired real events without cross-tenant leakage."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from src.core.database import SessionLocal
from src.models.risk_assessment import RiskAssessment
from src.services.risk_qualification_cohort_service import (
    load_qualification_cohort, summarize_risk_qualification,
)
from src.services.risk_version import RISK_VERSION_V2, RISK_VERSION_V3
from tests.integration.test_risk_scoring_v2 import (
    _cleanup, _make_entity, _make_event, _make_organization, _make_page,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="Database integration tests are disabled",
)


def create_assessment(org_id, event_id, risk_version, score=.43, topology=False):
    return RiskAssessment(
        id=uuid.uuid4(), organization_id=org_id,
        semantic_drift_event_id=event_id, risk_version=risk_version,
        assessed_at=datetime.now(timezone.utc), score=score,
        semantic_magnitude_contribution=score-.09-(.05 if topology else 0),
        legacy_impact_contribution=0,
        semantic_severity_contribution=0,
        page_context_contribution=0,
        confidence_contribution=.09,
        topology_score=.2 if topology else 0,
        topology_contribution=.05 if topology else 0,
        topology_shift_component=.2 if topology else 0,
        topology_centrality_component=0,
        topology_downstream_component=0,
        has_topology_signal=True,
        has_previous_topology_state=topology,
    )


def test_qualification_is_tenant_scoped_event_paired_and_read_only():
    creator = SessionLocal()
    org_ids = []
    try:
        for _ in range(2):
            suffix = uuid.uuid4().hex[:8]
            org = _make_organization(creator, suffix=suffix)
            org_ids.append(org.id)
            entity = _make_entity(creator, organization_id=org.id, suffix=suffix)
            page = _make_page(creator, organization_id=org.id, entity_id=entity.id, suffix=suffix)
            event = _make_event(creator, organization_id=org.id, monitored_page_id=page.id,
                                detected_at=datetime.now(timezone.utc), suffix=suffix)
            creator.add(create_assessment(org.id, event.id, RISK_VERSION_V2, topology=True))
            creator.add(create_assessment(org.id, event.id, RISK_VERSION_V3))
            if len(org_ids) == 1:
                event2 = _make_event(creator, organization_id=org.id, monitored_page_id=page.id,
                                     detected_at=datetime.now(timezone.utc), suffix=suffix + "-2")
                creator.add(create_assessment(org.id, event2.id, RISK_VERSION_V2))
        creator.commit()

        with SessionLocal() as reader:
            reader.connection().exec_driver_sql(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
            )
            assert reader.execute(text("SHOW transaction_read_only")).scalar_one() == "on"
            baseline, paired, truncated = load_qualification_cohort(
                reader, organization_id=org_ids[0], limit=1,
            )
            assert len(baseline) == 1 and truncated is True
            assert all(row.organization_id == org_ids[0] for row in baseline + paired)
            all_baseline, all_paired, all_truncated = load_qualification_cohort(
                reader, organization_id=org_ids[0], limit=10,
            )
            report = summarize_risk_qualification(
                all_baseline, all_paired, organization_id=org_ids[0], truncated=all_truncated,
            )
            assert report["linked_unique_baseline_events"] == 2
            assert report["paired_shadow_events"] == 1
            assert report["missing_shadow_events"] == 1
            assert report["topology_comparable"] == 1
            assert report["confidence_band_flips"] == 2
            assert all_truncated is False
            assert report["model_accuracy_evaluated"] is False
            with pytest.raises(Exception):
                reader.execute(text("CREATE TABLE sde_qualification_should_not_exist (id integer)"))
            reader.rollback()
    finally:
        creator.rollback()
        for org_id in reversed(org_ids):
            _cleanup(creator, org_id)
        creator.close()
