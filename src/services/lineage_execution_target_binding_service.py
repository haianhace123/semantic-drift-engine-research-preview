from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.orm import Session

from src.domain.lineage_execution_target import (
    LineageExecutionTargetBinding,
    LineageExecutionTargetIdentity,
)
from src.models.lineage_execution_target_binding import (
    LineageExecutionTargetBindingRecord,
)
from src.services.lineage_authority_lock_service import (
    acquire_lineage_target_authority_lock,
)


class LineageExecutionTargetBindingConflictError(RuntimeError):
    """Raised when one immutable binding revision has conflicting facts."""


class LineageExecutionTargetBindingRevisionGapError(RuntimeError):
    """Raised when publication skips a binding revision."""


@dataclass(frozen=True)
class PersistedLineageExecutionTargetBinding:
    record: LineageExecutionTargetBindingRecord
    created: bool


def persist_lineage_execution_target_binding(
    db: Session,
    *,
    organization_id: uuid.UUID,
    binding: LineageExecutionTargetBinding,
) -> PersistedLineageExecutionTargetBinding:
    acquire_lineage_target_authority_lock(
        db,
        organization_id=organization_id,
        source_external_id=binding.identity.source_external_id,
        asset_external_id=binding.identity.asset_external_id,
    )

    existing = _load_exact_binding(
        db,
        organization_id=organization_id,
        identity=binding.identity,
        binding_revision=binding.binding_revision,
    )

    if existing is not None:
        _assert_same_payload(
            existing,
            binding,
        )
        return PersistedLineageExecutionTargetBinding(
            record=existing,
            created=False,
        )

    latest = _load_latest_binding(
        db,
        organization_id=organization_id,
        identity=binding.identity,
    )

    expected_revision = (
        1
        if latest is None
        else int(latest.binding_revision) + 1
    )

    if binding.binding_revision > expected_revision:
        raise LineageExecutionTargetBindingRevisionGapError(
            "Execution target binding revisions must be contiguous; "
            f"expected revision {expected_revision}, received "
            f"{binding.binding_revision}."
        )

    record = LineageExecutionTargetBindingRecord(
        organization_id=organization_id,
        source_external_id=binding.identity.source_external_id,
        asset_external_id=binding.identity.asset_external_id,
        binding_revision=binding.binding_revision,
        provider=binding.provider,
        target_kind=binding.target_kind,
        configuration=dict(binding.configuration),
    )

    db.add(record)
    db.flush()

    return PersistedLineageExecutionTargetBinding(
        record=record,
        created=True,
    )


def load_current_lineage_execution_target_binding(
    db: Session,
    *,
    organization_id: uuid.UUID,
    identity: LineageExecutionTargetIdentity,
) -> LineageExecutionTargetBindingRecord | None:
    return _load_latest_binding(
        db,
        organization_id=organization_id,
        identity=identity,
    )


def load_lineage_execution_target_binding_revision(
    db: Session,
    *,
    organization_id: uuid.UUID,
    identity: LineageExecutionTargetIdentity,
    binding_revision: int,
) -> LineageExecutionTargetBindingRecord | None:
    if binding_revision <= 0:
        raise ValueError(
            "binding_revision must be greater than zero."
        )

    return _load_exact_binding(
        db,
        organization_id=organization_id,
        identity=identity,
        binding_revision=binding_revision,
    )


def _load_exact_binding(
    db: Session,
    *,
    organization_id: uuid.UUID,
    identity: LineageExecutionTargetIdentity,
    binding_revision: int,
) -> LineageExecutionTargetBindingRecord | None:
    return (
        db.query(LineageExecutionTargetBindingRecord)
        .filter(
            LineageExecutionTargetBindingRecord.organization_id
            == organization_id,
            LineageExecutionTargetBindingRecord.source_external_id
            == identity.source_external_id,
            LineageExecutionTargetBindingRecord.asset_external_id
            == identity.asset_external_id,
            LineageExecutionTargetBindingRecord.binding_revision
            == binding_revision,
        )
        .one_or_none()
    )


def _load_latest_binding(
    db: Session,
    *,
    organization_id: uuid.UUID,
    identity: LineageExecutionTargetIdentity,
) -> LineageExecutionTargetBindingRecord | None:
    return (
        db.query(LineageExecutionTargetBindingRecord)
        .filter(
            LineageExecutionTargetBindingRecord.organization_id
            == organization_id,
            LineageExecutionTargetBindingRecord.source_external_id
            == identity.source_external_id,
            LineageExecutionTargetBindingRecord.asset_external_id
            == identity.asset_external_id,
        )
        .order_by(
            LineageExecutionTargetBindingRecord.binding_revision.desc(),
            LineageExecutionTargetBindingRecord.id.desc(),
        )
        .first()
    )


def _assert_same_payload(
    record: LineageExecutionTargetBindingRecord,
    binding: LineageExecutionTargetBinding,
) -> None:
    persisted_payload = (
        record.provider,
        record.target_kind,
        dict(record.configuration),
    )
    incoming_payload = (
        binding.provider,
        binding.target_kind,
        dict(binding.configuration),
    )

    if persisted_payload != incoming_payload:
        raise LineageExecutionTargetBindingConflictError(
            "Conflicting execution target bindings share the same "
            "organization-scoped target revision."
        )