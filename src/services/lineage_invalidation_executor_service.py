from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
import uuid

from sqlalchemy.orm import Session

from src.domain.lineage_execution_target import (
    LineageExecutionTargetIdentity,
)
from src.models.lineage_invalidation_execution_attempt import (
    LineageInvalidationExecutionAttemptRecord,
)
from src.models.lineage_invalidation_execution_intent import (
    LineageInvalidationExecutionIntentRecord,
)
from src.services.lineage_execution_target_binding_service import (
    load_lineage_execution_target_binding_revision,
)
from src.services.lineage_invalidation_projection_service import (
    load_lineage_invalidation_decision_by_record_id,
)
from src.services.lineage_invalidation_execution_target_guard import (
    LineageInvalidationExecutionTargetCausalMismatchError,
    resolve_causal_chunk_target_configuration,
)
from src.services.lineage_pgvector_invalidation_service import (
    LANGCHAIN_PGVECTOR_CAUSAL_CHUNK_TARGET_KIND,
    LANGCHAIN_PGVECTOR_DOCUMENT_TARGET_KIND,
    PGVECTOR_PROVIDER,
)


_CONNECTION_REF_CONFIGURATION_KEY = "connection_ref"


@dataclass(frozen=True, slots=True)
class LineageInvalidationExecutionPlan:
    attempt_id: uuid.UUID
    binding_revision: int
    provider: str
    target_kind: str
    connection_ref: str
    target_configuration: Mapping[str, str]


class LineageInvalidationExecutorError(RuntimeError):
    """Base error for lineage invalidation execution planning failures."""


class LineageInvalidationExecutorAttemptNotFoundError(
    LineageInvalidationExecutorError
):
    """Raised when the requested execution attempt is unavailable."""


class LineageInvalidationExecutorAuthorityNotFoundError(
    LineageInvalidationExecutorError
):
    """Raised when immutable execution authority cannot be reconstructed."""


class LineageInvalidationExecutorBindingNotFoundError(
    LineageInvalidationExecutorError
):
    """Raised when the attempt's pinned execution binding is unavailable."""


class LineageInvalidationExecutorUnsupportedTargetError(
    LineageInvalidationExecutorError
):
    """Raised when the pinned target has no supported executor."""


class LineageInvalidationExecutorCausalMismatchError(
    LineageInvalidationExecutorError
):
    """Raised when a pinned physical revision is outside causal authority."""


def resolve_lineage_invalidation_execution_plan(
    db: Session,
    *,
    organization_id: uuid.UUID,
    attempt_id: uuid.UUID,
) -> LineageInvalidationExecutionPlan:
    """
    Resolve the exact immutable physical target pinned by one execution attempt.

    This function reads SDE execution authority only. It does not claim work,
    perform an external side effect, publish an execution result, or commit the
    caller transaction.
    """
    attempt = (
        db.query(LineageInvalidationExecutionAttemptRecord)
        .filter(
            LineageInvalidationExecutionAttemptRecord.organization_id
            == organization_id,
            LineageInvalidationExecutionAttemptRecord.id == attempt_id,
        )
        .one_or_none()
    )

    if attempt is None:
        raise LineageInvalidationExecutorAttemptNotFoundError(
            "Execution attempt was not found."
        )

    intent = (
        db.query(LineageInvalidationExecutionIntentRecord)
        .filter(
            LineageInvalidationExecutionIntentRecord.organization_id
            == organization_id,
            LineageInvalidationExecutionIntentRecord.id == attempt.intent_id,
        )
        .one_or_none()
    )

    if intent is None:
        raise LineageInvalidationExecutorAuthorityNotFoundError(
            "Execution attempt intent was not found."
        )

    persisted = load_lineage_invalidation_decision_by_record_id(
        db,
        organization_id=organization_id,
        record_id=intent.decision_id,
    )

    if persisted is None:
        raise LineageInvalidationExecutorAuthorityNotFoundError(
            "Execution attempt decision was not found."
        )

    decision = persisted.decision
    target_identity = LineageExecutionTargetIdentity(
        source_external_id=decision.target.source_external_id,
        asset_external_id=decision.target.asset_external_id,
    )

    binding = load_lineage_execution_target_binding_revision(
        db,
        organization_id=organization_id,
        identity=target_identity,
        binding_revision=attempt.binding_revision,
    )

    if binding is None:
        raise LineageInvalidationExecutorBindingNotFoundError(
            "Pinned execution target binding was not found."
        )

    # Historical attempts stay pinned to their original binding revision.
    # Upgrading the target must not make an old, unauthenticated row executable.
    if (
        binding.provider != PGVECTOR_PROVIDER
        or binding.target_kind != LANGCHAIN_PGVECTOR_CAUSAL_CHUNK_TARGET_KIND
    ):
        raise LineageInvalidationExecutorUnsupportedTargetError(
            "Pinned execution target has no causally verified physical identity."
        )

    try:
        causal_configuration = resolve_causal_chunk_target_configuration(
            db,
            organization_id=organization_id,
            decision=decision,
            provider=binding.provider,
            target_kind=binding.target_kind,
            configuration=binding.configuration,
        )
    except LineageInvalidationExecutionTargetCausalMismatchError as exc:
        raise LineageInvalidationExecutorCausalMismatchError(str(exc)) from exc

    configuration = dict(binding.configuration)
    connection_ref = configuration.pop(
        _CONNECTION_REF_CONFIGURATION_KEY,
        None,
    )

    if not isinstance(connection_ref, str) or not connection_ref.strip():
        raise LineageInvalidationExecutorBindingNotFoundError(
            "Pinned execution target binding has no downstream "
            "connection reference."
        )

    return LineageInvalidationExecutionPlan(
        attempt_id=attempt.id,
        binding_revision=attempt.binding_revision,
        provider=binding.provider,
        target_kind=binding.target_kind,
        connection_ref=connection_ref.strip(),
        target_configuration=causal_configuration,
    )
