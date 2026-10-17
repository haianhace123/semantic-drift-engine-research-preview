"""Operator-only bounded tenant snapshot-evidence manifest.

Not an organization data export, not erasure evidence, and not an API.
Do not include raw snapshot content, URLs, paths, vectors or credentials.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from sqlalchemy import MetaData, func, or_, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

MAX_MANIFEST_SNAPSHOTS = 200
_HASH = re.compile(r"[0-9a-fA-F]{64}\Z")


class EvidenceManifestUnavailable(RuntimeError):
    """Reject partial or misleading tenant evidence output."""


def _as_utc_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise EvidenceManifestUnavailable("Snapshot timestamp is not timezone-aware")
    return value.isoformat()


def _snapshot_record(row) -> dict:
    digest = row.content_hash
    if not isinstance(digest, str) or _HASH.fullmatch(digest) is None:
        raise EvidenceManifestUnavailable("Snapshot content digest is invalid")
    if not isinstance(row.snapshot_id, uuid.UUID) or not isinstance(row.page_id, uuid.UUID):
        raise EvidenceManifestUnavailable("Snapshot identity is invalid")
    return {
        "snapshot_id": str(row.snapshot_id),
        "monitored_page_id": str(row.page_id),
        "content_sha256": digest.lower(),
        "crawled_at": _as_utc_timestamp(row.crawled_at),
        "page_type": str(row.page_type),
        "embedding_status": str(row.embedding_status),
    }


def _collect_bounded_manifest(connection, *, organization_id: uuid.UUID,
                              metadata: MetaData, limit: int) -> dict:
    """Build one bounded manifest inside an existing read-only DB snapshot.

    metadata is validated by the production caller. This helper is a pure
    scoped SQL adapter; never reaches raw content or embedded vector columns.
    """
    if not isinstance(organization_id, uuid.UUID):
        raise EvidenceManifestUnavailable("Organization identity is invalid")
    if type(limit) is not int or not 1 <= limit <= MAX_MANIFEST_SNAPSHOTS:
        raise EvidenceManifestUnavailable("Snapshot limit is invalid")
    organizations = metadata.tables["organizations"]
    pages = metadata.tables["monitored_pages"]
    snapshots = metadata.tables["page_snapshots"]

    exists = connection.execute(
        select(organizations.c.id).where(
            organizations.c.id == organization_id,
            organizations.c.is_active.is_(True),
        ).limit(1)
    ).first()
    if exists is None:
        raise EvidenceManifestUnavailable("Active organization not found")

    mismatched = connection.execute(
        select(snapshots.c.id).select_from(
            snapshots.join(pages, snapshots.c.monitored_page_id == pages.c.id)
        ).where(
            or_(snapshots.c.organization_id == organization_id,
                pages.c.organization_id == organization_id),
            snapshots.c.organization_id != pages.c.organization_id,
        ).limit(1)
    ).first()
    if mismatched is not None:
        raise EvidenceManifestUnavailable("Cross-tenant snapshot relationship")

    source_count = connection.execute(
        select(func.count()).select_from(pages).where(pages.c.organization_id == organization_id)
    ).scalar_one()
    snapshot_count = connection.execute(
        select(func.count()).select_from(snapshots).where(
            snapshots.c.organization_id == organization_id)
    ).scalar_one()
    if any(type(v) is not int or v < 0 for v in (source_count, snapshot_count)):
        raise EvidenceManifestUnavailable("Invalid source/snapshot count")

    # Bound SELECT projection. In particular, never SELECT url, raw_content,
    # raw_content_path, embedding or embedding_error, even into process memory.
    rows = connection.execute(
        select(
            snapshots.c.id.label("snapshot_id"),
            snapshots.c.monitored_page_id.label("page_id"),
            snapshots.c.content_hash,
            snapshots.c.crawled_at,
            snapshots.c.embedding_status,
            pages.c.page_type,
        ).select_from(
            snapshots.join(pages, snapshots.c.monitored_page_id == pages.c.id)
        ).where(
            snapshots.c.organization_id == organization_id,
            pages.c.organization_id == organization_id,
        ).order_by(
            snapshots.c.crawled_at.desc().nulls_last(),
            snapshots.c.id.desc(),
        ).limit(limit + 1)
    ).mappings().all()

    if len(rows) != min(snapshot_count, limit + 1):
        # Mismatched totals can indicate orphan references, RLS or unexpected
        # query behavior. Do not silently declare completeness.
        raise EvidenceManifestUnavailable("Snapshot relation coverage inconsistent")
    records = [_snapshot_record(row) for row in rows[:limit]]
    truncated = snapshot_count > limit
    return {
        "schema_version": 1,
        "manifest_type": "operator_tenant_snapshot_evidence",
        "organization_id": str(organization_id),
        "status": "TRUNCATED" if truncated else "COMPLETE_FOR_SNAPSHOT_METADATA_SCOPE_ONLY",
        "source_count": source_count,
        "snapshot_count": snapshot_count,
        "records_returned": len(records),
        "records": records,
        "contains_raw_document_or_vector": False,
        "full_organization_export": False,
        "erasure_attestation": False,
        "excluded_scopes": [
            "raw_snapshot_files", "document_contents", "embeddings",
            "indirect_and_global_tables", "external_rag_consumers",
            "third_party_providers", "backups", "non_snapshot_organization_records",
        ],
    }


def build_operator_snapshot_evidence_manifest(engine: Engine, *,
                                               organization_id: uuid.UUID,
                                               limit: int = MAX_MANIFEST_SNAPSHOTS) -> dict:
    """Read-only repeatable-read operator action, independent of HTTP auth."""
    if not isinstance(organization_id, uuid.UUID):
        raise EvidenceManifestUnavailable("Invalid organization ID")
    if engine.dialect.name != "postgresql":
        raise EvidenceManifestUnavailable("PostgreSQL authority required")

    # Lazy import allows pure SQL/tests to run without production DB packages.
    from src.services.organization_data_inventory_service import (
        _assert_live_schema_authority,
        _runtime_metadata,
        _validated_direct_tables,
    )
    from src.services.tenant_data_ownership_authority import indirect_owner_paths_are_valid
    from src.core.database import Base

    metadata = _runtime_metadata()
    direct = _validated_direct_tables(metadata)
    if not indirect_owner_paths_are_valid(Base.metadata.tables):
        raise EvidenceManifestUnavailable("Indirect owner contract changed")
    for key in ("organizations", "monitored_pages", "page_snapshots"):
        if key not in metadata.tables:
            raise EvidenceManifestUnavailable("Required authority table missing")
    if "page_snapshots" not in direct or "monitored_pages" not in direct:
        raise EvidenceManifestUnavailable("Tenant snapshot authority incomplete")

    with engine.connect() as connection:
        connection.exec_driver_sql(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
        )
        connection.exec_driver_sql("SET LOCAL statement_timeout = '1500ms'")
        connection.exec_driver_sql("SET LOCAL lock_timeout = '750ms'")
        if connection.execute(text("SHOW transaction_read_only")).scalar_one() != "on":
            raise EvidenceManifestUnavailable("Read-only transaction unavailable")
        _assert_live_schema_authority(connection)
        return _collect_bounded_manifest(
            connection, organization_id=organization_id,
            metadata=metadata, limit=limit,
        )
