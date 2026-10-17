"""Read-only, scoped organization inventory. NOT a data export or erasure plan.

The table allowlist reflects reviewed ORM models at the pinned migration boundary.
An unrecognized or unregistered tenant table blocks the inventory rather than
silently implying that all organization data has been enumerated.
"""

from __future__ import annotations

import time
import uuid

from sqlalchemy import MetaData, func, or_, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from src.core.database import Base
from src.services.tenant_data_ownership_authority import (
    KNOWN_INDIRECT_OR_GLOBAL_TABLES,
    indirect_owner_paths_are_valid,
    registered_ownership_metadata,
)



DIRECT_ORGANIZATION_TABLES = frozenset((
    "alert_deliveries", "alerts", "anomalies", "api_keys", "auth_security_events",
    "entities", "entity_graph_metric_snapshots", "entity_relationship_observations",
    "entity_relationships", "investigations", "lineage_execution_target_bindings",
    "lineage_invalidation_decisions", "lineage_invalidation_execution_attempts",
    "lineage_invalidation_execution_intents", "lineage_invalidation_execution_results",
    "lineage_invalidation_execution_verifications", "lineage_invalidation_policy_activations",
    "lineage_observations", "lineage_successor_recovery_certificates",
    "monitored_pages", "organization_alert_policy_revisions", "organization_work_schedules",
    "page_snapshots", "patterns", "pipeline_checkpoints", "risk_assessments",
    "semantic_drift_events", "temporal_lineage_invalidation_decision_provenance",
    "topology_shift_snapshots", "users", "watchlists", "webhook_subscriptions",
))

# Intentionally NOT counted in this first inventory: ownership is indirect or
# global. They must be resolved before any full export or deletion claim.
UNRESOLVED_TABLES = KNOWN_INDIRECT_OR_GLOBAL_TABLES


class OrganizationInventoryUnavailable(RuntimeError):
    """Refuse a misleading partial inventory or unverifiable tenant boundary."""


def _runtime_metadata() -> MetaData:
    return registered_ownership_metadata()


def _validated_direct_tables(metadata: MetaData) -> dict:
    direct = {
        table.name: table
        for table in metadata.tables.values()
        if "organization_id" in table.c
    }
    # A model/schema change must trigger an explicit authority review.
    if set(direct) != DIRECT_ORGANIZATION_TABLES:
        raise OrganizationInventoryUnavailable("Tenant table coverage changed")
    if ("organizations" not in metadata.tables
            or "id" not in metadata.tables["organizations"].c
            or "is_active" not in metadata.tables["organizations"].c):
        raise OrganizationInventoryUnavailable("Organization active-state authority missing")
    for name in ("page_snapshots", "monitored_pages"):
        if "id" not in direct[name].c:
            raise OrganizationInventoryUnavailable("Missing snapshot ownership key")
    snapshot = direct["page_snapshots"]
    if "monitored_page_id" not in snapshot.c or "raw_content_path" not in snapshot.c:
        raise OrganizationInventoryUnavailable("Snapshot schema changed")
    return direct


def _count(db: Session, statement) -> int:
    deadline = db.info.get("sde_inventory_deadline")
    if deadline is not None and time.monotonic() >= deadline:
        raise OrganizationInventoryUnavailable("Inventory time budget exceeded")
    value = db.execute(statement).scalar_one()
    if deadline is not None and time.monotonic() >= deadline:
        raise OrganizationInventoryUnavailable("Inventory time budget exceeded")
    if type(value) is not int or value < 0:
        raise OrganizationInventoryUnavailable("Invalid inventory count")
    return value


def build_organization_data_inventory(
    db: Session,
    organization_id: uuid.UUID,
    *,
    metadata: MetaData | None = None,
) -> dict:
    """Inspect only authenticated tenant records; never read raw content/path values.

    `metadata` is an internal test seam, not an API parameter.
    Database exceptions are sanitized by the caller. No COMMIT, writes, file
    reads, provider access, or external calls occur in this function.
    """
    if not isinstance(organization_id, uuid.UUID):
        raise OrganizationInventoryUnavailable("Invalid authenticated organization")
    direct = _validated_direct_tables(metadata if metadata is not None else _runtime_metadata())
    # The production path must share the same FK/correlation contract as the
    # operator audit. Fixture metadata is intentionally limited to direct rows.
    if metadata is None and not indirect_owner_paths_are_valid(Base.metadata.tables):
        raise OrganizationInventoryUnavailable("Indirect tenant ownership contract changed")

    # Verify that the owning organization still exists; no user-supplied org ID.
    authority = metadata if metadata is not None else Base.metadata
    org = _count(
        db,
        select(func.count()).select_from(authority.tables["organizations"])
        .where(authority.tables["organizations"].c.id == organization_id),
    )
    if org != 1:
        raise OrganizationInventoryUnavailable("Organization ownership unavailable")

    counts = {}
    for name in sorted(DIRECT_ORGANIZATION_TABLES):
        table = direct[name]
        counts[name] = _count(
            db,
            select(func.count()).select_from(table).where(table.c.organization_id == organization_id),
        )

    snapshots = direct["page_snapshots"]
    pages = direct["monitored_pages"]
    # A snapshot linked to a page in another organization is a tenant-boundary
    # integrity failure. Never return its contents or even the foreign count.
    mismatched = _count(
        db,
        select(func.count()).select_from(
            snapshots.join(pages, snapshots.c.monitored_page_id == pages.c.id)
        ).where(
            or_(snapshots.c.organization_id == organization_id,
                pages.c.organization_id == organization_id),
            snapshots.c.organization_id != pages.c.organization_id,
        ),
    )
    if mismatched:
        raise OrganizationInventoryUnavailable("Snapshot tenant relation inconsistent")

    recorded_paths = _count(
        db,
        select(func.count()).select_from(snapshots).where(
            snapshots.c.organization_id == organization_id,
            snapshots.c.raw_content_path.is_not(None),
        ),
    )

    return {
        "schema_version": 1,
        "scope": "authenticated_organization_only",
        "organization_id": str(organization_id),
        "status": "PARTIAL_READ_ONLY_INVENTORY_NOT_EXPORT_OR_ERASURE",
        "direct_table_counts": counts,
        "direct_tables_with_nullable_organization_id": sorted(
            name for name, table in direct.items() if table.c.organization_id.nullable
        ),
        "unresolved_indirect_or_global_tables": list(UNRESOLVED_TABLES),
        "database_coverage_complete": False,
        "snapshot_artifacts": {
            "rows_with_recorded_raw_content_path": recorded_paths,
            "file_existence_and_ownership_verified": False,
        },
        "outside_database_not_inventoried": [
            "raw_snapshot_files", "backups", "exports", "external_ai_providers",
            "email_providers", "downstream_rag_systems",
        ],
        "can_export_organization_data": False,
        "can_erase_organization_data": False,
    }


