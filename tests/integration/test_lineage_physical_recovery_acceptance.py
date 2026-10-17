"""Physical PGVector invalidation followed by producer-attested rematerialization.

This scenario deliberately uses separate downstream and SDE databases. It
qualifies the supported contracts; it does not make SDE the rebuild executor
or turn source-reported lineage into independent physical repair proof.
"""

from __future__ import annotations

import json
import os
import secrets
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from src.core.database import SessionLocal
from src.core.security import token_hash
from src.integrations.integrity_admission import (
    IntegrityAdmissionPolicy,
    evaluate_integrity_admission,
)
from src.integrations.integrity_client import (
    ContextIntegrityAssessment,
    ContextIntegritySummary,
    IntegrityEvidence,
    IntegrityRevision,
    SDEIntegrityClient,
    TargetIntegrityAssessment,
)
from src.domain.lineage_observation import LineageObservation, LineageRevisionReference
from src.domain.lineage_invalidation import (
    LineageInvalidationDecision,
    LineageInvalidationPolicyIdentity,
)
from src.domain.lineage_revision import LineageRevisionIdentity
from src.integrations.langchain.context_admission import (
    LangChainContextNotAdmittedError,
    admit_langchain_context,
)
from experiments.guarded_langchain_consumer import run_guarded_model_call
from src.models import ApiKey
from src.services.lineage_invalidation_persistence_service import (
    persist_lineage_invalidation_decision,
)
from src.models.lineage_invalidation_execution_result import (
    LineageInvalidationExecutionResultRecord,
)
from src.services.lineage_downstream_connection_service import (
    create_lineage_downstream_session,
    resolve_lineage_downstream_connection,
)
from src.services.lineage_invalidation_execution_cycle_service import (
    LineageInvalidationExecutionCycleStatus,
    run_lineage_invalidation_execution_cycle,
)
from src.services.lineage_invalidation_execution_verification_projection_service import (
    load_lineage_invalidation_execution_verification_view,
)
from src.services.lineage_invalidation_execution_verification_service import (
    LineageInvalidationExecutionVerificationResultNotFoundError,
    persist_lineage_invalidation_execution_verification,
)
from src.services.lineage_invalidation_projection_service import (
    load_lineage_invalidation_decision_by_record_id,
)
from src.services.lineage_observation_persistence_service import (
    persist_lineage_observation,
)
from src.services.lineage_target_integrity_service import (
    LineageTargetIntegrityAssessment,
    assess_lineage_target_integrity,
)
from tests.integration.test_lineage_invalidation_execution_lifecycle import (
    DOWNSTREAM_DATABASE_URL,
    _cleanup,
    _cleanup_downstream_collections,
    _ensure_downstream_schema,
    _establish_claimable_intent,
    _insert_collection,
    _insert_document,
    _make_organization,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="Database integration tests are disabled.",
)


def _admission_from_persisted_target(
    result: LineageTargetIntegrityAssessment,
):
    """Use persisted authority evidence, not a fixture-supplied status."""
    status = result.status
    revision = result.target
    return evaluate_integrity_admission(
        ContextIntegrityAssessment(
            assessments=(
                TargetIntegrityAssessment(
                    target=IntegrityRevision(
                        source_external_id=revision.source_external_id,
                        asset_external_id=revision.asset_external_id,
                        revision_external_id=revision.revision_external_id,
                    ),
                    status=status,
                    evidence=tuple(
                        IntegrityEvidence(
                            decision_id=str(item.decision_id),
                            root=IntegrityRevision(
                                source_external_id=item.root.source_external_id,
                                asset_external_id=item.root.asset_external_id,
                                revision_external_id=item.root.revision_external_id,
                            ),
                            disposition=item.disposition,
                            reason_code=item.reason_code,
                            state=item.state,
                        )
                        for item in result.evidence
                    ),
                ),
            ),
            summary=ContextIntegritySummary(
                target_count=1,
                unknown_count=int(status == "unknown"),
                not_affected_count=int(status == "not_affected"),
                affected_count=int(status == "affected"),
                verified_count=int(status == "verified"),
                has_unknown=status == "unknown",
                has_unresolved_impact=status == "affected",
            ),
        ),
        policy=IntegrityAdmissionPolicy(unknown="block"),
    )


def _http_client(db, organization_id: uuid.UUID) -> SDEIntegrityClient:
    """Create a read-only key scoped to a disposable test organization."""
    api_url = os.environ["SDE_LINEAGE_ACCEPTANCE_API_URL"]
    raw_key = f"sk-{secrets.token_urlsafe(32)}"
    db.add(ApiKey(
        organization_id=organization_id,
        name="isolated-http-admission",
        key_hash=token_hash(raw_key),
        permissions={"read": True, "write": False},
        is_active=True,
    ))
    db.commit()
    return SDEIntegrityClient(base_url=api_url, api_key=raw_key, timeout_seconds=5)


