from __future__ import annotations

import hashlib
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.domain.lineage_revision import LINEAGE_EXTERNAL_ID_MAX_LENGTH


_POLICY_AUTHORITY_NAMESPACE = b"sde:lineage:policy-authority:v1"
_TARGET_AUTHORITY_NAMESPACE = b"sde:lineage:target-authority:v1"


class LineageAuthorityLockError(ValueError):
    """Raised when a lineage authority lock identity is invalid."""


def acquire_lineage_policy_authority_lock(
    db: Session,
    *,
    organization_id: uuid.UUID,
    policy_external_id: str,
) -> None:
    normalized_policy_external_id = _normalize_external_id(
        policy_external_id,
        field_name="policy_external_id",
    )

    _acquire_transaction_lock(
        db,
        lock_key=_derive_lock_key(
            namespace=_POLICY_AUTHORITY_NAMESPACE,
            organization_id=organization_id,
            identity_parts=(normalized_policy_external_id,),
        ),
    )


def acquire_lineage_target_authority_lock(
    db: Session,
    *,
    organization_id: uuid.UUID,
    source_external_id: str,
    asset_external_id: str,
) -> None:
    normalized_source_external_id = _normalize_external_id(
        source_external_id,
        field_name="source_external_id",
    )
    normalized_asset_external_id = _normalize_external_id(
        asset_external_id,
        field_name="asset_external_id",
    )

    _acquire_transaction_lock(
        db,
        lock_key=_derive_lock_key(
            namespace=_TARGET_AUTHORITY_NAMESPACE,
            organization_id=organization_id,
            identity_parts=(
                normalized_source_external_id,
                normalized_asset_external_id,
            ),
        ),
    )


def _acquire_transaction_lock(
    db: Session,
    *,
    lock_key: int,
) -> None:
    db.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": lock_key},
    )


def _derive_lock_key(
    *,
    namespace: bytes,
    organization_id: uuid.UUID,
    identity_parts: tuple[str, ...],
) -> int:
    digest = hashlib.sha256()

    _update_digest_part(digest, namespace)
    _update_digest_part(digest, organization_id.bytes)

    for identity_part in identity_parts:
        _update_digest_part(
            digest,
            identity_part.encode("utf-8"),
        )

    return int.from_bytes(
        digest.digest()[:8],
        byteorder="big",
        signed=True,
    )


def _update_digest_part(
    digest,
    value: bytes,
) -> None:
    digest.update(len(value).to_bytes(4, byteorder="big"))
    digest.update(value)


def _normalize_external_id(
    value: str,
    *,
    field_name: str,
) -> str:
    normalized = value.strip()

    if not normalized:
        raise LineageAuthorityLockError(
            f"{field_name} must not be empty."
        )

    if len(normalized) > LINEAGE_EXTERNAL_ID_MAX_LENGTH:
        raise LineageAuthorityLockError(
            f"{field_name} must be at most "
            f"{LINEAGE_EXTERNAL_ID_MAX_LENGTH} characters."
        )

    return normalized
