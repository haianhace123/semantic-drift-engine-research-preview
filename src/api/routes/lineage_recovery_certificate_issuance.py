"""Admin-session issuance of *evidence only* for one pilot-style PGVector successor.

Independent write flag is disabled by default. No caller-provided URL, digest,
certificate, DSN, organization or issuer. A valid session and CSRF are required.
No general indexing, LLM proxy or persistent serving grant is exposed here.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict

from src.api.auth_routes import require_csrf
from src.core.config import settings
from src.core.database import SessionLocal
from src.core.security import require_roles
from src.models import User
from src.services.lineage_successor_recovery_certificate_service import (
    SuccessorCertificateDenied,
    issue_successor_recovery_evidence_certificate,
)
from src.services.lineage_successor_recovery_preflight_service import SuccessorPreflightBlocked
from src.services.lineage_recovery_certificate_source_service import (
    IssuanceSourceUnavailable,
    load_current_recovery_source,
)

router = APIRouter(prefix='/recovery-certificates', tags=['lineage'])
_MAX_REQUEST_BYTES = 1024


class CertificateIssueRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    source_id: uuid.UUID


class CertificateIssueResponse(BaseModel):
    certificate_id: uuid.UUID
    created: bool
    grants_admission: Literal[False] = False
    evidence_only: Literal[True] = True


async def _read_body(request: Request) -> CertificateIssueRequest:
    from pydantic import ValidationError
    if request.headers.get('content-type', '').split(';', 1)[0].strip().lower() != 'application/json':
        raise HTTPException(status_code=415, detail='JSON content type required')
    content = bytearray()
    async for fragment in request.stream():
        if len(content) + len(fragment) > _MAX_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail='Recovery certificate request too large')
        content.extend(fragment)
    try:
        return CertificateIssueRequest.model_validate_json(bytes(content))
    except (ValidationError, ValueError):
        raise HTTPException(status_code=422, detail='Invalid recovery certificate request') from None


@router.post('', response_model=CertificateIssueResponse, status_code=200)
async def issue_recovery_certificate(
    request: Request,
    response: Response,
    user: User = Depends(require_roles('admin')),
) -> CertificateIssueResponse:
    response.headers['Cache-Control'] = 'no-store, private'
    if not settings.SDE_RECOVERY_CERTIFICATE_ISSUANCE_ENABLED:
        raise HTTPException(status_code=404, detail='Not found')
    # Browser session mutating endpoint: protect against cross-site requests.
    require_csrf(request)
    body = await _read_body(request)
    root_text = settings.SDE_RECOVERY_SNAPSHOT_ROOT
    root = Path(root_text) if root_text else None
    try:
        if root is None or not root.is_absolute() or not root.is_dir():
            raise HTTPException(status_code=503, detail='Verification unavailable')
    except OSError:
        raise HTTPException(status_code=503, detail='Verification unavailable') from None
    try:
        source = load_current_recovery_source(
            authority_session_factory=SessionLocal,
            organization_id=user.organization_id,
            source_id=body.source_id,
        )
        issued = issue_successor_recovery_evidence_certificate(
            authority_session_factory=SessionLocal,
            organization_id=user.organization_id,
            source_id=body.source_id,
            expected_source_url=source.url,
            expected_snapshot_id=source.snapshot_id,
            expected_snapshot_sha256=source.snapshot_sha256,
            snapshot_root=root,
            issuer_user_id=user.id,
        )
        if issued.grants_admission is not False:
            raise RuntimeError('Invalid certificate outcome')
    except (IssuanceSourceUnavailable, SuccessorPreflightBlocked, SuccessorCertificateDenied):
        # Never disclose another tenant's source, certificate, or downstream DSN.
        raise HTTPException(status_code=409, detail='Recovery evidence not eligible') from None
    except Exception:
        raise HTTPException(status_code=503, detail='Verification unavailable') from None
    return CertificateIssueResponse(certificate_id=issued.certificate_id, created=issued.created)