def _revision_from_authority(target: LineageRevisionIdentity) -> IntegrityRevision:
    return IntegrityRevision(
        source_external_id=target.source_external_id,
        asset_external_id=target.asset_external_id,
        revision_external_id=target.revision_external_id,
    )


def _downstream_document(row: tuple[str, str, str]):
    source_id, revision, document = row
    return SimpleNamespace(
        id="chunk-a",
        page_content=document,
        metadata={
            "source_external_id": source_id,
            "chunk_revision_external_id": revision,
        },
    )


def _assert_http_block(client, serving, document, reason: str) -> None:
    with pytest.raises(LangChainContextNotAdmittedError) as error:
        admit_langchain_context(
            client=client,
            serving_revision=serving,
            retrieved_documents=[document],
        )
    assert error.value.reason == reason

    # A real, tenant-scoped SDE HTTP refusal must prevent the model boundary,
    # not merely return a decision object while a caller still serves bytes.
    model_prompts: list[str] = []
    with pytest.raises(LangChainContextNotAdmittedError) as guarded_error:
        run_guarded_model_call(
            question="What does the current policy say?",
            retrieve=lambda question: [document],
            client=client,
            serving_revision=serving,
            invoke_model=lambda prompt: model_prompts.append(prompt) or "unexpected-answer",
        )
    assert guarded_error.value.reason == reason
    assert model_prompts == [], "model invoked on unadmitted physical context"


