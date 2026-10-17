"""Regression for persisted Risk v2 escalation reused as current intelligence.

Run with RUN_DB_INTEGRATION_TESTS=1. Requires the integration PostgreSQL DB.
"""

from datetime import datetime, timedelta, timezone
import uuid

import pytest

from src.core.database import SessionLocal
from src.models.alert import Alert
from src.models.pattern import Pattern
from src.services.alert_execution_service import execute_alert_generation
from src.services.alert_policy_revision_service import bump_alert_policy_revision
from src.services.persisted_pattern_qualification_service import qualified_pattern_ids
from src.services.pipeline_source_discovery_service import (
    discover_alert_reconciliation_source,
    discover_pipeline_source,
)
from src.models.pipeline_checkpoint import PIPELINE_ALERT_GENERATION_V1
from src.services.what_you_missed_evidence_service import _load_patterns

from tests.integration.test_alert_execution_service import (
    _make_organization,
    _make_entity_and_page,
    _make_event,
    _make_pattern,
    _make_risk_assessment,
    _cleanup,
)

pytestmark = pytest.mark.skipif(
    not __import__('os').getenv('RUN_DB_INTEGRATION_TESTS'),
    reason='RUN_DB_INTEGRATION_TESTS is not enabled',
)


def test_historical_unqualified_escalation_is_not_realerted_or_missed_evidence():
    db = SessionLocal()
    organization_id = None
    try:
        organization = _make_organization(db)
        organization_id = organization.id
        entity, page = _make_entity_and_page(
            db, organization_id=organization_id, suffix='pr2',
        )
        base = datetime(2026, 9, 1, tzinfo=timezone.utc)
        assessed_at = base + timedelta(days=3)
        materialized_at = base + timedelta(days=5)
        event_ids = []
        for index in range(6):
            event = _make_event(
                db,
                organization_id=organization_id,
                page=page,
                detected_at=base + timedelta(days=1, minutes=index),
                suffix=f'pr2-{index}',
            )
            if index == 5:
                event.analysis_status = 'pending'
            assessment = _make_risk_assessment(
                db,
                organization_id=organization_id,
                assessed_at=assessed_at,
                created_at=base + timedelta(days=4),
                score=0.10,
            )
            assessment.semantic_drift_event_id = event.id
            event_ids.append(event.id)
        db.flush()

        valid = _make_pattern(
            db,
            organization_id=organization_id,
            entity_id=entity.id,
            detected_at=assessed_at,
            created_at=materialized_at,
        )
        invalid = _make_pattern(
            db,
            organization_id=organization_id,
            entity_id=entity.id,
            detected_at=assessed_at,
            created_at=materialized_at + timedelta(minutes=1),
        )
        for pattern, ids in ((valid, event_ids[:3]), (invalid, event_ids[3:])):
            pattern.pattern_type = 'risk_escalation'
            pattern.event_ids = [str(item) for item in ids]
            pattern.event_count = 3
        db.flush()
        db.commit()

        allowed = qualified_pattern_ids(
            db, organization_id=organization_id,
            patterns=(valid, invalid),
        )
        assert allowed == frozenset({valid.id})
        assert not qualified_pattern_ids(
            db, organization_id=uuid.uuid4(), patterns=(valid, invalid),
        )
        assert {
            item.pattern_id for item in _load_patterns(
                db, organization_id=organization_id,
                missed_event_ids=set(event_ids),
                since=base, until=materialized_at + timedelta(days=1),
            )
        } == {valid.id}

        source = discover_pipeline_source(
            db,
            organization_id=organization_id,
            pipeline_name=PIPELINE_ALERT_GENERATION_V1,
        )
        assert source is not None
        result = execute_alert_generation(db, source=source)
        db.commit()
        assert result.pattern_source_count == 2
        assert result.pattern_candidate_count == 1
        assert {
            row.source_id for row in db.query(Alert).filter(
                Alert.organization_id == organization_id,
                Alert.source_type == 'pattern',
            ).all()
        } == {valid.id}

        bump_alert_policy_revision(db, organization_id=organization_id)
        db.commit()
        reconciliation = discover_alert_reconciliation_source(
            db, organization_id=organization_id,
        )
        assert reconciliation is not None
        retry = execute_alert_generation(db, source=reconciliation)
        db.commit()
        assert retry.alert_created_count == 0
        assert {
            row.source_id for row in db.query(Alert).filter(
                Alert.organization_id == organization_id,
                Alert.source_type == 'pattern',
            ).all()
        } == {valid.id}
        assert db.query(Pattern).filter(
            Pattern.organization_id == organization_id,
        ).count() == 2  # historical evidence preserved
    finally:
        try:
            _cleanup(db, organization_id)
        finally:
            db.close()
