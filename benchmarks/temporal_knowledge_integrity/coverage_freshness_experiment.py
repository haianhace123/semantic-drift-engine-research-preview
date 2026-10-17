"""P2.8A: falsifiable *offline* lineage-coverage/freshness protocol.

NOT an SDE admission implementation, a signed attestation, or a way to infer
unknown dependencies.  The upstream manifest and completed event cursor are
hypothetical trusted inputs; the production schema cannot yet supply them.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, replace
from typing import Literal

from src.domain.lineage_observation import (
    LineageObservation,
    LineageRevisionReference,
)


Disposition = Literal["unknown", "not_affected", "affected", "verified"]
Outcome = Literal["allow", "block"]
Reason = Literal[
    "unresolved", "superseded", "unknown", "untrusted_coverage",
    "incomplete_publication", "manifest_mismatch", "untrusted_freshness",
    "unprocessed_source_events", "decision_not_current", "safe_within_window",
]


@dataclass(frozen=True)
class MaterializationWindow:
    """Prospective manifest from a *trusted* synchronous publisher.

    ``expected_chunks`` must originate from a trusted, independently checked
    materialization input, NOT be reconstructed from observed lineage rows.
    This fixture cannot establish whether the publisher knew every upstream
    source; a missing edge omitted everywhere is unobservable by definition.
    """

    organization_id: uuid.UUID
    source_external_id: str
    snapshot_external_id: str
    index_asset_external_id: str
    index_revision_external_id: str
    sequence: int
    expected_chunks: tuple[LineageRevisionReference, ...]
    physical_ack_ids: tuple[str, ...]
    persisted_observations: tuple[LineageObservation, ...]
    publisher_committed: bool
    input_manifest_trusted: bool


@dataclass(frozen=True)
class SourceProcessingWindow:
    organization_id: uuid.UUID
    source_external_id: str
    known_event_cursor: int
    fully_processed_cursor: int
    no_action_decision_cursor: int
    cursor_authority_trusted: bool
    processing_completion_committed: bool


@dataclass(frozen=True)
class AdmissionRequest:
    organization_id: uuid.UUID
    source_external_id: str
    index_asset_external_id: str
    index_revision_external_id: str
    existing_status: Disposition


@dataclass(frozen=True)
class WindowAdmission:
    outcome: Outcome
    reason: Reason


def _exact_manifest(window: MaterializationWindow) -> bool:
    """Reject partial physical publication or inconsistent lineage rows.

    Set equivalence is insufficient: duplicate ACKs and duplicate persisted
    chunks are rejected even if the set appears complete.
    """
    expected = window.expected_chunks
    if not expected or window.sequence < 0:
        return False
    if any(d.dependency_type != "indexed_from" for d in expected):
        return False
    expected_keys = tuple((d.asset_external_id, d.revision_external_id) for d in expected)
    if len(set(expected_keys)) != len(expected_keys):
        return False
    ids = tuple(d.asset_external_id for d in expected)
    if len(set(ids)) != len(ids):
        return False
    if len(window.physical_ack_ids) != len(ids) or set(window.physical_ack_ids) != set(ids):
        return False

    observations = window.persisted_observations
    chunks = tuple(o for o in observations if o.observation_kind == "materialization" and o.asset_kind == "chunk")
    indexes = tuple(o for o in observations if o.observation_kind == "materialization" and o.asset_kind == "index")
    if len(chunks) != len(ids) or len(indexes) != 1 or len(observations) != len(chunks) + 1:
        return False
    for observation in observations:
        if (observation.source_external_id != window.source_external_id
                or observation.snapshot_external_id != window.snapshot_external_id
                or observation.sequence != window.sequence):
            return False
    index = indexes[0]
    if (index.asset_external_id != window.index_asset_external_id
            or index.revision_external_id != window.index_revision_external_id):
        return False
    if set(index.dependencies) != set(expected) or len(index.dependencies) != len(expected):
        return False
    observed_keys = tuple((c.asset_external_id, c.revision_external_id) for c in chunks)
    return len(set(observed_keys)) == len(expected) and set(observed_keys) == set(expected_keys)


def strict_window_admission(
    request: AdmissionRequest,
    materialization: MaterializationWindow | None,
    progress: SourceProcessingWindow | None,
) -> WindowAdmission:
    """Model an enforceable *scoped* window, NOT global freshness.

    ``not_affected`` alone cannot imply safety; its decision must cover the
    authoritative latest relevant source event and be durably completed.
    """
    if request.existing_status == "affected":
        return WindowAdmission("block", "unresolved")
    if request.existing_status == "verified":
        return WindowAdmission("block", "superseded")
    if request.existing_status == "unknown":
        return WindowAdmission("block", "unknown")

    if (materialization is None or not materialization.input_manifest_trusted):
        return WindowAdmission("block", "untrusted_coverage")
    if not materialization.publisher_committed:
        return WindowAdmission("block", "incomplete_publication")
    if (
        request.organization_id != materialization.organization_id
        or request.source_external_id != materialization.source_external_id
        or request.index_asset_external_id != materialization.index_asset_external_id
        or request.index_revision_external_id != materialization.index_revision_external_id
    ):
        return WindowAdmission("block", "untrusted_coverage")
    if not _exact_manifest(materialization):
        return WindowAdmission("block", "manifest_mismatch")

    if (progress is None or not progress.cursor_authority_trusted or not progress.processing_completion_committed):
        return WindowAdmission("block", "untrusted_freshness")
    if (progress.organization_id != request.organization_id
            or progress.source_external_id != request.source_external_id):
        return WindowAdmission("block", "untrusted_freshness")
    if any(x < 0 for x in (progress.known_event_cursor, progress.fully_processed_cursor,
                             progress.no_action_decision_cursor)):
        return WindowAdmission("block", "untrusted_freshness")
    if progress.fully_processed_cursor > progress.known_event_cursor:
        return WindowAdmission("block", "untrusted_freshness")
    if progress.no_action_decision_cursor > progress.fully_processed_cursor:
        return WindowAdmission("block", "untrusted_freshness")
    if progress.fully_processed_cursor < progress.known_event_cursor:
        return WindowAdmission("block", "unprocessed_source_events")
    if progress.no_action_decision_cursor < progress.known_event_cursor:
        return WindowAdmission("block", "decision_not_current")
    return WindowAdmission("allow", "safe_within_window")


def legacy_status_admission(status: Disposition) -> WindowAdmission:
    if status == "affected":
        return WindowAdmission("block", "unresolved")
    if status == "verified":
        return WindowAdmission("block", "superseded")
    if status == "unknown":
        return WindowAdmission("block", "unknown")
    return WindowAdmission("allow", "safe_within_window")


def demo_proof() -> tuple[AdmissionRequest, MaterializationWindow, SourceProcessingWindow]:
    """Construct real SDE lineage domain objects for one exact revision."""
    org = uuid.UUID("b3a1e111-c3c5-42d2-a12b-bba57ec4ef43")
    source, snapshot, index, index_rev = "publisher/refunds", "snap-7", "refund-index", "index-7"
    refs = (
        LineageRevisionReference("chunk-refunds", "chunk-refunds:aaaa", "indexed_from"),
        LineageRevisionReference("chunk-exceptions", "chunk-exceptions:bbbb", "indexed_from"),
    )
    chunks = tuple(
        LineageObservation(
            observation_kind="materialization", source_external_id=source,
            snapshot_external_id=snapshot, asset_external_id=r.asset_external_id,
            asset_kind="chunk", revision_external_id=r.revision_external_id,
            sequence=7,
        ) for r in refs
    )
    index_observation = LineageObservation(
        observation_kind="materialization", source_external_id=source,
        snapshot_external_id=snapshot, asset_external_id=index,
        asset_kind="index", revision_external_id=index_rev, sequence=7,
        dependencies=refs,
    )
    return (
        AdmissionRequest(org, source, index, index_rev, "not_affected"),
        MaterializationWindow(
            org, source, snapshot, index, index_rev, 7, refs,
            tuple(d.asset_external_id for d in refs), (*chunks, index_observation),
            publisher_committed=True, input_manifest_trusted=True,
        ),
        SourceProcessingWindow(org, source, 7, 7, 7, True, True),
    )


@dataclass(frozen=True)
class DiagnosticCase:
    name: str
    truth: Literal["safe", "stale"]
    request: AdmissionRequest
    materialization: MaterializationWindow | None
    progress: SourceProcessingWindow | None


def diagnostic_cases() -> tuple[DiagnosticCase, ...]:
    request, proof, progress = demo_proof()
    index = proof.persisted_observations[-1]
    first_chunk = proof.persisted_observations[0]
    return (
        DiagnosticCase("complete_exact_revision", "safe", request, proof, progress),
        DiagnosticCase("new_source_event_fully_processed", "safe", request, proof,
                       replace(progress, known_event_cursor=8, fully_processed_cursor=8, no_action_decision_cursor=8)),
        DiagnosticCase("unrelated_source_event", "safe", request, proof, progress),
        DiagnosticCase("missing_chunk_observation", "stale", request,
                       replace(proof, persisted_observations=(first_chunk, index)), progress),
        DiagnosticCase("partial_physical_ack", "stale", request,
                       replace(proof, physical_ack_ids=("chunk-refunds",)), progress),
        DiagnosticCase("partial_remote_publication", "stale", request,
                       replace(proof, publisher_committed=False), progress),
        DiagnosticCase("new_unprocessed_event", "stale", request, proof,
                       replace(progress, known_event_cursor=8)),
        DiagnosticCase("old_no_action_after_event", "stale", request, proof,
                       replace(progress, known_event_cursor=8, fully_processed_cursor=8)),
        DiagnosticCase("unobserved_upstream_omitted_by_producer", "stale", request, proof, progress),
        DiagnosticCase("already_affected", "stale", replace(request, existing_status="affected"), proof, progress),
        DiagnosticCase("superseded_revision", "stale", replace(request, existing_status="verified"), proof, progress),
        DiagnosticCase("unknown_integrity", "stale", replace(request, existing_status="unknown"), proof, progress),
    )


def run_experiment() -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for case in diagnostic_cases():
        legacy = legacy_status_admission(case.request.existing_status)
        strict = strict_window_admission(case.request, case.materialization, case.progress)
        rows.append({
            "case": case.name,
            "truth": case.truth,
            "legacy": legacy.outcome,
            "window": strict.outcome,
            "window_reason": strict.reason,
        })

    def measure(strategy: str) -> dict[str, int]:
        return {
            "stale_allowed": sum(r["truth"] == "stale" and r[strategy] == "allow" for r in rows),
            "safe_blocked": sum(r["truth"] == "safe" and r[strategy] == "block" for r in rows),
        }

    return {
        "mode": "offline_synthetic_negative_controls_not_production_accuracy",
        "cases": rows,
        "legacy_counts": measure("legacy"),
        "candidate_window_counts": measure("window"),
        "unobservable_world_pair": ["complete_exact_revision", "unobserved_upstream_omitted_by_producer"],
        "security_conclusion": (
            "An omitted dependency absent from both publisher input and SDE observations "
            "is indistinguishable from a genuinely complete graph. Input provenance "
            "and trusted source/event authority are prerequisites to any safety claim."
        ),
    }


if __name__ == "__main__":
    print(json.dumps(run_experiment(), sort_keys=True, indent=2))
