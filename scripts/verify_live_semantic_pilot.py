"""Read-only attestation of a controlled, real-transport source monitoring pilot.

This command never crawls, writes database state, or calls a semantic provider.
A customer-authorized source is enrolled and crawled using SDE's normal runtime;
this command checks durable evidence at the baseline and change checkpoints.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import uuid
from typing import Any

MAX_ARTIFACT_BYTES = 1_048_576


def _artifact_state(path: str | None, expected_sha256: str) -> str:
    """Check saved snapshot bytes without printing or returning document data."""
    if not path:
        return "missing"
    try:
        if not os.path.isfile(path):
            return "missing"
        digest = hashlib.sha256()
        size = 0
        with open(path, "rb") as source:
            while chunk := source.read(65536):
                size += len(chunk)
                if size > MAX_ARTIFACT_BYTES:
                    return "too_large"
                digest.update(chunk)
        return "verified" if digest.hexdigest() == expected_sha256 else "hash_mismatch"
    except OSError:
        return "unreadable"


def read_persisted_source(*, organization_id: uuid.UUID, source_id: uuid.UUID) -> dict[str, Any] | None:
    """Read a single tenant's source and newest pair from one DB snapshot.

    Uses the established source diagnostics authority for the pair/event status.
    Additional persisted fields are needed solely for artifact integrity and
    the safe, evidence-level acceptance criteria below.
    """
    from sqlalchemy import text

    from src.core.database import SessionLocal
    from src.models import MonitoredPage, PageSnapshot, SemanticDriftEvent
    from src.services.source_diagnostics_service import read_source_diagnostics

    with SessionLocal() as db:
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        page = db.query(MonitoredPage).filter(
            MonitoredPage.id == source_id,
            MonitoredPage.organization_id == organization_id,
        ).one_or_none()
        if page is None:
            return None
        diagnostics = read_source_diagnostics(
            db, organization_id=organization_id, monitored_page_id=source_id,
        )
        snapshots = db.query(PageSnapshot).filter(
            PageSnapshot.organization_id == organization_id,
            PageSnapshot.monitored_page_id == source_id,
        ).order_by(PageSnapshot.crawled_at.desc(), PageSnapshot.id.desc()).limit(2).all()
        artifacts = [
            {
                "id": str(snapshot.id),
                "content_hash": snapshot.content_hash,
                "crawled_at": snapshot.crawled_at.isoformat() if snapshot.crawled_at else None,
                "artifact_state": _artifact_state(snapshot.raw_content_path, snapshot.content_hash),
            }
            for snapshot in snapshots
        ]
        event_summary = None
        comparison = diagnostics["latest_comparison"]
        if comparison and comparison["event_id"]:
            event = db.query(SemanticDriftEvent).filter(
                SemanticDriftEvent.id == uuid.UUID(comparison["event_id"]),
                SemanticDriftEvent.organization_id == organization_id,
                SemanticDriftEvent.monitored_page_id == source_id,
                SemanticDriftEvent.old_snapshot_id == uuid.UUID(comparison["old_snapshot_id"]),
                SemanticDriftEvent.new_snapshot_id == uuid.UUID(comparison["new_snapshot_id"]),
            ).one_or_none()
            if event is not None:
                event_summary = {
                    "id": str(event.id),
                    "analysis_status": event.analysis_status,
                    "changes_count": event.changes_count,
                    "risk_level": event.risk_level,
                    "delta": event.semantic_delta_json,
                }
        return {
            "snapshots": artifacts,
            "comparison": comparison,
            "event": event_summary,
            "crawl_status": page.last_crawl_status,
            "crawl_failure_code": page.last_crawl_failure_code,
        }


def assess_checkpoint(
    *, phase: str, evidence: dict[str, Any] | None,
    baseline_id: str | None = None, expect: str | None = None,
) -> dict[str, Any]:
    """Assess pipeline evidence, NOT semantic accuracy against independent labels."""
    result: dict[str, Any] = {
        "phase": phase,
        "outcome": "incomplete",
        "reason": None,
        "verifier_database_writes": False,
        "verifier_http_requests": False,
        "verifier_provider_calls": False,
    }

    def stop(reason: str) -> dict[str, Any]:
        result["reason"] = reason
        return result

    if evidence is None:
        return stop("source_not_found")
    result["crawl_status"] = evidence.get("crawl_status")
    result["crawl_failure_code"] = evidence.get("crawl_failure_code")
    snapshots = evidence.get("snapshots") or []
    if not snapshots:
        return stop("awaiting_baseline")
    latest = snapshots[0]
    result["latest_snapshot_id"] = latest["id"]
    result["latest_crawled_at"] = latest.get("crawled_at")
    if latest.get("artifact_state") != "verified":
        return stop("snapshot_artifact_not_verified")
    if phase == "baseline":
        result["outcome"] = "pass"
        result["reason"] = "baseline_ready"
        return result
    if phase != "changed" or baseline_id is None or expect not in {
        "material", "nonmaterial", "unchanged",
    }:
        return stop("invalid_checkpoint_contract")
    if latest["id"] == baseline_id:
        return stop("awaiting_new_snapshot")
    if len(snapshots) != 2 or snapshots[1]["id"] != baseline_id:
        return stop("baseline_not_direct_predecessor")
    older = snapshots[1]
    if older.get("artifact_state") != "verified":
        return stop("snapshot_artifact_not_verified")
    comparison = evidence.get("comparison")
    if not isinstance(comparison, dict) or (
        comparison.get("old_snapshot_id") != baseline_id
        or comparison.get("new_snapshot_id") != latest["id"]
    ):
        return stop("diagnostics_pair_mismatch")
    changed = older["content_hash"] != latest["content_hash"]
    if changed is not comparison.get("content_changed"):
        return stop("diagnostics_hash_mismatch")
    result["content_changed"] = changed
    result["baseline_snapshot_id"] = baseline_id
    if not changed:
        if comparison.get("event_id") is not None:
            return stop("unexpected_semantic_event_for_identical_content")
        if expect != "unchanged":
            return stop("expected_content_change_missing")
        result["outcome"] = "pass"
        result["reason"] = "unchanged_content_observed"
        return result
    if expect == "unchanged":
        return stop("unexpected_normalized_content_change")
    if comparison.get("event_id") is None:
        return stop("semantic_event_missing")
    event = evidence.get("event")
    if not isinstance(event, dict) or event.get("id") != comparison.get("event_id"):
        return stop("semantic_event_inaccessible")
    result["event_id"] = event["id"]
    result["analysis_status"] = event.get("analysis_status")
    if event.get("analysis_status") != "completed":
        return stop("semantic_analysis_not_completed")
    delta = event.get("delta")
    semantic = delta.get("semantic_analysis") if isinstance(delta, dict) else None
    changes = semantic.get("changes") if isinstance(semantic, dict) else None
    if (
        not isinstance(changes, list)
        or semantic.get("analysis_status") != "success"
        or not isinstance(delta.get("old_hash"), str)
        or delta["old_hash"] != older["content_hash"]
        or delta.get("new_hash") != latest["content_hash"]
        or event.get("changes_count") != len(changes)
        or semantic.get("overall_risk") not in {"high", "medium", "low"}
        or event.get("risk_level") != semantic.get("overall_risk")
        or not isinstance(semantic.get("summary"), str)
        or any(
            not isinstance(change, dict)
            or not all(
                isinstance(change.get(field), str) and change[field].strip()
                for field in ("category", "old_meaning", "new_meaning", "impact", "risk_level")
            )
            for change in changes
        )
    ):
        return stop("semantic_evidence_incoherent")
    result["changes_count"] = len(changes)
    result["reported_risk_level"] = event.get("risk_level")
    if (expect == "material") != bool(changes):
        return stop("semantic_materiality_expectation_mismatch")
    result["outcome"] = "pass"
    result["reason"] = "completed_semantic_evidence_observed"
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--organization-id", type=uuid.UUID, required=True)
    parser.add_argument("--source-id", type=uuid.UUID, required=True)
    parser.add_argument("--phase", choices=("baseline", "changed"), required=True)
    parser.add_argument("--baseline-snapshot-id", type=uuid.UUID)
    parser.add_argument("--expect", choices=("material", "nonmaterial", "unchanged"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.phase == "changed" and (args.baseline_snapshot_id is None or args.expect is None):
        print(json.dumps({"outcome": "incomplete", "reason": "changed_phase_requires_baseline_and_expectation"}))
        return 2
    if args.phase == "baseline" and (args.baseline_snapshot_id is not None or args.expect is not None):
        print(json.dumps({"outcome": "incomplete", "reason": "baseline_phase_disallows_expectation"}))
        return 2
    try:
        evidence = read_persisted_source(
            organization_id=args.organization_id, source_id=args.source_id,
        )
        result = assess_checkpoint(
            phase=args.phase, evidence=evidence,
            baseline_id=str(args.baseline_snapshot_id) if args.baseline_snapshot_id else None,
            expect=args.expect,
        )
        print(json.dumps(result, sort_keys=True))
        return 0 if result["outcome"] == "pass" else 2
    except Exception:
        # The report must not disclose credentials, SQL, document data or
        # upstream error messages even when the database is unavailable.
        print(json.dumps({"outcome": "incomplete", "reason": "attestation_internal_error"}))
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
