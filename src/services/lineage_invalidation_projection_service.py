from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import tuple_
from sqlalchemy.orm import (
    Session,
    joinedload,
)

from src.domain.lineage_invalidation import (
    LineageInvalidationDecision,
    LineageInvalidationPolicyIdentity,
)
from src.domain.lineage_revision import (
    LineageRevisionIdentity,
)
from src.models.lineage_invalidation_decision import (
    LineageInvalidationDecisionPathRevisionRecord,
    LineageInvalidationDecisionRecord,
)


@dataclass(frozen=True)
class PersistedLineageInvalidationDecisionView:
    record_id: uuid.UUID
    organization_id: uuid.UUID
    decision: LineageInvalidationDecision
    created_at: datetime


def load_lineage_invalidation_decision_by_record_id(
    db: Session,
    *,
    organization_id: uuid.UUID,
    record_id: uuid.UUID,
) -> PersistedLineageInvalidationDecisionView | None:
    record = (
        _decision_query(
            db,
            organization_id=organization_id,
        )
        .filter(
            LineageInvalidationDecisionRecord.id == record_id,
        )
        .one_or_none()
    )

    if record is None:
        return None

    return _to_persisted_view(record)


def load_lineage_invalidation_decision(
    db: Session,
    *,
    organization_id: uuid.UUID,
    root: LineageRevisionIdentity,
    target: LineageRevisionIdentity,
    policy: LineageInvalidationPolicyIdentity,
) -> PersistedLineageInvalidationDecisionView | None:
    record = (
        _decision_query(
            db,
            organization_id=organization_id,
        )
        .filter(
            LineageInvalidationDecisionRecord.root_source_external_id
            == root.source_external_id,
            LineageInvalidationDecisionRecord.root_asset_external_id
            == root.asset_external_id,
            LineageInvalidationDecisionRecord.root_revision_external_id
            == root.revision_external_id,
            LineageInvalidationDecisionRecord.target_source_external_id
            == target.source_external_id,
            LineageInvalidationDecisionRecord.target_asset_external_id
            == target.asset_external_id,
            LineageInvalidationDecisionRecord.target_revision_external_id
            == target.revision_external_id,
            LineageInvalidationDecisionRecord.policy_external_id
            == policy.policy_external_id,
            LineageInvalidationDecisionRecord.policy_revision
            == policy.revision,
        )
        .one_or_none()
    )

    if record is None:
        return None

    return _to_persisted_view(record)


def load_lineage_invalidation_decisions_for_target(
    db: Session,
    *,
    organization_id: uuid.UUID,
    target: LineageRevisionIdentity,
) -> tuple[PersistedLineageInvalidationDecisionView, ...]:
    records = (
        _decision_query(
            db,
            organization_id=organization_id,
        )
        .filter(
            LineageInvalidationDecisionRecord.target_source_external_id
            == target.source_external_id,
            LineageInvalidationDecisionRecord.target_asset_external_id
            == target.asset_external_id,
            LineageInvalidationDecisionRecord.target_revision_external_id
            == target.revision_external_id,
        )
        .order_by(
            LineageInvalidationDecisionRecord.created_at.asc(),
            LineageInvalidationDecisionRecord.id.asc(),
        )
        .all()
    )

    return tuple(
        _to_persisted_view(record)
        for record in records
    )


def load_lineage_invalidation_decisions_for_targets(
    db: Session,
    *,
    organization_id: uuid.UUID,
    targets: tuple[LineageRevisionIdentity, ...],
) -> tuple[PersistedLineageInvalidationDecisionView, ...]:
    if not targets:
        return ()

    target_keys = tuple(
        (
            target.source_external_id,
            target.asset_external_id,
            target.revision_external_id,
        )
        for target in targets
    )
    records = (
        _decision_query(
            db,
            organization_id=organization_id,
        )
        .filter(
            tuple_(
                LineageInvalidationDecisionRecord.target_source_external_id,
                LineageInvalidationDecisionRecord.target_asset_external_id,
                LineageInvalidationDecisionRecord.target_revision_external_id,
            ).in_(target_keys)
        )
        .order_by(
            LineageInvalidationDecisionRecord.target_source_external_id.asc(),
            LineageInvalidationDecisionRecord.target_asset_external_id.asc(),
            LineageInvalidationDecisionRecord.target_revision_external_id.asc(),
            LineageInvalidationDecisionRecord.created_at.asc(),
            LineageInvalidationDecisionRecord.id.asc(),
        )
        .all()
    )

    return tuple(
        _to_persisted_view(record)
        for record in records
    )


def load_lineage_invalidation_decisions_for_root(
    db: Session,
    *,
    organization_id: uuid.UUID,
    root: LineageRevisionIdentity,
) -> tuple[PersistedLineageInvalidationDecisionView, ...]:
    records = (
        _decision_query(
            db,
            organization_id=organization_id,
        )
        .filter(
            LineageInvalidationDecisionRecord.root_source_external_id
            == root.source_external_id,
            LineageInvalidationDecisionRecord.root_asset_external_id
            == root.asset_external_id,
            LineageInvalidationDecisionRecord.root_revision_external_id
            == root.revision_external_id,
        )
        .order_by(
            LineageInvalidationDecisionRecord.created_at.asc(),
            LineageInvalidationDecisionRecord.id.asc(),
        )
        .all()
    )

    return tuple(
        _to_persisted_view(record)
        for record in records
    )


def _decision_query(
    db: Session,
    *,
    organization_id: uuid.UUID,
):
    return (
        db.query(LineageInvalidationDecisionRecord)
        .options(
            joinedload(
                LineageInvalidationDecisionRecord.causal_path
            )
        )
        .filter(
            LineageInvalidationDecisionRecord.organization_id
            == organization_id
        )
    )


def _to_persisted_view(
    record: LineageInvalidationDecisionRecord,
) -> PersistedLineageInvalidationDecisionView:
    root = LineageRevisionIdentity(
        source_external_id=record.root_source_external_id,
        asset_external_id=record.root_asset_external_id,
        revision_external_id=record.root_revision_external_id,
    )
    target = LineageRevisionIdentity(
        source_external_id=record.target_source_external_id,
        asset_external_id=record.target_asset_external_id,
        revision_external_id=record.target_revision_external_id,
    )

    causal_path = tuple(
        _to_revision_identity(path_revision)
        for path_revision in sorted(
            record.causal_path,
            key=lambda path_revision: path_revision.ordinal,
        )
    )

    decision = LineageInvalidationDecision(
        root=root,
        target=target,
        policy=LineageInvalidationPolicyIdentity(
            policy_external_id=record.policy_external_id,
            revision=int(record.policy_revision),
        ),
        disposition=record.disposition,
        reason_code=record.reason_code,
        causal_path=causal_path,
    )

    return PersistedLineageInvalidationDecisionView(
        record_id=record.id,
        organization_id=record.organization_id,
        decision=decision,
        created_at=record.created_at,
    )


def _to_revision_identity(
    record: LineageInvalidationDecisionPathRevisionRecord,
) -> LineageRevisionIdentity:
    return LineageRevisionIdentity(
        source_external_id=record.source_external_id,
        asset_external_id=record.asset_external_id,
        revision_external_id=record.revision_external_id,
    )