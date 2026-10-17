from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.domain.lineage_invalidation import (
    LineageInvalidationDecision,
)
from src.domain.lineage_revision import (
    LineageRevisionIdentity,
)
from src.models.lineage_invalidation_decision import (
    LineageInvalidationDecisionPathRevisionRecord,
    LineageInvalidationDecisionRecord,
)


class LineageInvalidationDecisionConflictError(RuntimeError):
    """Raised when one immutable decision identity has conflicting facts."""


@dataclass(frozen=True)
class PersistedLineageInvalidationDecision:
    record: LineageInvalidationDecisionRecord
    created: bool


def persist_lineage_invalidation_decision(
    db: Session,
    *,
    organization_id: uuid.UUID,
    decision: LineageInvalidationDecision,
) -> PersistedLineageInvalidationDecision:
    existing = _load_decision(
        db,
        organization_id=organization_id,
        decision=decision,
    )

    if existing is not None:
        _assert_same_payload(
            existing,
            decision,
        )
        return PersistedLineageInvalidationDecision(
            record=existing,
            created=False,
        )

    record = _build_record(
        organization_id=organization_id,
        decision=decision,
    )

    try:
        with db.begin_nested():
            db.add(record)
            db.flush()
    except IntegrityError:
        existing = _load_decision(
            db,
            organization_id=organization_id,
            decision=decision,
        )

        if existing is None:
            raise

        _assert_same_payload(
            existing,
            decision,
        )
        return PersistedLineageInvalidationDecision(
            record=existing,
            created=False,
        )

    return PersistedLineageInvalidationDecision(
        record=record,
        created=True,
    )


def _load_decision(
    db: Session,
    *,
    organization_id: uuid.UUID,
    decision: LineageInvalidationDecision,
) -> LineageInvalidationDecisionRecord | None:
    return (
        db.query(LineageInvalidationDecisionRecord)
        .filter(
            LineageInvalidationDecisionRecord.organization_id
            == organization_id,
            LineageInvalidationDecisionRecord.root_source_external_id
            == decision.root.source_external_id,
            LineageInvalidationDecisionRecord.root_asset_external_id
            == decision.root.asset_external_id,
            LineageInvalidationDecisionRecord.root_revision_external_id
            == decision.root.revision_external_id,
            LineageInvalidationDecisionRecord.target_source_external_id
            == decision.target.source_external_id,
            LineageInvalidationDecisionRecord.target_asset_external_id
            == decision.target.asset_external_id,
            LineageInvalidationDecisionRecord.target_revision_external_id
            == decision.target.revision_external_id,
            LineageInvalidationDecisionRecord.policy_external_id
            == decision.policy.policy_external_id,
            LineageInvalidationDecisionRecord.policy_revision
            == decision.policy.revision,
        )
        .one_or_none()
    )


def _build_record(
    *,
    organization_id: uuid.UUID,
    decision: LineageInvalidationDecision,
) -> LineageInvalidationDecisionRecord:
    record = LineageInvalidationDecisionRecord(
        organization_id=organization_id,
        root_source_external_id=decision.root.source_external_id,
        root_asset_external_id=decision.root.asset_external_id,
        root_revision_external_id=decision.root.revision_external_id,
        target_source_external_id=decision.target.source_external_id,
        target_asset_external_id=decision.target.asset_external_id,
        target_revision_external_id=decision.target.revision_external_id,
        policy_external_id=decision.policy.policy_external_id,
        policy_revision=decision.policy.revision,
        disposition=decision.disposition,
        reason_code=decision.reason_code,
    )

    record.causal_path = [
        LineageInvalidationDecisionPathRevisionRecord(
            ordinal=ordinal,
            source_external_id=revision.source_external_id,
            asset_external_id=revision.asset_external_id,
            revision_external_id=revision.revision_external_id,
        )
        for ordinal, revision in enumerate(
            decision.causal_path
        )
    ]

    return record


def _assert_same_payload(
    record: LineageInvalidationDecisionRecord,
    decision: LineageInvalidationDecision,
) -> None:
    persisted_payload = (
        record.disposition,
        record.reason_code,
        _persisted_causal_path(record),
    )
    incoming_payload = (
        decision.disposition,
        decision.reason_code,
        decision.causal_path,
    )

    if persisted_payload != incoming_payload:
        raise LineageInvalidationDecisionConflictError(
            "Conflicting lineage invalidation decisions share "
            "the same organization-scoped decision identity."
        )


def _persisted_causal_path(
    record: LineageInvalidationDecisionRecord,
) -> tuple[LineageRevisionIdentity, ...]:
    return tuple(
        LineageRevisionIdentity(
            source_external_id=path_revision.source_external_id,
            asset_external_id=path_revision.asset_external_id,
            revision_external_id=path_revision.revision_external_id,
        )
        for path_revision in sorted(
            record.causal_path,
            key=lambda path_revision: path_revision.ordinal,
        )
    )