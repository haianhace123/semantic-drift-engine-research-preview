"""Evidence-only live pilot checkpoints; no network, provider, or database calls."""

from __future__ import annotations

import hashlib
import json
import uuid

from scripts import verify_live_semantic_pilot as pilot

OLD = str(uuid.UUID("00000000-0000-0000-0000-000000000101"))
NEW = str(uuid.UUID("00000000-0000-0000-0000-000000000102"))
EVT = str(uuid.UUID("00000000-0000-0000-0000-000000000103"))
A = "a" * 64
B = "b" * 64


def _evidence(*, changed=True, status="completed", changes=1):
    old = {"id": OLD, "content_hash": A, "artifact_state": "verified", "crawled_at": "2026-10-10T00:00:00Z"}
    new = {"id": NEW, "content_hash": B if changed else A, "artifact_state": "verified", "crawled_at": "2026-10-10T01:00:00Z"}
    event = {
        "id": EVT, "analysis_status": status, "changes_count": changes, "risk_level": "medium",
        "delta": {
            "old_hash": A, "new_hash": B,
            "semantic_analysis": {
                "analysis_status": "success",
                "overall_risk": "medium", "summary": "Policy change observed",
                "changes": [{"category": "policy", "old_meaning": "30 days",
                             "new_meaning": "90 days", "impact": "Retention increases",
                             "risk_level": "medium"}] * changes,
            },
        },
    }
    return {
        "snapshots": [new, old],
        "comparison": {
            "old_snapshot_id": OLD, "new_snapshot_id": NEW,
            "content_changed": changed,
            "event_id": EVT if changed else None,
        },
        "event": event if changed else None,
        "crawl_status": "succeeded", "crawl_failure_code": None,
    }


def test_baseline_requires_verified_snapshot():
    assert pilot.assess_checkpoint(phase="baseline", evidence=None)["reason"] == "source_not_found"
    no_snapshot = _evidence()
    no_snapshot["snapshots"] = []
    assert pilot.assess_checkpoint(phase="baseline", evidence=no_snapshot)["reason"] == "awaiting_baseline"
    evidence = _evidence()
    assert pilot.assess_checkpoint(phase="baseline", evidence=evidence)["outcome"] == "pass"
    evidence["snapshots"][0]["artifact_state"] = "missing"
    assert pilot.assess_checkpoint(phase="baseline", evidence=evidence)["reason"] == "snapshot_artifact_not_verified"


def test_material_pilot_requires_completed_coherent_evidence():
    result = pilot.assess_checkpoint(phase="changed", evidence=_evidence(), baseline_id=OLD, expect="material")
    assert result["outcome"] == "pass"
    assert result["changes_count"] == 1
    assert result["verifier_provider_calls"] is False
    assert result["verifier_database_writes"] is False
    assert "delta" not in result and "summary" not in result


def test_nonmaterial_and_unchanged_are_separate_observations():
    nonmaterial = _evidence(changes=0)
    assert pilot.assess_checkpoint(phase="changed", evidence=nonmaterial, baseline_id=OLD, expect="nonmaterial")["outcome"] == "pass"
    assert pilot.assess_checkpoint(phase="changed", evidence=nonmaterial, baseline_id=OLD, expect="material")["reason"] == "semantic_materiality_expectation_mismatch"
    unchanged = _evidence(changed=False)
    assert pilot.assess_checkpoint(phase="changed", evidence=unchanged, baseline_id=OLD, expect="unchanged")["outcome"] == "pass"
    assert pilot.assess_checkpoint(phase="changed", evidence=unchanged, baseline_id=OLD, expect="material")["reason"] == "expected_content_change_missing"
    assert pilot.assess_checkpoint(phase="changed", evidence=_evidence(), baseline_id=OLD, expect="unchanged")["reason"] == "unexpected_normalized_content_change"


def test_fail_closed_when_baseline_moved_or_event_missing():
    evidence = _evidence()
    evidence["snapshots"] = [evidence["snapshots"][1]]
    assert pilot.assess_checkpoint(phase="changed", evidence=evidence, baseline_id=OLD, expect="material")["reason"] == "awaiting_new_snapshot"
    evidence = _evidence()
    assert pilot.assess_checkpoint(phase="changed", evidence=evidence, baseline_id=str(uuid.uuid4()), expect="material")["reason"] == "baseline_not_direct_predecessor"
    evidence = _evidence()
    evidence["comparison"]["event_id"] = None
    assert pilot.assess_checkpoint(phase="changed", evidence=evidence, baseline_id=OLD, expect="material")["reason"] == "semantic_event_missing"
    evidence = _evidence()
    evidence["event"] = None
    assert pilot.assess_checkpoint(phase="changed", evidence=evidence, baseline_id=OLD, expect="material")["reason"] == "semantic_event_inaccessible"


