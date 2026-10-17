"""Pure validation and batch behavior for stored Pattern evidence."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
import uuid

from src.services.persisted_pattern_qualification_service import (
    _event_ids,
    qualified_pattern_ids,
)


def _pattern(organization_id, *, pattern_type='risk_escalation', event_ids=None):
    return SimpleNamespace(
        id=uuid.uuid4(),
        organization_id=organization_id,
        pattern_type=pattern_type,
        event_ids=event_ids,
        created_at=datetime(2026, 9, 5, tzinfo=timezone.utc),
        detected_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
    )


def test_malformed_or_duplicate_event_lists_fail_closed():
    event_id = uuid.uuid4()
    org = uuid.uuid4()
    assert _event_ids(_pattern(org, event_ids=[])) is None
    assert _event_ids(_pattern(org, event_ids=['invalid'])) is None
    assert _event_ids(_pattern(org, event_ids=[str(event_id)] * 2)) is None
    assert _event_ids(_pattern(org, event_ids=[str(event_id)])) == frozenset({event_id})


def test_non_risk_patterns_remain_available_without_risk_assessments():
    organization_id = uuid.uuid4()
    pattern = _pattern(organization_id, pattern_type='repeated_drift')
    foreign = _pattern(uuid.uuid4(), pattern_type='repeated_drift')
    db = MagicMock()
    assert qualified_pattern_ids(
        db, organization_id=organization_id, patterns=(pattern, foreign),
    ) == frozenset({pattern.id})
    db.query.assert_not_called()


def test_risk_escalation_requires_evidence_materialized_before_pattern():
    organization_id = uuid.uuid4()
    event_id = uuid.uuid4()
    pattern = _pattern(organization_id, event_ids=[str(event_id)])
    db = MagicMock()
    query = db.query.return_value
    query.join.return_value = query
    query.filter.return_value = query
    rows = query.all
    rows.return_value = [
        (event_id, datetime(2026, 9, 6, tzinfo=timezone.utc),
         datetime(2026, 9, 2, tzinfo=timezone.utc)),
    ]
    assert not qualified_pattern_ids(
        db, organization_id=organization_id, patterns=(pattern,),
    )
    rows.return_value = [
        (event_id, datetime(2026, 9, 4, tzinfo=timezone.utc),
         datetime(2026, 9, 4, tzinfo=timezone.utc)),
    ]
    assert not qualified_pattern_ids(
        db, organization_id=organization_id, patterns=(pattern,),
    )
    rows.return_value = [
        (event_id, datetime(2026, 9, 4, tzinfo=timezone.utc),
         datetime(2026, 9, 2, tzinfo=timezone.utc)),
    ]
    assert qualified_pattern_ids(
        db, organization_id=organization_id, patterns=(pattern,),
    ) == frozenset({pattern.id})
