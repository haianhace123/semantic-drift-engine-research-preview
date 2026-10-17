from __future__ import annotations

import argparse
import json
import sys
import uuid
from typing import Any

from src.core.database import SessionLocal
from src.domain.lineage_execution_target import (
    LineageExecutionTargetBinding,
    LineageExecutionTargetIdentity,
)
from src.domain.lineage_observation import (
    LineageObservation,
    LineageRevisionReference,
)
from src.models.entity import Entity
from src.models.monitored_page import MonitoredPage
from src.models.organization import Organization
from src.models.page_snapshot import PageSnapshot
from src.models.semantic_drift_event import (
    SemanticDriftEvent,
    VECTOR_ANALYSIS_STATUS_COMPLETED,
)
from src.services.lineage_execution_target_binding_service import (
    persist_lineage_execution_target_binding,
)
from src.services.lineage_invalidation_execution_cycle_service import (
    LineageInvalidationExecutionCycleStatus,
    run_lineage_invalidation_execution_cycle,
)
from src.services.lineage_invalidation_execution_intent_service import (
    authorize_lineage_invalidation_execution,
)
from src.services.lineage_invalidation_policy_activation_service import (
    activate_lineage_invalidation_policy,
)
from src.services.lineage_invalidation_policy_service import (
    DEFAULT_LINEAGE_INVALIDATION_POLICY,
)
from src.services.lineage_observation_persistence_service import (
    persist_lineage_observation,
)
from src.services.temporal_lineage_correlation_service import (
    persist_temporal_lineage_correlation,
)
from src.services.temporal_lineage_invalidation_service import (
    analyze_temporal_lineage_invalidation,
)


SOURCE_EXTERNAL_ID = "external-rag-policy-source"
SNAPSHOT_V1_EXTERNAL_ID = "external-rag-snapshot-v1"

REFUND_ASSET_EXTERNAL_ID = "external-rag-chunk-refund"
REFUND_V1_REVISION_EXTERNAL_ID = (
    "external-rag-chunk-refund@v1"
)

INDEX_EXTERNAL_ID = "external-rag-support-index"
INDEX_REVISION_EXTERNAL_ID = (
    "external-rag-support-index@v1"
)

COLLECTION_NAME = "sde_10b4_repair_proof"
DOCUMENT_ID = "external-rag-chunk-refund"
CONNECTION_REF = "external-rag-proof"


class TemporalLineageRepairProofError(RuntimeError):
    pass


def _create_organization(db) -> Organization:
    suffix = uuid.uuid4().hex[:12]

    organization = Organization(
        name=f"temporal-repair-proof-{suffix}",
        slug=f"temporal-repair-proof-{suffix}",
        billing_email=f"{suffix}@example.test",
        subscription_tier="free",
        quota_urls=10,
        quota_requests=1000,
        is_active=True,
    )
    db.add(organization)
    db.flush()

    return organization


def _create_page(
    db,
    *,
    organization: Organization,
) -> MonitoredPage:
    suffix = uuid.uuid4().hex

    entity = Entity(
        id=uuid.uuid4(),
        organization_id=organization.id,
        name=f"Temporal Repair {suffix[:8]}",
        domain=f"{suffix}.example.test",
        category="test",
    )
    db.add(entity)
    db.flush()

    page = MonitoredPage(
        id=uuid.uuid4(),
        organization_id=organization.id,
        entity_id=entity.id,
        url=f"https://example.test/{suffix}",
        page_type="docs",
        crawl_frequency="daily",
        is_active=True,
    )
    db.add(page)
    db.flush()

    return page


def _create_snapshot(
    db,
    *,
    organization: Organization,
    page: MonitoredPage,
    raw_content: str,
) -> PageSnapshot:
    snapshot = PageSnapshot(
        id=uuid.uuid4(),
        monitored_page_id=page.id,
        organization_id=organization.id,
        content_hash=uuid.uuid4().hex,
        raw_content=raw_content,
        content_size=len(raw_content),
    )
    db.add(snapshot)
    db.flush()

    return snapshot


def _refund_observation() -> LineageObservation:
    return LineageObservation(
        observation_kind="materialization",
        source_external_id=SOURCE_EXTERNAL_ID,
        snapshot_external_id=SNAPSHOT_V1_EXTERNAL_ID,
        asset_external_id=REFUND_ASSET_EXTERNAL_ID,
        asset_kind="chunk",
        revision_external_id=(
            REFUND_V1_REVISION_EXTERNAL_ID
        ),
        sequence=1,
    )


