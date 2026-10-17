"""Offline tenant-boundary regression tests; no production DB or credentials."""

import uuid

import pytest
from sqlalchemy import Boolean, Column, ForeignKey, MetaData, String, Table, Uuid, create_engine
from sqlalchemy.orm import Session

from src.services.organization_data_inventory_service import (
    DIRECT_ORGANIZATION_TABLES,
    OrganizationInventoryUnavailable,
    build_organization_data_inventory,
    build_consistent_organization_data_inventory,
    _assert_live_schema_authority,
    _assert_active_admin_in_snapshot,
)


def _make_metadata():
    metadata = MetaData()
    Table("organizations", metadata,
          Column("id", Uuid(as_uuid=True), primary_key=True),
          Column("is_active", Boolean, nullable=False, default=True))
    for name in sorted(DIRECT_ORGANIZATION_TABLES):
        fields = [
            Column("id", Uuid(as_uuid=True), primary_key=True),
            Column("organization_id", Uuid(as_uuid=True), nullable=(name == "auth_security_events")),
        ]
        if name == "page_snapshots":
            fields.extend([
                Column("monitored_page_id", Uuid(as_uuid=True), ForeignKey("monitored_pages.id")),
                Column("raw_content_path", String),
            ])
        Table(name, metadata, *fields)
    return metadata


def _seed(db, meta, first, second, *, crossed=False):
    pages = meta.tables["monitored_pages"]
    snapshots = meta.tables["page_snapshots"]
    db.execute(meta.tables["organizations"].insert(), [{"id": first}, {"id": second}])
    page_one, page_two = uuid.uuid4(), uuid.uuid4()
    db.execute(pages.insert(), [
        {"id": page_one, "organization_id": first},
        {"id": page_two, "organization_id": second},
    ])
    db.execute(snapshots.insert(), [
        {"id": uuid.uuid4(), "organization_id": first,
         "monitored_page_id": page_two if crossed else page_one,
         "raw_content_path": "SECRET-TENANT-A-PATH"},
        {"id": uuid.uuid4(), "organization_id": second,
         "monitored_page_id": page_two,
         "raw_content_path": "SECRET-TENANT-B-PATH"},
    ])
    db.commit()


def test_inventory_counts_only_authenticated_organization_and_never_reveals_paths():
    meta = _make_metadata()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    meta.create_all(engine)
    a, b = uuid.uuid4(), uuid.uuid4()
    with Session(engine) as db:
        _seed(db, meta, a, b)
        report = build_organization_data_inventory(db, a, metadata=meta)
        assert report["direct_table_counts"]["page_snapshots"] == 1
        assert report["direct_table_counts"]["monitored_pages"] == 1
        assert report["snapshot_artifacts"]["rows_with_recorded_raw_content_path"] == 1
        assert report["status"] == "PARTIAL_READ_ONLY_INVENTORY_NOT_EXPORT_OR_ERASURE"
        assert not report["database_coverage_complete"]
        assert not report["can_export_organization_data"]
        assert not report["can_erase_organization_data"]
        assert "SECRET" not in str(report)
        assert str(b) not in str(report)


def test_cross_tenant_snapshot_link_fails_closed():
    meta = _make_metadata()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    meta.create_all(engine)
    a, b = uuid.uuid4(), uuid.uuid4()
    with Session(engine) as db:
        _seed(db, meta, a, b, crossed=True)
        with pytest.raises(OrganizationInventoryUnavailable, match="tenant relation"):
            build_organization_data_inventory(db, a, metadata=meta)


def test_unregistered_tenant_table_blocks_inventory():
    meta = _make_metadata()
    Table("new_tenant_data", meta, Column("organization_id", Uuid(as_uuid=True)))
    engine = create_engine("sqlite+pysqlite:///:memory:")
    meta.create_all(engine)
    with Session(engine) as db:
        with pytest.raises(OrganizationInventoryUnavailable, match="coverage changed"):
            build_organization_data_inventory(db, uuid.uuid4(), metadata=meta)


def test_invalid_identity_org_blocks_inventory():
    meta = _make_metadata()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    meta.create_all(engine)
    with Session(engine) as db:
        with pytest.raises(OrganizationInventoryUnavailable, match="authenticated organization"):
            build_organization_data_inventory(db, "other-tenant", metadata=meta)


def test_live_catalog_rejects_unregistered_postgresql_tenant_table():
    class FakeResult:
        def scalars(self):
            return self
        def __iter__(self):
            return iter(set(DIRECT_ORGANIZATION_TABLES) | {"new_unknown_tenant_table"})
    class FakeConnection:
        def execute(self, stmt):
            return FakeResult()
    with pytest.raises(OrganizationInventoryUnavailable, match="PostgreSQL tenant schema"):
        _assert_live_schema_authority(FakeConnection())


def test_consistent_inventory_fails_closed_on_non_postgresql_backend():
    meta = _make_metadata()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    meta.create_all(engine)
    with Session(engine) as db:
        with pytest.raises(OrganizationInventoryUnavailable, match="PostgreSQL engine"):
            build_consistent_organization_data_inventory(db, uuid.uuid4(), uuid.uuid4())


