from datetime import datetime, timedelta, timezone

from src.services.lineage_verification_evidence_contract import (
    qualifies_as_new_materialization_evidence,
)


def test_new_revision_after_execution_can_be_considered():
    result_at = datetime(2026, 10, 9, tzinfo=timezone.utc)
    assert qualifies_as_new_materialization_evidence(
        result_recorded_at=result_at,
        observation_created_at=result_at + timedelta(seconds=1),
        invalidated_revision_external_id="rev-1",
        observation_revision_external_id="rev-2",
    )


def test_prior_and_equal_ingestion_times_fail_closed():
    result_at = datetime(2026, 10, 9, tzinfo=timezone.utc)
    for created_at in (result_at, result_at - timedelta(seconds=1), None):
        assert not qualifies_as_new_materialization_evidence(
            result_recorded_at=result_at,
            observation_created_at=created_at,
            invalidated_revision_external_id="rev-1",
            observation_revision_external_id="rev-2",
        )


def test_same_revision_or_missing_result_time_fail_closed():
    result_at = datetime(2026, 10, 9, tzinfo=timezone.utc)
    assert not qualifies_as_new_materialization_evidence(
        result_recorded_at=result_at,
        observation_created_at=result_at + timedelta(seconds=1),
        invalidated_revision_external_id="rev-1",
        observation_revision_external_id="rev-1",
    )
    assert not qualifies_as_new_materialization_evidence(
        result_recorded_at=None,
        observation_created_at=result_at + timedelta(seconds=1),
        invalidated_revision_external_id="rev-1",
        observation_revision_external_id="rev-2",
    )