def test_incomplete_failed_or_corrupt_event_never_passes():
    for status in ("pending", "failed", "processing", "unknown"):
        evidence = _evidence(status=status)
        result = pilot.assess_checkpoint(phase="changed", evidence=evidence, baseline_id=OLD, expect="material")
        assert result["outcome"] == "incomplete"
        assert result["reason"] == "semantic_analysis_not_completed"
    for damage in ("hash", "count", "contract", "delta"):
        evidence = _evidence()
        if damage == "hash":
            evidence["event"]["delta"]["new_hash"] = "x" * 64
        elif damage == "count":
            evidence["event"]["changes_count"] = 2
        elif damage == "contract":
            evidence["event"]["delta"]["semantic_analysis"]["analysis_status"] = "failed"
        else:
            evidence["event"]["delta"] = None
        assert pilot.assess_checkpoint(phase="changed", evidence=evidence, baseline_id=OLD, expect="material")["reason"] == "semantic_evidence_incoherent"


def test_source_artifact_or_diagnostics_mismatch_prevents_pass():
    for damaged in ("old_artifact", "new_artifact", "pair", "hash_state", "event_on_unchanged"):
        evidence = _evidence(changed=damaged != "event_on_unchanged")
        if damaged == "old_artifact":
            evidence["snapshots"][1]["artifact_state"] = "hash_mismatch"
        elif damaged == "new_artifact":
            evidence["snapshots"][0]["artifact_state"] = "too_large"
        elif damaged == "pair":
            evidence["comparison"]["new_snapshot_id"] = str(uuid.uuid4())
        elif damaged == "hash_state":
            evidence["comparison"]["content_changed"] = False
        else:
            evidence["comparison"]["event_id"] = EVT
        result = pilot.assess_checkpoint(phase="changed", evidence=evidence, baseline_id=OLD, expect="unchanged" if damaged == "event_on_unchanged" else "material")
        assert result["outcome"] == "incomplete", damaged


def test_artifact_hash_check_bounded_and_private(tmp_path):
    source = tmp_path / "controlled.txt"
    data = "This policy has a thirty-day retention period."
    source.write_text(data, encoding="utf-8")
    digest = hashlib.sha256(data.encode("utf-8")).hexdigest()
    assert pilot._artifact_state(str(source), digest) == "verified"
    assert pilot._artifact_state(str(source), "0" * 64) == "hash_mismatch"
    assert pilot._artifact_state(str(tmp_path / "missing"), digest) == "missing"
    source.write_bytes(b"x" * (pilot.MAX_ARTIFACT_BYTES + 1))
    assert pilot._artifact_state(str(source), digest) == "too_large"


def test_cli_rejects_invalid_phase_without_database_or_provider_calls(monkeypatch, capsys):
    def forbidden(**kwargs):
        raise AssertionError("database read was not authorized")
    monkeypatch.setattr(pilot, "read_persisted_source", forbidden)
    org, source = str(uuid.uuid4()), str(uuid.uuid4())
    exit_code = pilot.main(["--organization-id", org, "--source-id", source, "--phase", "changed"])
    assert exit_code == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "changed_phase_requires_baseline_and_expectation"
    exit_code = pilot.main(["--organization-id", org, "--source-id", source, "--phase", "baseline", "--expect", "material"])
    assert exit_code == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "baseline_phase_disallows_expectation"


def test_cli_does_not_leak_exception_details(monkeypatch, capsys):
    def error(**kwargs):
        raise RuntimeError("database secret: sensitive credential")
    monkeypatch.setattr(pilot, "read_persisted_source", error)
    exit_code = pilot.main(["--organization-id", str(uuid.uuid4()), "--source-id", str(uuid.uuid4()), "--phase", "baseline"])
    assert exit_code == 3
    output = capsys.readouterr().out
    assert "sensitive credential" not in output
    assert json.loads(output)["reason"] == "attestation_internal_error"
