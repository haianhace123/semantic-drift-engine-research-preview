"""Canonical ownership contracts for organization lifecycle evidence.

Read-only, conservative ownership resolution. It does not authorize organization
export, erasure, admission, or copying records across tenant boundaries.

Operator audit and admin inventory import from here; do not duplicate the
indirect FK paths/correlation classification in those consumers.
"""

from __future__ import annotations

import importlib
import uuid
from typing import Any

# Reviewed model registration. `src.models` does not currently export every
# tenant-owned model; in particular, the recovery certificate model needs an
# explicit import. This is the common schema authority for both consumers.
_DIRECT_MODEL_MODULES = (
    "alert", "alert_delivery", "anomaly", "api_key", "auth_security_event",
    "entity", "entity_graph_metric_snapshot", "entity_relationship",
    "entity_relationship_observation", "investigation", "lineage_execution_target_binding",
    "lineage_invalidation_decision", "lineage_invalidation_execution_attempt",
    "lineage_invalidation_execution_intent", "lineage_invalidation_execution_result",
    "lineage_invalidation_execution_verification", "lineage_invalidation_policy_activation",
    "lineage_observation", "lineage_successor_recovery_certificate", "monitored_page",
    "organization_alert_policy_revision", "organization_work_schedule", "page_snapshot",
    "pattern", "pipeline_checkpoint", "risk_assessment", "semantic_drift_event",
    "temporal_lineage_invalidation_decision_provenance", "topology_shift_snapshot",
    "user", "watchlist", "webhook_subscription",
)


def registered_ownership_metadata():
    """Register the full reviewed ORM model set without querying the database."""
    from src.core.database import Base

    for module in _DIRECT_MODEL_MODULES:
        importlib.import_module(f"src.models.{module}")
    return Base.metadata


# Full tenant-level export/erasure remains blocked while these have unresolved
# attribution, external artifact or global-state provenance.
KNOWN_INDIRECT_OR_GLOBAL_TABLES = (
    "consent_logs", "lineage_invalidation_decision_path_revisions",
    "lineage_observation_dependencies", "temporal_lineage_correlations",
    "user_sessions", "worker_operational_states",
)

# Explicit ownership paths, not heuristic inference from table or FK names.
# The correlation table is handled separately because it links two tenant roots.
INDIRECT_OWNER_PATHS = {
    "consent_logs": ("users", "user_id"),
    "user_sessions": ("users", "user_id"),
    "lineage_invalidation_decision_path_revisions": (
        "lineage_invalidation_decisions", "decision_id"
    ),
    "lineage_observation_dependencies": ("lineage_observations", "observation_id"),
}


def indirect_owner_paths_are_valid(tables: Any) -> bool:
    """Require each declared ownership edge to match actual ORM foreign keys."""
    for name, (parent_name, fk_field) in INDIRECT_OWNER_PATHS.items():
        if name not in tables or parent_name not in tables:
            return False
        child = tables[name]
        parent = tables[parent_name]
        if "organization_id" not in parent.c or fk_field not in child.c:
            return False
        if not any(
            foreign_key.target_fullname == f"{parent_name}.id"
            for foreign_key in child.c[fk_field].foreign_keys
        ):
            return False
    correlation_name = "temporal_lineage_correlations"
    if correlation_name not in tables:
        return False
    correlation = tables[correlation_name]
    for field, parent_name in (
        ("page_snapshot_id", "page_snapshots"),
        ("lineage_observation_id", "lineage_observations"),
    ):
        if parent_name not in tables or field not in correlation.c:
            return False
        parent = tables[parent_name]
        if "organization_id" not in parent.c:
            return False
        if not any(
            fk.target_fullname == f"{parent_name}.id"
            for fk in correlation.c[field].foreign_keys
        ):
            return False
    return True


def table_scope(table: Any) -> dict[str, Any]:
    """Classify table ownership conservatively based on explicit ORM schema."""
    if table.name == "organizations":
        scope = "organization_root"
    elif "organization_id" in table.c:
        scope = "direct_organization_id"
    else:
        scope = "indirect_or_global_requires_review"
    return {
        "table": table.name,
        "scope": scope,
        "referenced_tables": sorted({fk.target_fullname.split(".")[0] for fk in table.foreign_keys}),
    }


def summarize_indirect_ownership(session: Any, tables: Any, organization_id: uuid.UUID) -> dict[str, Any]:
    """Count only known tenant-owned child rows and both sides of correlations.

    The caller must use a read-only DB transaction in production. No raw data is
    returned; a cross-tenant link is a diagnostic, never a valid ownership edge.
    """
    from sqlalchemy import and_, func, select

    if not indirect_owner_paths_are_valid(tables):
        raise ValueError("An indirect ownership contract no longer matches ORM schema")

    indirect_counts = {}
    for child_name, (parent_name, fk_field) in INDIRECT_OWNER_PATHS.items():
        child = tables[child_name]
        parent = tables[parent_name]
        indirect_counts[child_name] = session.execute(
            select(func.count()).select_from(
                child.join(parent, child.c[fk_field] == parent.c.id)
            ).where(parent.c.organization_id == organization_id)
        ).scalar_one()

    snapshots = tables["page_snapshots"]
    observations = tables["lineage_observations"]
    correlations = tables["temporal_lineage_correlations"]
    correlation_join = correlations.join(
        snapshots, correlations.c.page_snapshot_id == snapshots.c.id
    ).join(
        observations, correlations.c.lineage_observation_id == observations.c.id
    )
    correlation_counts = {}
    for label, snap_is_tenant, obs_is_tenant in (
        ("same_tenant", True, True),
        ("cross_tenant_outgoing", True, False),
        ("cross_tenant_incoming", False, True),
    ):
        correlation_counts[label] = session.execute(
            select(func.count()).select_from(correlation_join).where(
                and_(
                    (snapshots.c.organization_id == organization_id) if snap_is_tenant
                    else (snapshots.c.organization_id != organization_id),
                    (observations.c.organization_id == organization_id) if obs_is_tenant
                    else (observations.c.organization_id != organization_id),
                )
            )
        ).scalar_one()
    indirect_counts["temporal_lineage_correlations"] = sum(correlation_counts.values())

    # Older public consent writes had nullable user_id, with no attributable tenant.
    consent = tables["consent_logs"]
    unattributed_consent_count = session.execute(
        select(func.count()).select_from(consent).where(consent.c.user_id.is_(None))
    ).scalar_one()
    return {
        "indirect_table_counts": indirect_counts,
        "correlation_integrity": correlation_counts,
        "unattributed_consent_records_global": unattributed_consent_count,
    }


