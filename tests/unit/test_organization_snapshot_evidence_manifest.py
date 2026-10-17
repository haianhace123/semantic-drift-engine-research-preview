"""No DB service, providers, filesystem snapshot or Docker required."""
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4
import importlib.util
import json
import sys

import pytest
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, MetaData, String, Table, Uuid, create_engine

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src/services/organization_evidence_manifest_service.py"
CLI = ROOT / "scripts/export_organization_snapshot_evidence_readonly.py"


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


svc = _module(SRC, "sde_evidence_manifest_under_test")
cli = _module(CLI, "sde_evidence_manifest_cli_under_test")


@pytest.fixture
def seeded():
    org_a, org_b = uuid4(), uuid4()
    page_a, page_b = uuid4(), uuid4()
    m = MetaData()
    orgs = Table("organizations", m,
                 Column("id", Uuid(as_uuid=True), primary_key=True),
                 Column("is_active", Boolean, nullable=False))
    pages = Table("monitored_pages", m,
                  Column("id", Uuid(as_uuid=True), primary_key=True),
                  Column("organization_id", Uuid(as_uuid=True)),
                  Column("page_type", String))
    snapshots = Table("page_snapshots", m,
                      Column("id", Uuid(as_uuid=True), primary_key=True),
                      Column("organization_id", Uuid(as_uuid=True)),
                      Column("monitored_page_id", Uuid(as_uuid=True)),
                      Column("content_hash", String),
                      Column("crawled_at", DateTime(timezone=True)),
                      Column("embedding_status", String),
                      Column("raw_content", String),
                      Column("raw_content_path", String),
                      Column("embedding_error", String),
                      Column("embedding", String))
    e = create_engine("sqlite+pysqlite:///:memory:")
    m.create_all(e)
    with e.begin() as c:
        c.execute(orgs.insert(), [{"id": org_a, "is_active": True},
                                   {"id": org_b, "is_active": True}])
        c.execute(pages.insert(), [{"id": page_a, "organization_id": org_a, "page_type": "docs"},
                                    {"id": page_b, "organization_id": org_b, "page_type": "privacy"}])
        c.execute(snapshots.insert(), [{"id": uuid4(), "organization_id": org_a,
                                        "monitored_page_id": page_a,
                                        "content_hash": "a" * 64,
                                        "crawled_at": datetime(2026, 10, 15, tzinfo=timezone.utc),
                                        "embedding_status": "completed",
                                        "raw_content": "SENSITIVE_DOC_A",
                                        "raw_content_path": "/sensitive/path",
                                        "embedding_error": "SENSITIVE_ERROR",
                                        "embedding": "SENSITIVE_VECTOR"},
                                       {"id": uuid4(), "organization_id": org_b,
                                        "monitored_page_id": page_b,
                                        "content_hash": "b" * 64,
                                        "crawled_at": datetime(2026, 10, 14, tzinfo=timezone.utc),
                                        "embedding_status": "pending",
                                        "raw_content": "SENSITIVE_DOC_B",
                                        "raw_content_path": "/tenant-B/path",
                                        "embedding_error": "private",
                                        "embedding": "private-vector"}])
    return e, m, org_a, org_b, page_a, page_b, snapshots, orgs


def _run(seeded, org=None, limit=200):
    e, m, a, *_ = seeded
    with e.connect() as c:
        # sqlite has no time-zone aware roundtrip; SQL test fixtures synthesize
        # aware datetimes via SQLite ISO strings parsed as naive below.
        return svc._collect_bounded_manifest(c, organization_id=org or a, metadata=m, limit=limit)


def test_tenant_scope_excludes_other_tenant_values(seeded, monkeypatch):
    # SQLite strips time zone from DateTime; production PostgreSQL doesn't.
    monkeypatch.setattr(svc, "_as_utc_timestamp", lambda x: x.isoformat() if x else None)
    report = _run(seeded)
    serial = json.dumps(report)
    assert report["snapshot_count"] == 1
    assert report["source_count"] == 1
    assert len(report["records"]) == 1
    assert report["records"][0]["content_sha256"] == "a" * 64
    for private in ("SENSITIVE_DOC_A", "SENSITIVE_DOC_B", "/sensitive/path",
                    "/tenant-B/path", "SENSITIVE_VECTOR", "private-vector", "private"):
        assert private not in serial
    assert str(seeded[3]) not in serial
    assert report["full_organization_export"] is False
    assert report["erasure_attestation"] is False


def test_bounded_result_marks_truncation(seeded, monkeypatch):
    monkeypatch.setattr(svc, "_as_utc_timestamp", lambda x: x.isoformat() if x else None)
    e, m, org, _, page, _, snapshot, _ = seeded
    with e.begin() as c:
        c.execute(snapshot.insert(), [{"id": uuid4(), "organization_id": org,
                                       "monitored_page_id": page, "content_hash": "c" * 64,
                                       "crawled_at": datetime(2026, 10, 16, tzinfo=timezone.utc),
                                       "embedding_status": "pending"}])
    report = _run(seeded, limit=1)
    assert report["snapshot_count"] == 2
    assert report["records_returned"] == 1
    assert report["status"] == "TRUNCATED"


def test_cross_tenant_snapshot_relationship_blocks(seeded):
    e, m, org_a, org_b, page_a, page_b, snapshot, _ = seeded
    with e.begin() as c:
        c.execute(snapshot.insert(), [{"id": uuid4(), "organization_id": org_a,
                                       "monitored_page_id": page_b, "content_hash": "d" * 64,
                                       "embedding_status": "pending"}])
    with pytest.raises(svc.EvidenceManifestUnavailable, match="Cross-tenant"):
        _run(seeded)


def test_inactive_org_denied(seeded):
    e, m, org, _, _, _, _, org_table = seeded
    with e.begin() as c:
        c.execute(org_table.update().where(org_table.c.id == org).values(is_active=False))
    with pytest.raises(svc.EvidenceManifestUnavailable, match="Active organization"):
        _run(seeded)


def test_invalid_content_hash_blocks(seeded):
    e, m, org, _, _, _, snapshot, _ = seeded
    with e.begin() as c:
        c.execute(snapshot.update().where(snapshot.c.organization_id == org).values(content_hash="bad"))
    with pytest.raises(svc.EvidenceManifestUnavailable, match="digest"):
        _run(seeded)


@pytest.mark.parametrize("limit", [0, 201, -1, 1.5, True])
def test_invalid_limits_fail(seeded, limit):
    with pytest.raises(svc.EvidenceManifestUnavailable, match="limit"):
        _run(seeded, limit=limit)


def test_non_uuid_tenant_fail(seeded):
    with pytest.raises(svc.EvidenceManifestUnavailable, match="Organization identity"):
        _run(seeded, org="tenant-A")


def test_output_exclusive_private_and_non_overwrite(tmp_path):
    out = tmp_path / "manifest.json"
    cli._write_private_new_file(out, '{"ok": true}')
    assert out.read_text() == '{"ok": true}'
    with pytest.raises(FileExistsError):
        cli._write_private_new_file(out, 'new')


def test_output_symlink_refused(tmp_path):
    target = tmp_path / "target"
    target.write_text("private")
    link = tmp_path / "evidence.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Symlink unavailable on this platform")
    with pytest.raises(FileExistsError):
        cli._write_private_new_file(link, "overwrite")
    assert target.read_text() == "private"


def test_missing_path_timestamp_is_explicit():
    assert svc._as_utc_timestamp(None) is None
    with pytest.raises(svc.EvidenceManifestUnavailable, match="timezone-aware"):
        svc._as_utc_timestamp(datetime(2026, 10, 15))
