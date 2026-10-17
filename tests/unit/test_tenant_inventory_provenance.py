"""Enforce tenant ownership mapping and truthful consent provenance."""
from __future__ import annotations

from pathlib import Path

from src.core.database import Base
from src.models import LineageInvalidationExecutionVerificationRecord
from scripts.audit_organization_data_lifecycle_readonly import (
    INDIRECT_OWNER_PATHS,
    indirect_owner_paths_are_valid,
)
from src.api.auth_routes import router


ROOT = Path(__file__).resolve().parents[2]


def test_verification_table_registered_via_canonical_model_package():
    assert LineageInvalidationExecutionVerificationRecord.__tablename__ == (
        "lineage_invalidation_execution_verifications"
    )
    assert "lineage_invalidation_execution_verifications" in Base.metadata.tables
    assert "organization_id" in Base.metadata.tables[
        "lineage_invalidation_execution_verifications"
    ].c


def test_all_indirect_tenant_paths_have_real_parent_foreign_keys():
    assert indirect_owner_paths_are_valid(Base.metadata.tables)
    assert len(INDIRECT_OWNER_PATHS) == 4


def test_unattributed_consent_write_endpoint_is_retired():
    assert not any(
        getattr(route, "path", "") == "/api/auth/consent" and
        "POST" in getattr(route, "methods", set())
        for route in router.routes
    )
    source = (ROOT / "src/api/auth_routes.py").read_text(encoding="utf-8")
    assert "user_id=None" not in source
    assert 'consent_type=\n            "terms"' in source


def test_global_worker_state_is_not_assigned_to_any_tenant():
    table = Base.metadata.tables["worker_operational_states"]
    assert "organization_id" not in table.c
