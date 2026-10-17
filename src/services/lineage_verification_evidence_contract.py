from __future__ import annotations

from datetime import datetime


def qualifies_as_new_materialization_evidence(
    *,
    result_recorded_at: datetime | None,
    observation_created_at: datetime | None,
    invalidated_revision_external_id: str,
    observation_revision_external_id: str,
) -> bool:
    """Necessary provenance fences, not proof of a physical repair.

    Observation ingestion must start after authoritative result publication,
    and must report a different target revision. Causal attestation is still
    the responsibility of the source publishing the explicit binding.
    """
    return bool(
        result_recorded_at is not None
        and observation_created_at is not None
        and observation_created_at > result_recorded_at
        and observation_revision_external_id != invalidated_revision_external_id
    )
