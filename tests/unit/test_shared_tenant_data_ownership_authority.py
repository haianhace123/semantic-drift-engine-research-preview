"""Shared read-only tenant ownership contract: no DB network or credentials."""

from uuid import uuid4

import pytest
from sqlalchemy import Column, ForeignKey, MetaData, String, Table

from scripts import audit_organization_data_lifecycle_readonly as operator
from src.services import organization_data_inventory_service as admin_api
from src.services import tenant_data_ownership_authority as authority


def _schema():
    metadata = MetaData()
    for name in ("users", "lineage_invalidation_decisions", "lineage_observations", "page_snapshots"):
        Table(name, metadata, Column("id", String, primary_key=True), Column("organization_id", String))
    for name, parent, field in (("consent_logs", "users", "user_id"),
                                ("user_sessions", "users", "user_id"),
                                ("lineage_invalidation_decision_path_revisions", "lineage_invalidation_decisions", "decision_id"),
                                ("lineage_observation_dependencies", "lineage_observations", "observation_id")):
        Table(name, metadata, Column("id", String, primary_key=True),
              Column(field, String, ForeignKey(f"{parent}.id")))
    Table("temporal_lineage_correlations", metadata,
          Column("page_snapshot_id", String, ForeignKey("page_snapshots.id")),
          Column("lineage_observation_id", String, ForeignKey("lineage_observations.id")))
    Table("worker_operational_states", metadata, Column("id", String, primary_key=True))
    return metadata


def test_operator_uses_one_source_of_indirect_ownership_contract():
    assert operator.INDIRECT_OWNER_PATHS is authority.INDIRECT_OWNER_PATHS
    assert operator.summarize_indirect_ownership is authority.summarize_indirect_ownership
    assert operator.indirect_owner_paths_are_valid is authority.indirect_owner_paths_are_valid
    assert operator.table_scope is authority.table_scope


def test_admin_uses_same_non_direct_registry_without_new_exposure():
    assert admin_api.UNRESOLVED_TABLES is authority.KNOWN_INDIRECT_OR_GLOBAL_TABLES
    assert len(admin_api.UNRESOLVED_TABLES) == 6
    assert "worker_operational_states" in admin_api.UNRESOLVED_TABLES
    assert not hasattr(admin_api, "summarize_indirect_ownership")


def test_known_fk_paths_and_both_correlation_edges_are_verified():
    schema = _schema()
    assert authority.indirect_owner_paths_are_valid(schema.tables)
    assert len(authority.INDIRECT_OWNER_PATHS) == 4


def test_missing_correlation_relation_fails_closed():
    schema = _schema()
    schema.tables["temporal_lineage_correlations"].c["lineage_observation_id"].foreign_keys.clear()
    assert not authority.indirect_owner_paths_are_valid(schema.tables)


def test_spoofed_indirect_fk_fails_closed():
    schema = _schema()
    schema.tables["user_sessions"].c["user_id"].foreign_keys.clear()
    assert not authority.indirect_owner_paths_are_valid(schema.tables)


def test_global_worker_state_never_assigned_an_organization():
    result = authority.table_scope(_schema().tables["worker_operational_states"])
    assert result["scope"] == "indirect_or_global_requires_review"


def test_admin_fails_closed_before_db_access_if_indirect_contract_missing(monkeypatch):
    from sqlalchemy import Boolean, Uuid
    metadata = MetaData()
    Table("organizations", metadata, Column("id", Uuid(as_uuid=True), primary_key=True),
          Column("is_active", Boolean, nullable=False))
    for name in sorted(admin_api.DIRECT_ORGANIZATION_TABLES):
        fields = [Column("id", Uuid(as_uuid=True), primary_key=True),
                  Column("organization_id", Uuid(as_uuid=True))]
        if name == "page_snapshots":
            fields.extend([Column("monitored_page_id", Uuid(as_uuid=True), ForeignKey("monitored_pages.id")),
                           Column("raw_content_path", String)])
        Table(name, metadata, *fields)
    # The 32 direct tables are present but the verified indirect links are not.
    monkeypatch.setattr(admin_api, "_runtime_metadata", lambda: metadata)
    monkeypatch.setattr(admin_api.Base, "metadata", metadata)
    with pytest.raises(admin_api.OrganizationInventoryUnavailable, match="Indirect tenant ownership contract"):
        admin_api.build_organization_data_inventory(None, uuid4())


def test_full_orm_registration_includes_recovery_certificates():
    metadata = authority.registered_ownership_metadata()
    assert len(metadata.tables) == 39
    assert "lineage_successor_recovery_certificates" in metadata.tables
    assert len([t for t in metadata.tables.values() if "organization_id" in t.c]) == 32
    assert admin_api._runtime_metadata() is metadata
    assert operator.registered_ownership_metadata is authority.registered_ownership_metadata


def test_operator_audit_uses_registered_authority_metadata():
    # Keep this contract explicit: the operator must not silently rely on the
    # subset of models exported by src.models.__init__.
    import inspect
    source = inspect.getsource(operator.audit)
    assert "registered_ownership_metadata()" in source
    assert "Base.metadata" not in source
