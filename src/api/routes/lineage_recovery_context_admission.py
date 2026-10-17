"""Authenticated HTTP boundary for the narrow recovery-context admission decision.

API-key read permission and organization are server-owned. No caller-supplied
certificate, issuer, DSN, tenant or raw content. The endpoint never calls an
LLM; the consumer must freeze and reuse exactly the bytes whose digest it sent.
Disabled by default. Not a generic multi-chunk or arbitrary-vector-store API.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from src.core.config import settings
from src.core.database import SessionLocal
from src.core.security import require_api_key_permission
from src.models import ApiKey
from src.services.lineage_recovery_context_admission_service import (
    FrozenSuccessorContext,
    decide_recovery_context_admission,
)

router = APIRouter(prefix="/recovery-context-admission", tags=["lineage"])

# Bound even for transfer-encoding: chunked. Reject before JSON parsing.
_MAX_REQUEST_BYTES = 8192


class RecoveryContextAdmissionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source_id: uuid.UUID
    snapshot_id: uuid.UUID
    chunk_external_id: str = Field(min_length=1, max_length=512)
    chunk_revision_external_id: str = Field(min_length=1, max_length=512)
    index_revision_external_id: str = Field(min_length=1, max_length=512)
    frozen_content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RecoveryContextAdmissionResponse(BaseModel):
    outcome: Literal["proceed", "block"]
    reason: Literal[
        "verified_successor", "unsupported", "invalid_context", "denied",
        "not_current", "evidence_unavailable", "verification_failed",
    ]
    snapshot_id: uuid.UUID | None = None
    certificate_id: uuid.UUID | None = None
    chunk_content_sha256: str | None = None
    request_scoped: Literal[True] = True


async def _read_request(request: Request) -> RecoveryContextAdmissionRequest:
    # Don't trust Content-Length: streaming also bounds chunked requests.
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(status_code=415, detail="JSON content type required")
    content = bytearray()
    async for fragment in request.stream():
        if len(content) + len(fragment) > _MAX_REQUEST_BYTES:
            raise HTTPException(status_code=413,
                                detail="Recovery admission body too large")
        content.extend(fragment)
    try:
        return RecoveryContextAdmissionRequest.model_validate_json(bytes(content))
    except (ValidationError, ValueError):
        # No confidential input or Pydantic error body is returned to clients.
        raise HTTPException(status_code=422,
                            detail="Invalid recovery admission request") from None


@router.post(
    "/query", response_model=RecoveryContextAdmissionResponse,
    status_code=status.HTTP_200_OK,
)
async def query_recovery_context_admission(
    request: Request,
    response: Response,
    api_key: ApiKey = Depends(require_api_key_permission("read")),
) -> RecoveryContextAdmissionResponse:
    """Single-chunk, request-scoped decision; not a reusable serving grant."""
    response.headers["Cache-Control"] = "no-store, private"
    body = await _read_request(request)
    # SDE only activates the bounded adapter with an explicit operator-owned
    # ABSOLUTE snapshot-root path; never resolve a path supplied by the caller.
    root_text = settings.SDE_RECOVERY_SNAPSHOT_ROOT
    root = Path(root_text) if root_text else None
    try:
        enabled = bool(
            settings.SDE_RECOVERY_CONTEXT_ADMISSION_ENABLED
            and root is not None and root.is_absolute() and root.is_dir()
        )
    except OSError:
        enabled = False
    if not enabled:
        return RecoveryContextAdmissionResponse(outcome="block", reason="unsupported")
    try:
        decision = decide_recovery_context_admission(
            enabled=True,
            authority_session_factory=SessionLocal,
            snapshot_root=root,
            organization_id=api_key.organization_id,
            authenticated_api_key_id=api_key.id,
            context=FrozenSuccessorContext(
                source_id=body.source_id,
                snapshot_id=body.snapshot_id,
                chunk_external_id=body.chunk_external_id,
                chunk_revision_external_id=body.chunk_revision_external_id,
                index_revision_external_id=body.index_revision_external_id,
                frozen_content_sha256=body.frozen_content_sha256,
            ),
        )
        return RecoveryContextAdmissionResponse(
            outcome=decision.outcome, reason=decision.reason,
            snapshot_id=decision.snapshot_id,
            certificate_id=decision.certificate_id,
            chunk_content_sha256=decision.chunk_content_sha256,
        )
    except Exception:
        # Fatal adapter errors fail closed; no DSN, tenant or raw context leak.
        return RecoveryContextAdmissionResponse(outcome="block", reason="verification_failed")
