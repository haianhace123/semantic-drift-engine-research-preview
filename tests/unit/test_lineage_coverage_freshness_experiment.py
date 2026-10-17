"""P2.8A negative controls for proposed certificate semantics, not live admission."""

from dataclasses import replace
import uuid

import pytest

from benchmarks.temporal_knowledge_integrity.coverage_freshness_experiment import (
    demo_proof, diagnostic_cases, legacy_status_admission, run_experiment,
    strict_window_admission,
)
from src.domain.lineage_observation import LineageObservation, LineageRevisionReference


def test_complete_materialization_and_current_event_cursor_admit():
    request, proof, progress = demo_proof()
    assert strict_window_admission(request, proof, progress).outcome == "allow"


@pytest.mark.parametrize("case_name,reason", [
    ("missing_chunk_observation", "manifest_mismatch"),
    ("partial_physical_ack", "manifest_mismatch"),
    ("partial_remote_publication", "incomplete_publication"),
    ("new_unprocessed_event", "unprocessed_source_events"),
    ("old_no_action_after_event", "decision_not_current"),
])
def test_known_partial_publication_or_new_event_denied(case_name, reason):
    case = next(c for c in diagnostic_cases() if c.name == case_name)
    result = strict_window_admission(case.request, case.materialization, case.progress)
    assert (result.outcome, result.reason) == ("block", reason)
    assert legacy_status_admission(case.request.existing_status).outcome == "allow"


def test_unobservable_missing_edge_cannot_be_deduced_from_a_self_reported_manifest():
    worlds = diagnostic_cases()
    safe = next(c for c in worlds if c.name == "complete_exact_revision")
    missing = next(c for c in worlds if c.name == "unobserved_upstream_omitted_by_producer")
    assert safe.truth != missing.truth
    assert (safe.request, safe.materialization, safe.progress) == (
        missing.request, missing.materialization, missing.progress
    )
    assert strict_window_admission(safe.request, safe.materialization, safe.progress).outcome == "allow"
    assert strict_window_admission(missing.request, missing.materialization, missing.progress).outcome == "allow"


def test_unrelated_source_event_does_not_block_this_scope():
    case = next(c for c in diagnostic_cases() if c.name == "unrelated_source_event")
    assert strict_window_admission(case.request, case.materialization, case.progress).outcome == "allow"


@pytest.mark.parametrize("status", ["unknown", "affected", "verified"])
def test_existing_fail_closed_statuses_remain_blocked(status):
    request, proof, progress = demo_proof()
    assert strict_window_admission(replace(request, existing_status=status), proof, progress).outcome == "block"


def test_missing_or_untrusted_manifest_blocks_without_explicit_evidence():
    request, proof, progress = demo_proof()
    assert strict_window_admission(request, None, progress).reason == "untrusted_coverage"
    assert strict_window_admission(request, replace(proof, input_manifest_trusted=False), progress).reason == "untrusted_coverage"


def test_missing_or_untrusted_cursor_blocks_without_freshness_evidence():
    request, proof, progress = demo_proof()
    assert strict_window_admission(request, proof, None).reason == "untrusted_freshness"
    assert strict_window_admission(request, proof, replace(progress, cursor_authority_trusted=False)).reason == "untrusted_freshness"
    assert strict_window_admission(request, proof, replace(progress, processing_completion_committed=False)).reason == "untrusted_freshness"


@pytest.mark.parametrize("field", ["organization_id", "source_external_id", "index_asset_external_id", "index_revision_external_id"])
def test_exact_revision_and_tenant_are_required(field):
    request, proof, progress = demo_proof()
    value = uuid.uuid4() if field == "organization_id" else "wrong"
    result = strict_window_admission(replace(request, **{field: value}), proof, progress)
    assert (result.outcome, result.reason) == ("block", "untrusted_coverage")


def test_cursor_mismatch_cross_tenant_and_overclaim_block():
    request, proof, progress = demo_proof()
    assert strict_window_admission(request, proof, replace(progress, organization_id=uuid.uuid4())).reason == "untrusted_freshness"
    assert strict_window_admission(request, proof, replace(progress, source_external_id="other")).reason == "untrusted_freshness"
    assert strict_window_admission(request, proof, replace(progress, fully_processed_cursor=8)).reason == "untrusted_freshness"
    assert strict_window_admission(request, proof, replace(progress, no_action_decision_cursor=8)).reason == "untrusted_freshness"
    assert strict_window_admission(request, proof, replace(progress, known_event_cursor=-1)).reason == "untrusted_freshness"


def test_manifest_duplication_or_unexpected_chunk_block():
    request, proof, progress = demo_proof()
    duplicated_ack = replace(proof, physical_ack_ids=("chunk-refunds", "chunk-refunds"))
    assert strict_window_admission(request, duplicated_ack, progress).reason == "manifest_mismatch"
    duplicated_ref = replace(proof, expected_chunks=(proof.expected_chunks[0], proof.expected_chunks[0]))
    assert strict_window_admission(request, duplicated_ref, progress).reason == "manifest_mismatch"
    altered_ref = replace(proof, expected_chunks=(proof.expected_chunks[0], LineageRevisionReference("chunk-exceptions", "other", "indexed_from")))
    assert strict_window_admission(request, altered_ref, progress).reason == "manifest_mismatch"
    extra_row = replace(proof, persisted_observations=(proof.persisted_observations[0], *proof.persisted_observations))
    assert strict_window_admission(request, extra_row, progress).reason == "manifest_mismatch"


def test_manifest_snapshot_source_and_sequence_consistency():
    request, proof, progress = demo_proof()
    changed = replace(proof.persisted_observations[0], snapshot_external_id="wrong")
    assert strict_window_admission(request, replace(proof, persisted_observations=(changed, *proof.persisted_observations[1:])), progress).reason == "manifest_mismatch"
    changed = replace(proof.persisted_observations[0], sequence=99)
    assert strict_window_admission(request, replace(proof, persisted_observations=(changed, *proof.persisted_observations[1:])), progress).reason == "manifest_mismatch"


def test_experiment_reports_nonzero_residual_risk_and_zero_synthetic_false_blocks():
    result = run_experiment()
    assert result["legacy_counts"] == {"stale_allowed": 6, "safe_blocked": 0}
    assert result["candidate_window_counts"] == {"stale_allowed": 1, "safe_blocked": 0}
    assert len(result["cases"]) == 12
    assert result["mode"] == "offline_synthetic_negative_controls_not_production_accuracy"
