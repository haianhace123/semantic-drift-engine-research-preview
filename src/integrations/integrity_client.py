from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

import httpx

from src.domain.lineage_observation import LineageObservation


IntegrityStatus = Literal[
    "unknown",
    "not_affected",
    "affected",
    "verified",
]
EvidenceState = Literal[
    "no_action",
    "unresolved",
    "verified",
]
Disposition = Literal[
    "no_action",
    "invalidate",
    "rematerialize",
]


class SDEIntegrityClientError(RuntimeError):
    """Base error for integrity client failures."""


class SDEIntegrityTransportError(SDEIntegrityClientError):
    """The SDE service could not be reached or completed the request."""


class SDEIntegrityHTTPError(SDEIntegrityClientError):
    """The SDE service returned a non-success HTTP response."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(
            f"SDE integrity request returned HTTP {status_code}."
        )


class SDEIntegrityProtocolError(SDEIntegrityClientError):
    """The SDE service returned a response that violated the integrity protocol."""


@dataclass(frozen=True, slots=True)
class IntegrityRevision:
    source_external_id: str
    asset_external_id: str
    revision_external_id: str

    def as_payload(self) -> dict[str, str]:
        return {
            "source_external_id": self.source_external_id,
            "asset_external_id": self.asset_external_id,
            "revision_external_id": self.revision_external_id,
        }


@dataclass(frozen=True, slots=True)
class LineageObservationPublication:
    id: uuid.UUID
    created: bool


@dataclass(frozen=True, slots=True)
class CausalPgVectorBindingPublication:
    id: uuid.UUID
    source_external_id: str
    index_external_id: str
    binding_revision: int
    created: bool


@dataclass(frozen=True, slots=True)
class LineageExecutionVerificationPublication:
    id: uuid.UUID
    execution_result_id: uuid.UUID
    observation_id: uuid.UUID
    created: bool


@dataclass(frozen=True, slots=True)
class LineageRecoveryPublication:
    observation: LineageObservationPublication
    verification: LineageExecutionVerificationPublication


@dataclass(frozen=True, slots=True)
class IntegrityEvidence:
    decision_id: str
    root: IntegrityRevision
    disposition: Disposition
    reason_code: str
    state: EvidenceState


@dataclass(frozen=True, slots=True)
class TargetIntegrityAssessment:
    target: IntegrityRevision
    status: IntegrityStatus
    evidence: tuple[IntegrityEvidence, ...]


@dataclass(frozen=True, slots=True)
class ContextIntegritySummary:
    target_count: int
    unknown_count: int
    not_affected_count: int
    affected_count: int
    verified_count: int
    has_unknown: bool
    has_unresolved_impact: bool


@dataclass(frozen=True, slots=True)
class ContextIntegrityAssessment:
    assessments: tuple[TargetIntegrityAssessment, ...]
    summary: ContextIntegritySummary


class SDEIntegrityClient:
    """Small synchronous client for SDE runtime integrity assessments."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        normalized_base_url = base_url.strip().rstrip("/")
        if not normalized_base_url:
            raise ValueError("SDE base URL must not be empty.")
        normalized_api_key = api_key.strip()
        if not normalized_api_key:
            raise ValueError("SDE API key must not be empty.")
        if timeout_seconds <= 0:
            raise ValueError("SDE timeout must be greater than zero.")

        self._client = httpx.Client(
            base_url=normalized_base_url,
            headers={"X-API-Key": normalized_api_key},
            timeout=timeout_seconds,
            transport=transport,
        )

    def __enter__(self) -> SDEIntegrityClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def publish_lineage_observation(
        self,
        observation: LineageObservation,
    ) -> LineageObservationPublication:
        payload = self._post_json(
            "/api/lineage/observations",
            {
                "observation_kind": observation.observation_kind,
                "source_external_id": observation.source_external_id,
                "snapshot_external_id": observation.snapshot_external_id,
                "asset_external_id": observation.asset_external_id,
                "asset_kind": observation.asset_kind,
                "revision_external_id": observation.revision_external_id,
                "sequence": observation.sequence,
                "dependencies": [
                    {
                        "asset_external_id": dependency.asset_external_id,
                        "revision_external_id": dependency.revision_external_id,
                        "dependency_type": dependency.dependency_type,
                    }
                    for dependency in observation.dependencies
                ],
            },
        )

        raw_id = payload.get("id")
        if not isinstance(raw_id, str):
            raise SDEIntegrityProtocolError(
                "SDE lineage observation response id must be a UUID string."
            )
        try:
            publication_id = uuid.UUID(raw_id)
        except ValueError as exc:
            raise SDEIntegrityProtocolError(
                "SDE lineage observation response id must be a valid UUID."
            ) from exc

        created = payload.get("created")
        if type(created) is not bool:
            raise SDEIntegrityProtocolError(
                "SDE lineage observation response created must be a boolean."
            )

        return LineageObservationPublication(
            id=publication_id,
            created=created,
        )

    def publish_causal_pgvector_binding(
        self,
        *,
        source_external_id: str,
        index_external_id: str,
        binding_revision: int,
        connection_ref: str,
        collection_name: str,
    ) -> CausalPgVectorBindingPublication:
        """Register a collection selector through the authenticated SDE API.

        Physical chunk IDs/revisions and downstream credentials are forbidden.
        The API key selects the organization; this does not authorize deletion.
        """
        identifiers = {
            "source_external_id": source_external_id,
            "index_external_id": index_external_id,
            "connection_ref": connection_ref,
            "collection_name": collection_name,
        }
        normalized: dict[str, str] = {}
        for key, value in identifiers.items():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{key} must be a non-empty string.")
            normalized[key] = value.strip()
        if type(binding_revision) is not int or binding_revision <= 0:
            raise ValueError("binding_revision must be a positive integer.")
        # Registry selectors must never contain raw DSNs, tokens or credentials.
        if any(char in normalized["connection_ref"] for char in ("://", "@", "=", " ")):
            raise ValueError("connection_ref must be a registry key, not a DSN.")

        configuration = {
            "connection_ref": normalized["connection_ref"],
            "collection_name": normalized["collection_name"],
        }
        payload = self._post_json(
            "/api/lineage/execution-target-bindings",
            {
                "source_external_id": normalized["source_external_id"],
                "asset_external_id": normalized["index_external_id"],
                "binding_revision": binding_revision,
                "provider": "pgvector",
                "target_kind": "langchain_pgvector_causal_chunk",
                "configuration": configuration,
            },
        )
        result_id = _uuid(payload, "id", label="causal PGVector binding")
        created = _boolean(payload, "created")
        expected_echo = {
            "source_external_id": normalized["source_external_id"],
            "asset_external_id": normalized["index_external_id"],
            "binding_revision": binding_revision,
            "provider": "pgvector",
            "target_kind": "langchain_pgvector_causal_chunk",
            "configuration": configuration,
        }
        if any(
            type(payload.get(key)) is not type(value) or payload.get(key) != value
            for key, value in expected_echo.items()
        ):
            raise SDEIntegrityProtocolError(
                "SDE causal PGVector binding response did not match the request."
            )
        return CausalPgVectorBindingPublication(
            id=result_id,
            source_external_id=normalized["source_external_id"],
            index_external_id=normalized["index_external_id"],
            binding_revision=binding_revision,
            created=created,
        )

    def publish_lineage_execution_verification(
        self,
        *,
        execution_result_id: uuid.UUID,
        observation_id: uuid.UUID,
    ) -> LineageExecutionVerificationPublication:
        payload = self._post_json(
            "/api/lineage/invalidation-execution-verifications",
            {
                "execution_result_id": str(execution_result_id),
                "observation_id": str(observation_id),
            },
        )

        publication_id = _uuid(
            payload,
            "id",
            label="lineage execution verification",
        )
        response_execution_result_id = _uuid(
            payload,
            "execution_result_id",
            label="lineage execution verification",
        )
        response_observation_id = _uuid(
            payload,
            "observation_id",
            label="lineage execution verification",
        )
        created = _boolean(payload, "created")

        if response_execution_result_id != execution_result_id:
            raise SDEIntegrityProtocolError(
                "SDE lineage execution verification response did not match "
                "the requested execution result."
            )
        if response_observation_id != observation_id:
            raise SDEIntegrityProtocolError(
                "SDE lineage execution verification response did not match "
                "the requested observation."
            )

        return LineageExecutionVerificationPublication(
            id=publication_id,
            execution_result_id=response_execution_result_id,
            observation_id=response_observation_id,
            created=created,
        )

    def publish_lineage_recovery(
        self,
        *,
        execution_result_id: uuid.UUID,
        observation: LineageObservation,
    ) -> LineageRecoveryPublication:
        if observation.observation_kind != "materialization":
            raise ValueError(
                "Lineage recovery evidence must be a materialization observation."
            )

        observation_publication = self.publish_lineage_observation(observation)
        verification_publication = self.publish_lineage_execution_verification(
            execution_result_id=execution_result_id,
            observation_id=observation_publication.id,
        )

        return LineageRecoveryPublication(
            observation=observation_publication,
            verification=verification_publication,
        )

    def query_target_integrity(
        self,
        target: IntegrityRevision,
    ) -> TargetIntegrityAssessment:
        payload = self._post_json(
            "/api/lineage/target-integrity/query",
            target.as_payload(),
        )
        assessment = _parse_target_assessment(payload)
        if assessment.target != target:
            raise SDEIntegrityProtocolError(
                "SDE target integrity response did not match the requested revision."
            )
        return assessment

    def query_context_integrity(
        self,
        targets: Sequence[IntegrityRevision],
    ) -> ContextIntegrityAssessment:
        if not targets:
            raise ValueError("Context integrity query requires at least one target.")
        if len(targets) > 64:
            raise ValueError("Context integrity query supports at most 64 targets.")

        requested_targets = tuple(targets)
        payload = self._post_json(
            "/api/lineage/context-integrity/query",
            {"targets": [target.as_payload() for target in requested_targets]},
        )
        assessment = _parse_context_assessment(payload)
        validate_context_integrity_assessment(
            assessment, requested_targets=requested_targets,
        )
        return assessment

    def _post_json(
        self,
        path: str,
        payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        try:
            response = self._client.post(path, json=payload)
        except httpx.RequestError as exc:
            raise SDEIntegrityTransportError(
                "SDE integrity request failed before a valid response was received."
            ) from exc

        if not response.is_success:
            raise SDEIntegrityHTTPError(response.status_code)

        try:
            body = response.json()
        except ValueError as exc:
            raise SDEIntegrityProtocolError(
                "SDE integrity response was not valid JSON."
            ) from exc

        if not isinstance(body, Mapping):
            raise SDEIntegrityProtocolError(
                "SDE integrity response must be a JSON object."
            )
        return body


def _parse_revision(value: object) -> IntegrityRevision:
    body = _mapping(value, "revision")
    return IntegrityRevision(
        source_external_id=_string(body, "source_external_id"),
        asset_external_id=_string(body, "asset_external_id"),
        revision_external_id=_string(body, "revision_external_id"),
    )


def _parse_target_assessment(
    value: object,
) -> TargetIntegrityAssessment:
    body = _mapping(value, "target assessment")
    status = _string(body, "status")
    if status not in {"unknown", "not_affected", "affected", "verified"}:
        raise SDEIntegrityProtocolError("SDE returned an unknown integrity status.")

    raw_evidence = body.get("evidence")
    if not isinstance(raw_evidence, list):
        raise SDEIntegrityProtocolError("SDE integrity evidence must be a list.")

    evidence: list[IntegrityEvidence] = []
    for raw_item in raw_evidence:
        item = _mapping(raw_item, "integrity evidence")
        disposition = _string(item, "disposition")
        state = _string(item, "state")
        if disposition not in {"no_action", "invalidate", "rematerialize"}:
            raise SDEIntegrityProtocolError("SDE returned an unknown disposition.")
        if state not in {"no_action", "unresolved", "verified"}:
            raise SDEIntegrityProtocolError("SDE returned an unknown evidence state.")
        evidence.append(
            IntegrityEvidence(
                decision_id=_string(item, "decision_id"),
                root=_parse_revision(item.get("root")),
                disposition=disposition,
                reason_code=_string(item, "reason_code"),
                state=state,
            )
        )

    return TargetIntegrityAssessment(
        target=_parse_revision(body.get("target")),
        status=status,
        evidence=tuple(evidence),
    )


def validate_context_integrity_assessment(
    assessment: ContextIntegrityAssessment,
    *,
    requested_targets: Sequence[IntegrityRevision],
) -> None:
    """Validate any integrity client response before a serving decision.

    The HTTP client checks this invariant too, but integrations also accept
    custom client implementations. A clean result for the wrong revision or
    an incomplete response must never be interpreted as permission to serve.
    """
    if not isinstance(assessment, ContextIntegrityAssessment):
        raise SDEIntegrityProtocolError(
            "SDE context integrity client returned an invalid assessment."
        )
    if not requested_targets:
        raise SDEIntegrityProtocolError(
            "SDE context integrity assessment requires requested revisions."
        )
    _validate_context_summary(assessment)
    response_targets = tuple(item.target for item in assessment.assessments)
    if response_targets != tuple(requested_targets):
        raise SDEIntegrityProtocolError(
            "SDE context integrity response did not match the requested revisions."
        )
    if assessment.summary.target_count != len(requested_targets):
        raise SDEIntegrityProtocolError(
            "SDE context integrity summary target count was inconsistent."
        )


def _validate_context_summary(
    assessment: ContextIntegrityAssessment,
) -> None:
    counts = {
        "unknown": 0,
        "not_affected": 0,
        "affected": 0,
        "verified": 0,
    }
    for item in assessment.assessments:
        counts[item.status] += 1

    summary = assessment.summary
    if summary.target_count != len(assessment.assessments):
        raise SDEIntegrityProtocolError(
            "SDE context integrity summary target count was inconsistent."
        )

    if (
        summary.unknown_count != counts["unknown"]
        or summary.not_affected_count != counts["not_affected"]
        or summary.affected_count != counts["affected"]
        or summary.verified_count != counts["verified"]
    ):
        raise SDEIntegrityProtocolError(
            "SDE context integrity summary status counts were inconsistent."
        )

    if summary.has_unknown != (summary.unknown_count > 0):
        raise SDEIntegrityProtocolError(
            "SDE context integrity summary unknown flag was inconsistent."
        )

    if summary.has_unresolved_impact != (summary.affected_count > 0):
        raise SDEIntegrityProtocolError(
            "SDE context integrity summary unresolved-impact flag was inconsistent."
        )


def _parse_context_assessment(value: object) -> ContextIntegrityAssessment:
    body = _mapping(value, "context assessment")
    raw_assessments = body.get("assessments")
    if not isinstance(raw_assessments, list):
        raise SDEIntegrityProtocolError("SDE context assessments must be a list.")
    summary = _mapping(body.get("summary"), "context summary")

    return ContextIntegrityAssessment(
        assessments=tuple(
            _parse_target_assessment(item)
            for item in raw_assessments
        ),
        summary=ContextIntegritySummary(
            target_count=_integer(summary, "target_count"),
            unknown_count=_integer(summary, "unknown_count"),
            not_affected_count=_integer(summary, "not_affected_count"),
            affected_count=_integer(summary, "affected_count"),
            verified_count=_integer(summary, "verified_count"),
            has_unknown=_boolean(summary, "has_unknown"),
            has_unresolved_impact=_boolean(summary, "has_unresolved_impact"),
        ),
    )


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise SDEIntegrityProtocolError(f"SDE {label} must be a JSON object.")
    return value


def _string(body: Mapping[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value:
        raise SDEIntegrityProtocolError(
            f"SDE response field '{key}' must be a non-empty string."
        )
    return value


def _integer(body: Mapping[str, Any], key: str) -> int:
    value = body.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SDEIntegrityProtocolError(
            f"SDE response field '{key}' must be a non-negative integer."
        )
    return value


def _uuid(
    body: Mapping[str, Any],
    key: str,
    *,
    label: str,
) -> uuid.UUID:
    value = body.get(key)
    if not isinstance(value, str):
        raise SDEIntegrityProtocolError(
            f"SDE {label} response field '{key}' must be a UUID string."
        )
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise SDEIntegrityProtocolError(
            f"SDE {label} response field '{key}' must be a valid UUID."
        ) from exc


def _boolean(body: Mapping[str, Any], key: str) -> bool:
    value = body.get(key)
    if not isinstance(value, bool):
        raise SDEIntegrityProtocolError(
            f"SDE response field '{key}' must be a boolean."
        )
    return value