@pytest.mark.parametrize("retains_stale_dependency", [False, True])
def test_real_downstream_rebuild_requires_clean_lineage_attestation(
    monkeypatch: pytest.MonkeyPatch,
    retains_stale_dependency: bool,
) -> None:
    """Physical rebuild alone cannot clear a reported stale dependency."""
    authority = SessionLocal()
    downstream = None
    downstream_engine = None
    organizations: list[uuid.UUID] = []
    collections: list[uuid.UUID] = []
    owner_client = None
    other_client = None

    try:
        monkeypatch.setattr(
            "src.services.lineage_downstream_connection_service."
            "settings.DOWNSTREAM_DATABASE_URLS",
            json.dumps({"test-downstream": DOWNSTREAM_DATABASE_URL}),
        )
        connection = resolve_lineage_downstream_connection(
            connection_ref="test-downstream"
        )
        downstream, downstream_engine = create_lineage_downstream_session(
            connection
        )
        _ensure_downstream_schema(downstream)

        owner = _make_organization(authority)
        other = _make_organization(authority)
        organizations.extend((owner.id, other.id))
        owner_client = _http_client(authority, owner.id)
        other_client = _http_client(authority, other.id)
        collection_id = uuid.uuid4()
        collections.append(collection_id)
        collection_name = f"sde-recovery-acceptance-{uuid.uuid4().hex}"

        _insert_collection(
            downstream,
            collection_id=collection_id,
            collection_name=collection_name,
            organization_id=owner.id,
        )
        _insert_document(
            downstream,
            collection_id=collection_id,
            document_id="chunk-a",
            document_revision_external_id="chunk-a-r3",
        )
        downstream.commit()
        initial_physical = downstream.execute(
            text(
                "SELECT cmetadata->>'source_external_id', "
                "cmetadata->>'chunk_revision_external_id', document "
                "FROM langchain_pg_embedding "
                "WHERE collection_id = :collection_id AND id = 'chunk-a'"
            ),
            {"collection_id": collection_id},
        ).one()
        old_document = _downstream_document(tuple(initial_physical))
        downstream.rollback()
        old_serving = IntegrityRevision(
            source_external_id="source-a",
            asset_external_id="index-a",
            revision_external_id="index-a-r7",
        )
        # No persisted decision is NOT proof of valid knowledge.
        _assert_http_block(owner_client, old_serving, old_document, "unknown_integrity")

        intent = _establish_claimable_intent(
            authority,
            organization_id=owner.id,
            collection_name=collection_name,
            document_id="chunk-a",
        )
        decision_id = intent.decision_id
        authority.commit()

        cycle = run_lineage_invalidation_execution_cycle(
            organization_id=owner.id
        )
        assert cycle.status == LineageInvalidationExecutionCycleStatus.SUCCEEDED
        assert cycle.attempt_id is not None

        downstream.rollback()
        assert downstream.execute(
            text(
                "SELECT count(*) FROM langchain_pg_embedding "
                "WHERE collection_id = :collection_id AND id = 'chunk-a'"
            ),
            {"collection_id": collection_id},
        ).scalar_one() == 0
        downstream.rollback()

        authority.rollback()
        result = (
            authority.query(LineageInvalidationExecutionResultRecord)
            .filter(
                LineageInvalidationExecutionResultRecord.organization_id
                == owner.id,
                LineageInvalidationExecutionResultRecord.attempt_id
                == cycle.attempt_id,
            )
            .one()
        )
        assert result.outcome == "succeeded"
        result_id = result.id
        decision = load_lineage_invalidation_decision_by_record_id(
            authority,
            organization_id=owner.id,
            record_id=decision_id,
        )
        assert decision is not None
        assert load_lineage_invalidation_execution_verification_view(
            authority,
            organization_id=owner.id,
            decision=decision,
            execution_result_id=result_id,
        ).state == "awaiting_evidence"
        affected_target = assess_lineage_target_integrity(
            authority,
            organization_id=owner.id,
            target=decision.decision.target,
        )
        assert affected_target.status == "affected"
        admission = _admission_from_persisted_target(affected_target)
        assert (admission.outcome, admission.reason) == (
            "block", "unresolved_impact",
        )
        authority.rollback()
        assert owner_client.query_target_integrity(old_serving).status == "affected"
        assert other_client.query_target_integrity(old_serving).status == "unknown"
        _assert_http_block(owner_client, old_serving, old_document, "unresolved_impact")
        _assert_http_block(other_client, old_serving, old_document, "unknown_integrity")

        # An independent downstream producer materializes the replacement.
        # SDE's invalidation worker does not perform this write.
        _insert_document(
            downstream,
            collection_id=collection_id,
            document_id="chunk-a",
            document_revision_external_id="chunk-a-r4",
        )
        downstream.execute(
            text(
                "UPDATE langchain_pg_embedding "
                "SET document = 'replacement-content-revision-4' "
                "WHERE collection_id = :collection_id AND id = 'chunk-a'"
            ),
            {"collection_id": collection_id},
        )
        downstream.commit()
        physical = downstream.execute(
            text(
                "SELECT cmetadata->>'source_external_id', "
                "cmetadata->>'chunk_revision_external_id', document "
                "FROM langchain_pg_embedding "
                "WHERE collection_id = :collection_id AND id = 'chunk-a'"
            ),
            {"collection_id": collection_id},
        ).one()
        assert tuple(physical) == (
            "source-a", "chunk-a-r4", "replacement-content-revision-4"
        )
        new_document = _downstream_document(tuple(physical))
        downstream.rollback()
        new_serving = IntegrityRevision(
            source_external_id="source-a",
            asset_external_id="index-a",
            revision_external_id="index-a-r8",
        )
        _assert_http_block(owner_client, new_serving, new_document, "unknown_integrity")

        # Physical replacement without a materialization attestation is
        # insufficient to advance Control Plane verification.
        assert load_lineage_invalidation_execution_verification_view(
            authority,
            organization_id=owner.id,
            decision=decision,
            execution_result_id=result_id,
        ).state == "awaiting_evidence"
        authority.rollback()

        dependencies = [
            LineageRevisionReference(
                asset_external_id="chunk-a",
                revision_external_id="chunk-a-r4",
                dependency_type="indexed_from",
            )
        ]
        if retains_stale_dependency:
            dependencies.append(
                LineageRevisionReference(
                    asset_external_id="chunk-a",
                    revision_external_id="chunk-a-r3",
                    dependency_type="indexed_from",
                )
            )
        observation = persist_lineage_observation(
            authority,
            organization_id=owner.id,
            observation=LineageObservation(
                observation_kind="materialization",
                source_external_id="source-a",
                snapshot_external_id="snapshot-8",
                asset_external_id="index-a",
                asset_kind="index",
                revision_external_id="index-a-r8",
                sequence=8,
                dependencies=tuple(dependencies),
            ),
        )
        authority.commit()

        with pytest.raises(
            LineageInvalidationExecutionVerificationResultNotFoundError
        ):
            persist_lineage_invalidation_execution_verification(
                authority,
                organization_id=other.id,
                execution_result_id=result_id,
                observation_id=observation.record.id,
            )
        authority.rollback()

        binding = persist_lineage_invalidation_execution_verification(
            authority,
            organization_id=owner.id,
            execution_result_id=result_id,
            observation_id=observation.record.id,
        )
        assert binding.created
        authority.commit()
        replay = persist_lineage_invalidation_execution_verification(
            authority,
            organization_id=owner.id,
            execution_result_id=result_id,
            observation_id=observation.record.id,
        )
        assert not replay.created
        assert replay.record.id == binding.record.id
        authority.rollback()

        expected_view = (
            "stale_dependency_present" if retains_stale_dependency else "verified"
        )
        expected_integrity = "affected" if retains_stale_dependency else "verified"
        assert load_lineage_invalidation_execution_verification_view(
            authority,
            organization_id=owner.id,
            decision=decision,
            execution_result_id=result_id,
        ).state == expected_view
        current_target = assess_lineage_target_integrity(
            authority,
            organization_id=owner.id,
            target=decision.decision.target,
        )
        assert current_target.status == expected_integrity
        admission = _admission_from_persisted_target(current_target)
        # Physical repair and a verified new materialization cannot
        # grant serving permission to the invalidated historical target.
        assert (admission.outcome, admission.reason) == (
            "block",
            "unresolved_impact" if retains_stale_dependency else "superseded_revision",
        )
        authority.rollback()
        # Producer attestation may verify repair; it must never re-admit old bytes.
        assert owner_client.query_target_integrity(old_serving).status == expected_integrity
        _assert_http_block(
            owner_client,
            old_serving,
            old_document,
            "unresolved_impact" if retains_stale_dependency else "superseded_revision",
        )
        assert other_client.query_target_integrity(old_serving).status == "unknown"

        # A successor is independently assessed. For this controlled test,
        # a pair of explicitly persisted no-action decisions models that
        # assessment; they are not proof that real source content was checked.
        if not retains_stale_dependency:
            root = LineageRevisionIdentity(
                source_external_id="source-a",
                asset_external_id="policy-root",
                revision_external_id="policy-v2",
            )
            policy = LineageInvalidationPolicyIdentity(
                policy_external_id="isolated-acceptance-policy", revision=8,
            )
            for target in (
                LineageRevisionIdentity("source-a", "index-a", "index-a-r8"),
                LineageRevisionIdentity("source-a", "chunk-a", "chunk-a-r4"),
            ):
                persist_lineage_invalidation_decision(
                    authority,
                    organization_id=owner.id,
                    decision=LineageInvalidationDecision(
                        root=root, target=target, policy=policy,
                        disposition="no_action", reason_code="controlled_acceptance_no_action",
                        causal_path=(root, target),
                    ),
                )
            authority.commit()
            admitted = admit_langchain_context(
                client=owner_client,
                serving_revision=new_serving,
                retrieved_documents=[new_document],
            )
            assert len(admitted.documents) == 1
            assert admitted.documents[0].id == new_document.id
            assert admitted.documents[0].page_content == new_document.page_content
            assert dict(admitted.documents[0].metadata) == dict(new_document.metadata)
            assert admitted.admission.outcome == "proceed"

            # The only permissible model input is the newly materialized,
            # independently admitted row, never the old retrieved document.
            model_prompts: list[str] = []
            guarded = run_guarded_model_call(
                question="What does the current policy say?",
                retrieve=lambda question: [new_document],
                client=owner_client,
                serving_revision=new_serving,
                invoke_model=lambda prompt: model_prompts.append(prompt) or "spy-answer",
            )
            assert guarded.output == "spy-answer"
            assert guarded.admitted_document_count == 1
            assert guarded.admission_reason == "no_unresolved_impact"
            assert len(model_prompts) == 1
            assert new_document.page_content in model_prompts[0]
            assert old_document.page_content not in model_prompts[0]

            _assert_http_block(other_client, new_serving, new_document, "unknown_integrity")
            _assert_http_block(owner_client, old_serving, old_document, "superseded_revision")

        # Replaying the terminal worker must not remove the newly rebuilt row.
        followup = run_lineage_invalidation_execution_cycle(
            organization_id=owner.id
        )
        assert followup.status != LineageInvalidationExecutionCycleStatus.SUCCEEDED
        downstream.rollback()
        persisted_revision = downstream.execute(
            text(
                "SELECT cmetadata->>'chunk_revision_external_id' "
                "FROM langchain_pg_embedding "
                "WHERE collection_id = :collection_id AND id = 'chunk-a'"
            ),
            {"collection_id": collection_id},
        ).scalar_one()
        assert persisted_revision == "chunk-a-r4"
    finally:
        if owner_client is not None:
            owner_client.close()
        if other_client is not None:
            other_client.close()
        authority.rollback()
        authority.close()
        if downstream is not None:
            downstream.rollback()
            downstream.close()
        if downstream_engine is not None:
            downstream_engine.dispose()
        _cleanup_downstream_collections(collections)
        _cleanup(organizations)