def _assert_live_schema_authority(connection) -> None:
    """Compare registered ORM tenant ownership with the PostgreSQL catalog.

    The catalog is queried in the SAME snapshot as subsequent tenant counts.
    This prevents a new direct tenant table missing from ORM imports from being
    silently omitted. Indirect/global tables remain explicitly unresolved.
    """
    table_names = set(connection.execute(text("""
        SELECT DISTINCT col.table_name
        FROM information_schema.columns AS col
        JOIN information_schema.tables AS tbl
          ON tbl.table_schema = col.table_schema
         AND tbl.table_name = col.table_name
        WHERE col.table_schema = current_schema()
          AND col.column_name = 'organization_id'
          AND tbl.table_type = 'BASE TABLE'
    """)).scalars())
    if table_names != DIRECT_ORGANIZATION_TABLES:
        raise OrganizationInventoryUnavailable("PostgreSQL tenant schema coverage changed")


def _assert_active_admin_in_snapshot(connection, user_id: uuid.UUID,
                                     organization_id: uuid.UUID, *, users, organizations) -> None:
    """Recheck active account AND active tenant in the inventory snapshot.

    The authentication dependency already checks both, but the organization
    may be suspended between its earlier query and this separate transaction.
    """
    result = connection.execute(
        select(users.c.id).select_from(
            users.join(organizations, users.c.organization_id == organizations.c.id)
        ).where(
            users.c.id == user_id,
            users.c.organization_id == organization_id,
            users.c.role == "admin",
            users.c.is_active.is_(True),
            users.c.deleted_at.is_(None),
            organizations.c.is_active.is_(True),
        ).limit(1)
    ).first()
    if result is None:
        raise OrganizationInventoryUnavailable("Administrator authorization unavailable")


def build_consistent_organization_data_inventory(
    auth_db: Session,
    authenticated_user_id: uuid.UUID,
    organization_id: uuid.UUID,
) -> dict:
    """Use a separate consistent read-only PostgreSQL snapshot for inventory.

    Auth dependencies may already have used their own session/transaction, so
    changing isolation on that session could fail or leave mixed snapshots.
    The new transaction is started by SET TRANSACTION as its FIRST statement.
    No data changes or external calls are made.
    """
    if not isinstance(organization_id, uuid.UUID) or not isinstance(authenticated_user_id, uuid.UUID):
        raise OrganizationInventoryUnavailable("Invalid authenticated identity or organization")
    bind = auth_db.get_bind()
    if not isinstance(bind, Engine) or bind.dialect.name != "postgresql":
        raise OrganizationInventoryUnavailable("PostgreSQL engine required")

    # Bounded live inventory is an admin diagnostic, not a long-running export.
    # Each statement is limited separately; the overall budget also caps the
    # accumulated time across ~35 metadata and COUNT queries.
    deadline = time.monotonic() + 8.0
    with bind.connect() as connection:
        connection.exec_driver_sql(
            "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
        )
        connection.exec_driver_sql("SET LOCAL statement_timeout = '1500ms'")
        connection.exec_driver_sql("SET LOCAL lock_timeout = '750ms'")
        if connection.execute(text("SHOW transaction_read_only")).scalar_one() != "on":
            raise OrganizationInventoryUnavailable("Read-only transaction unavailable")
        # Authorization is rechecked against the same database snapshot used
        # for counts; do not trust only the previously loaded auth ORM object.
        metadata = _runtime_metadata()
        direct = _validated_direct_tables(metadata)
        _assert_active_admin_in_snapshot(
            connection, authenticated_user_id, organization_id,
            users=direct["users"], organizations=metadata.tables["organizations"],
        )
        _assert_live_schema_authority(connection)
        if time.monotonic() >= deadline:
            raise OrganizationInventoryUnavailable("Inventory time budget exceeded")
        with Session(bind=connection, autoflush=False) as query_db:
            query_db.info["sde_inventory_deadline"] = deadline
            return build_organization_data_inventory(query_db, organization_id)