def _index_observation() -> LineageObservation:
    return LineageObservation(
        observation_kind="materialization",
        source_external_id=SOURCE_EXTERNAL_ID,
        snapshot_external_id=SNAPSHOT_V1_EXTERNAL_ID,
        asset_external_id=INDEX_EXTERNAL_ID,
        asset_kind="index",
        revision_external_id=INDEX_REVISION_EXTERNAL_ID,
        sequence=1,
        dependencies=(
            LineageRevisionReference(
                asset_external_id=(
                    REFUND_ASSET_EXTERNAL_ID
                ),
                revision_external_id=(
                    REFUND_V1_REVISION_EXTERNAL_ID
                ),
                dependency_type="indexed_from",
            ),
        ),
    )


def _target_binding() -> LineageExecutionTargetBinding:
    return LineageExecutionTargetBinding(
        identity=LineageExecutionTargetIdentity(
            source_external_id=SOURCE_EXTERNAL_ID,
            asset_external_id=INDEX_EXTERNAL_ID,
        ),
        binding_revision=1,
        provider="pgvector",
        target_kind="langchain_pgvector_document_revision",
        configuration={
            "connection_ref": CONNECTION_REF,
            "collection_name": COLLECTION_NAME,
            "document_id": DOCUMENT_ID,
            "document_revision_external_id": (
                REFUND_V1_REVISION_EXTERNAL_ID
            ),
        },
    )


def prepare() -> dict[str, Any]:
    with SessionLocal() as db:
        organization = _create_organization(db)

        page = _create_page(
            db,
            organization=organization,
        )

        old_snapshot = _create_snapshot(
            db,
            organization=organization,
            page=page,
            raw_content="Refund period is 30 days.",
        )
        new_snapshot = _create_snapshot(
            db,
            organization=organization,
            page=page,
            raw_content="Refund period is 14 days.",
        )

        root = persist_lineage_observation(
            db,
            organization_id=organization.id,
            observation=_refund_observation(),
        )

        persist_lineage_observation(
            db,
            organization_id=organization.id,
            observation=_index_observation(),
        )

        persist_temporal_lineage_correlation(
            db,
            organization_id=organization.id,
            page_snapshot_id=old_snapshot.id,
            lineage_observation_id=root.record.id,
        )

        event = SemanticDriftEvent(
            id=uuid.uuid4(),
            monitored_page_id=page.id,
            organization_id=organization.id,
            old_snapshot_id=old_snapshot.id,
            new_snapshot_id=new_snapshot.id,
            semantic_delta_json={
                "changes": [
                    {
                        "before": "30 days",
                        "after": "14 days",
                    }
                ]
            },
            analysis_status="completed",
            vector_analysis_status=(
                VECTOR_ANALYSIS_STATUS_COMPLETED
            ),
            risk_level="medium",
            confidence=0.95,
            impact_score=50,
            changes_count=1,
        )
        db.add(event)
        db.commit()

        organization_id = organization.id
        event_id = event.id

    with SessionLocal() as db:
        analysis = analyze_temporal_lineage_invalidation(
            db,
            organization_id=organization_id,
            event_id=event_id,
            policy_identity=(
                DEFAULT_LINEAGE_INVALIDATION_POLICY
            ),
        )

        if len(analysis.roots) != 1:
            raise TemporalLineageRepairProofError(
                "Expected exactly one temporal lineage root."
            )

        if len(analysis.analyses) != 1:
            raise TemporalLineageRepairProofError(
                "Expected exactly one lineage analysis."
            )

        lineage_analysis = analysis.analyses[0]

        decisions = [
            persisted
            for persisted in lineage_analysis.decisions
            if (
                persisted.record.disposition
                == "invalidate"
            )
        ]

        if len(decisions) != 1:
            raise TemporalLineageRepairProofError(
                "Expected exactly one actionable "
                "invalidation decision."
            )

        decision_record = decisions[0].record

        if (
            decision_record.target_asset_external_id
            != INDEX_EXTERNAL_ID
            or decision_record.target_revision_external_id
            != INDEX_REVISION_EXTERNAL_ID
        ):
            raise TemporalLineageRepairProofError(
                "Temporal invalidation selected an "
                "unexpected target."
            )

        activate_lineage_invalidation_policy(
            db,
            organization_id=organization_id,
            policy=DEFAULT_LINEAGE_INVALIDATION_POLICY,
        )

        persist_lineage_execution_target_binding(
            db,
            organization_id=organization_id,
            binding=_target_binding(),
        )

        authority = authorize_lineage_invalidation_execution(
            db,
            organization_id=organization_id,
            decision_record_id=decision_record.id,
        )

        db.commit()

        return {
            "organization_id": str(organization_id),
            "event_id": str(event_id),
            "old_snapshot_id": str(
                analysis.old_snapshot_id
            ),
            "new_snapshot_id": str(
                analysis.new_snapshot_id
            ),
            "decision_id": str(decision_record.id),
            "intent_id": str(authority.record.id),
            "intent_created": authority.created,
            "target": {
                "source_external_id": (
                    decision_record.target_source_external_id
                ),
                "asset_external_id": (
                    decision_record.target_asset_external_id
                ),
                "revision_external_id": (
                    decision_record.target_revision_external_id
                ),
            },
            "execution_target": {
                "connection_ref": CONNECTION_REF,
                "collection_name": COLLECTION_NAME,
                "document_id": DOCUMENT_ID,
                "document_revision_external_id": (
                    REFUND_V1_REVISION_EXTERNAL_ID
                ),
            },
        }