def test_total_budget_rejects_stale_snapshot_before_counts():
    import time
    meta = _make_metadata()
    engine = create_engine("sqlite+pysqlite:///:memory:")
    meta.create_all(engine)
    with Session(engine) as db:
        db.info["sde_inventory_deadline"] = time.monotonic() - 1
        with pytest.raises(OrganizationInventoryUnavailable, match="time budget"):
            build_organization_data_inventory(db, uuid.uuid4(), metadata=meta)


def test_consistent_wrapper_isolates_counts_in_one_read_only_snapshot(monkeypatch):
    import src.services.organization_data_inventory_service as service
    calls = []
    tenant = uuid.uuid4()
    identity = uuid.uuid4()

    class FakeResult:
        def scalar_one(self):
            return "on"

    class FakeConnection:
        def __enter__(self):
            calls.append("connection_open")
            return self
        def __exit__(self, *args):
            calls.append("connection_close_rollback")
        def exec_driver_sql(self, sql):
            calls.append(sql)
        def execute(self, sql):
            calls.append("read_only_status")
            return FakeResult()

    class FakeEngine:
        dialect = type("Dialect", (), {"name": "postgresql"})()
        def connect(self):
            calls.append("engine_connect")
            return FakeConnection()

    class FakeAuthSession:
        def get_bind(self):
            return fake_engine

    class FakeReadSession:
        def __init__(self, bind, autoflush):
            assert isinstance(bind, FakeConnection)
            assert autoflush is False
            self.info = {}
        def __enter__(self):
            calls.append("read_session_open")
            return self
        def __exit__(self, *args):
            calls.append("read_session_close")

    fake_engine = FakeEngine()
    monkeypatch.setattr(service, "Engine", FakeEngine)
    monkeypatch.setattr(service, "Session", FakeReadSession)
    monkeypatch.setattr(service, "_assert_live_schema_authority",
                        lambda conn: calls.append("schema_verified"))
    monkeypatch.setattr(service, "_validated_direct_tables",
                        lambda meta: {"users": object()})
    org_marker = object()
    fake_metadata = type("Metadata", (), {"tables": {"organizations": org_marker}})()
    monkeypatch.setattr(service, "_runtime_metadata", lambda: fake_metadata)
    def check_admin(conn, user, org, *, users, organizations):
        assert user == identity and org == tenant
        assert organizations is org_marker
        calls.append("snapshot_admin_verified")
    monkeypatch.setattr(service, "_assert_active_admin_in_snapshot", check_admin)
    def evaluate(db, org):
        assert org == tenant
        assert db.info["sde_inventory_deadline"] > 0
        calls.append("tenant_counts")
        return {"organization_id": str(org)}
    monkeypatch.setattr(service, "build_organization_data_inventory", evaluate)

    report = service.build_consistent_organization_data_inventory(
        FakeAuthSession(), identity, tenant)
    assert report == {"organization_id": str(tenant)}
    assert calls == [
        "engine_connect", "connection_open",
        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY",
        "SET LOCAL statement_timeout = '1500ms'",
        "SET LOCAL lock_timeout = '750ms'",
        "read_only_status", "snapshot_admin_verified", "schema_verified", "read_session_open",
        "tenant_counts", "read_session_close", "connection_close_rollback",
    ]


def test_snapshot_admin_rechecks_identity_and_tenant_before_counts():
    from sqlalchemy import DateTime
    meta = _make_metadata()
    users = meta.tables["users"]
    users.append_column(Column("role", String, nullable=False))
    users.append_column(Column("is_active", Boolean, nullable=False))
    users.append_column(Column("deleted_at", DateTime, nullable=True))
    engine = create_engine("sqlite+pysqlite:///:memory:")
    meta.create_all(engine)
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    active_admin, viewer, inactive, suspended_org_admin = (
        uuid.uuid4() for _ in range(4)
    )
    organizations = meta.tables["organizations"]
    with engine.begin() as conn:
        conn.execute(organizations.insert(), [
            {"id": tenant_a, "is_active": True},
            {"id": tenant_b, "is_active": False},
        ])
        conn.execute(users.insert(), [
            {"id": active_admin, "organization_id": tenant_a, "role": "admin", "is_active": True},
            {"id": viewer, "organization_id": tenant_a, "role": "viewer", "is_active": True},
            {"id": inactive, "organization_id": tenant_a, "role": "admin", "is_active": False},
            {"id": suspended_org_admin, "organization_id": tenant_b, "role": "admin", "is_active": True},
        ])
    with engine.connect() as conn:
        _assert_active_admin_in_snapshot(
            conn, active_admin, tenant_a, users=users, organizations=organizations
        )
        rejected = (
            (active_admin, tenant_b), (viewer, tenant_a), (inactive, tenant_a),
            (suspended_org_admin, tenant_b),
        )
        for uid, org in rejected:
            with pytest.raises(OrganizationInventoryUnavailable, match="authorization"):
                _assert_active_admin_in_snapshot(
                    conn, uid, org, users=users, organizations=organizations
                )


def test_missing_organization_active_state_fails_closed():
    meta = _make_metadata()
    from sqlalchemy import MetaData, Table, Column, Uuid
    stale = MetaData()
    Table("organizations", stale, Column("id", Uuid(as_uuid=True), primary_key=True))
    for name, table in meta.tables.items():
        if name != "organizations":
            table.to_metadata(stale)
    from src.services.organization_data_inventory_service import _validated_direct_tables
    with pytest.raises(OrganizationInventoryUnavailable, match="active-state authority"):
        _validated_direct_tables(stale)