def execute(
    *,
    organization_id: uuid.UUID,
) -> dict[str, Any]:
    result = run_lineage_invalidation_execution_cycle(
        organization_id=organization_id,
    )

    if (
        result.status
        != LineageInvalidationExecutionCycleStatus.SUCCEEDED
    ):
        raise TemporalLineageRepairProofError(
            "Expected execution cycle to succeed, "
            f"got {result.status!s} "
            f"with reason {result.reason_code!r}."
        )

    if result.attempt_id is None:
        raise TemporalLineageRepairProofError(
            "Successful execution did not return "
            "an attempt id."
        )

    return {
        "organization_id": str(organization_id),
        "status": result.status.value,
        "attempt_id": str(result.attempt_id),
        "intent_id": (
            str(result.candidate.intent_id)
            if result.candidate is not None
            else None
        ),
    }


def execute_fenced(
    *,
    organization_id: uuid.UUID,
) -> dict[str, Any]:
    result = run_lineage_invalidation_execution_cycle(
        organization_id=organization_id,
    )

    if (
        result.status
        != LineageInvalidationExecutionCycleStatus.FAILED
    ):
        raise TemporalLineageRepairProofError(
            "Expected revision-fenced execution cycle to fail, "
            f"got {result.status!s} "
            f"with reason {result.reason_code!r}."
        )

    if result.reason_code != "downstream_execution_failed":
        raise TemporalLineageRepairProofError(
            "Expected revision-fenced execution to fail with "
            "'downstream_execution_failed', "
            f"got {result.reason_code!r}."
        )

    if result.attempt_id is None:
        raise TemporalLineageRepairProofError(
            "Revision-fenced execution did not return an attempt id."
        )

    return {
        "organization_id": str(organization_id),
        "status": result.status.value,
        "reason_code": result.reason_code,
        "attempt_id": str(result.attempt_id),
        "intent_id": (
            str(result.candidate.intent_id)
            if result.candidate is not None
            else None
        ),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Prove temporal lineage invalidation "
            "against the external PGVector fixture."
        )
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    subparsers.add_parser(
        "prepare",
        help=(
            "Create the temporal change, derive the "
            "decision, install authority, and authorize it."
        ),
    )

    execute_parser = subparsers.add_parser(
        "execute",
        help=(
            "Execute one previously authorized "
            "invalidation intent."
        ),
    )
    execute_parser.add_argument(
        "--organization-id",
        required=True,
        type=uuid.UUID,
    )
    execute_fenced_parser = subparsers.add_parser(
        "execute-fenced",
        help=(
            "Execute one authorized invalidation intent and "
            "require the downstream revision fence to reject it."
        ),
    )
    execute_fenced_parser.add_argument(
        "--organization-id",
        required=True,
        type=uuid.UUID,
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    try:
        if args.command == "prepare":
            result = prepare()
        elif args.command == "execute":
            result = execute(
                organization_id=args.organization_id,
            )
        else:
            result = execute_fenced(
                organization_id=args.organization_id,
            )
    except TemporalLineageRepairProofError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        print(
            f"Temporal lineage repair proof failed: {exc}",
            file=sys.stderr,
        )
        return 1

    print(
        json.dumps(
            result,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())