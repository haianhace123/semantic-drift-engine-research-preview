"""Opt-in PostgreSQL disaster recovery acceptance on two disposable databases.

Never runs against the configured application database. The test creates two
uniquely named databases, migrates/seeds the source, restores to the target,
and removes only the databases that it successfully created.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from disposable_postgres_admin_guard import require_disposable_postgres_admin
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

from scripts import backup_database, migrate, restore_database
from src.api import system_routes
from src.main import app
from src.services.schema_compatibility_service import assert_schema_compatible


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1"
    or os.getenv("RUN_DR_ACCEPTANCE_TESTS") != "1",
    reason="destructive disposable-database DR acceptance requires both opt-in flags",
)

_SAFE_TEST_DATABASE = re.compile(r"^sde_pr13_(?:source|restored)_[0-9a-f]{12}$")


def _test_url(configured_url: str, database: str) -> str:
    assert _SAFE_TEST_DATABASE.fullmatch(database)
    return make_url(configured_url).set(database=database).render_as_string(
        hide_password=False
    )


def _create_database(admin: Engine, database: str) -> None:
    assert _SAFE_TEST_DATABASE.fullmatch(database)
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database}" TEMPLATE template0'))


def _drop_database(admin: Engine, database: str) -> None:
    assert _SAFE_TEST_DATABASE.fullmatch(database)
    with admin.connect() as connection:
        connection.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :database AND pid <> pg_backend_pid()"
            ),
            {"database": database},
        )
        connection.execute(text(f'DROP DATABASE IF EXISTS "{database}"'))


def _seed_organizations(engine: Engine) -> None:
    """Use real FK-linked events, assessments and exact-revision lineage."""
    with engine.begin() as connection:
        for index in (1, 2):
            org_id = uuid.uuid4()
            entity_id = uuid.uuid4()
            page_id = uuid.uuid4()
            before_id = uuid.uuid4()
            after_id = uuid.uuid4()
            event_id = uuid.uuid4()
            observation_id = uuid.uuid4()
            raw_before = f"Policy revision 1 for test tenant {index}"
            raw_after = f"Policy revision 2 for test tenant {index}"

            connection.execute(
                text(
                    "INSERT INTO organizations "
                    "(id, name, slug, billing_email, is_active) "
                    "VALUES (:id, :name, :slug, :email, true)"
                ),
                {
                    "id": org_id,
                    "name": f"DR test tenant {index}",
                    "slug": f"pr13-{org_id.hex[:16]}",
                    "email": f"pr13-{index}@example.invalid",
                },
            )
            # Identical entity domains / asset IDs across both tenants prove
            # the restored organization identity remains the partition key.
            connection.execute(
                text(
                    "INSERT INTO entities (id, organization_id, domain, name) "
                    "VALUES (:id, :org, 'shared.example.invalid', :name)"
                ),
                {"id": entity_id, "org": org_id, "name": f"Tenant {index}"},
            )
            connection.execute(
                text(
                    "INSERT INTO monitored_pages "
                    "(id, organization_id, entity_id, url, page_type, "
                    "crawl_frequency, is_active) "
                    "VALUES (:id, :org, :entity, :url, 'tos', 'daily', true)"
                ),
                {
                    "id": page_id,
                    "org": org_id,
                    "entity": entity_id,
                    "url": "https://shared.example.invalid/policy",
                },
            )
            for snapshot_id, content in (
                (before_id, raw_before),
                (after_id, raw_after),
            ):
                connection.execute(
                    text(
                        "INSERT INTO page_snapshots "
                        "(id, monitored_page_id, organization_id, content_hash, "
                        "raw_content, content_size) "
                        "VALUES (:id, :page, :org, :hash, :content, :size)"
                    ),
                    {
                        "id": snapshot_id,
                        "page": page_id,
                        "org": org_id,
                        "hash": hashlib.sha256(content.encode()).hexdigest(),
                        "content": content,
                        "size": len(content.encode()),
                    },
                )
            connection.execute(
                text(
                    "INSERT INTO semantic_drift_events "
                    "(id, monitored_page_id, organization_id, old_snapshot_id, "
                    "new_snapshot_id, semantic_delta_json, summary_text, "
                    "analysis_status, vector_analysis_status, risk_scoring_status, "
                    "risk_level, changes_count) "
                    "VALUES (:id, :page, :org, :before, :after, "
                    "CAST(:delta AS json), :summary, 'completed', 'completed', "
                    "'completed', 'medium', 1)"
                ),
                {
                    "id": event_id,
                    "page": page_id,
                    "org": org_id,
                    "before": before_id,
                    "after": after_id,
                    "delta": json.dumps({"changes": [{"type": "modified"}]}),
                    "summary": f"Policy obligation changed for tenant {index}",
                },
            )
            connection.execute(
                text(
                    "INSERT INTO risk_assessments "
                    "(id, organization_id, semantic_drift_event_id, risk_version, "
                    "assessed_at, score, semantic_magnitude_contribution, "
                    "legacy_impact_contribution, semantic_severity_contribution, "
                    "page_context_contribution, confidence_contribution, "
                    "topology_score, topology_contribution, "
                    "topology_shift_component, topology_centrality_component, "
                    "topology_downstream_component, has_topology_signal, "
                    "has_previous_topology_state) "
                    "VALUES (:id, :org, :event, 'deterministic_v2', now(), "
                    "0.35, 0.10, 0.10, 0.10, 0.03, 0.02, 0, 0, 0, 0, 0, false, false)"
                ),
                {"id": uuid.uuid4(), "org": org_id, "event": event_id},
            )
            connection.execute(
                text(
                    "INSERT INTO lineage_observations "
                    "(id, organization_id, observation_kind, source_external_id, "
                    "snapshot_external_id, asset_external_id, asset_kind, "
                    "revision_external_id, sequence) "
                    "VALUES (:id, :org, 'materialization', 'policy-source', "
                    "'source-revision-2', 'shared-index', 'index', 'index-v2', 2)"
                ),
                {"id": observation_id, "org": org_id},
            )
            connection.execute(
                text(
                    "INSERT INTO lineage_observation_dependencies "
                    "(id, observation_id, asset_external_id, "
                    "revision_external_id, dependency_type) "
                    "VALUES (:id, :observation, 'shared-policy', 'policy-v2', "
                    "'derived_from')"
                ),
                {"id": uuid.uuid4(), "observation": observation_id},
            )


# The projection intentionally contains no passwords, tokens or private raw
# source content. We compare exactly the data identities needed by this test.
_RECOVERY_QUERIES = {
    "organizations": "SELECT id::text, slug FROM organizations ORDER BY id",
    "entities": (
        "SELECT id::text, organization_id::text, domain "
        "FROM entities ORDER BY id"
    ),
    "monitored_pages": (
        "SELECT id::text, organization_id::text, entity_id::text, "
        "url, crawl_frequency::text FROM monitored_pages ORDER BY id"
    ),
    "page_snapshots": (
        "SELECT id::text, organization_id::text, monitored_page_id::text, "
        "content_hash, raw_content FROM page_snapshots ORDER BY id"
    ),
    "semantic_drift_events": (
        "SELECT id::text, organization_id::text, old_snapshot_id::text, "
        "new_snapshot_id::text, summary_text, analysis_status, "
        "vector_analysis_status, risk_scoring_status, semantic_delta_json::text "
        "FROM semantic_drift_events ORDER BY id"
    ),
    "risk_assessments": (
        "SELECT organization_id::text, semantic_drift_event_id::text, "
        "risk_version, score FROM risk_assessments ORDER BY id"
    ),
    "lineage_observations": (
        "SELECT id::text, organization_id::text, asset_external_id, "
        "revision_external_id, sequence "
        "FROM lineage_observations ORDER BY id"
    ),
    "lineage_dependencies": (
        "SELECT o.organization_id::text, o.id::text, d.asset_external_id, "
        "d.revision_external_id, d.dependency_type "
        "FROM lineage_observation_dependencies d "
        "JOIN lineage_observations o ON o.id = d.observation_id "
        "ORDER BY o.id, d.id"
    ),
}


def _recovery_projection(engine: Engine) -> dict[str, list[tuple]]:
    with engine.connect() as connection:
        return {
            name: [tuple(row) for row in connection.execute(text(query))]
            for name, query in _RECOVERY_QUERIES.items()
        }


def _assert_tenant_boundaries(engine: Engine) -> None:
    with engine.connect() as connection:
        org_ids = [
            row[0]
            for row in connection.execute(text("SELECT id FROM organizations ORDER BY id"))
        ]
        assert len(org_ids) == 2
        for org_id in org_ids:
            for table, expected in (
                ("entities", 1),
                ("monitored_pages", 1),
                ("page_snapshots", 2),
                ("semantic_drift_events", 1),
                ("risk_assessments", 1),
                ("lineage_observations", 1),
            ):
                assert connection.execute(
                    text(f"SELECT count(*) FROM {table} WHERE organization_id = :org"),
                    {"org": org_id},
                ).scalar_one() == expected
            assert connection.execute(
                text(
                    "SELECT count(*) FROM lineage_observation_dependencies d "
                    "JOIN lineage_observations o ON o.id = d.observation_id "
                    "WHERE o.organization_id = :org"
                ),
                {"org": org_id},
            ).scalar_one() == 1


def _worker_preflight(database_url: str) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ, DATABASE_URL=database_url)
    # This checks the real production entrypoint, but execs only a harmless
    # subprocess, never an active worker with external side effects.
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "scripts.schema_preflight",
            "--",
            sys.executable,
            "-c",
            "pass",
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def test_restored_database_passes_schema_readiness_and_data_integrity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    require_disposable_postgres_admin()
    configured_url = os.environ["DATABASE_URL"]
    suffix = uuid.uuid4().hex[:12]
    source_name = f"sde_pr13_source_{suffix}"
    restored_name = f"sde_pr13_restored_{suffix}"
    source_url = _test_url(configured_url, source_name)
    restored_url = _test_url(configured_url, restored_name)
    admin = create_engine(configured_url, isolation_level="AUTOCOMMIT")
    created: list[str] = []
    source: Engine | None = None
    restored: Engine | None = None

    try:
        for name in (source_name, restored_name):
            _create_database(admin, name)
            created.append(name)

        monkeypatch.setenv("DATABASE_URL", source_url)
        # Exercise exactly the release migration runner and manifest used by
        # production preflight. Only the newly-created source is migrated.
        migrate.run_migrations()
        source = create_engine(source_url)
        with source.connect() as connection:
            assert_schema_compatible(connection)
        _seed_organizations(source)
        before = _recovery_projection(source)
        _assert_tenant_boundaries(source)

        monkeypatch.setattr(backup_database, "BACKUP_DIR", tmp_path)
        archive = backup_database.run_backup()
        assert archive.is_file() and archive.stat().st_size > 0
        assert not list(tmp_path.glob("*.partial"))
        restore_database.restore_backup(archive, restored_name)

        restored = create_engine(restored_url)
        with restored.connect() as connection:
            assert_schema_compatible(connection)
        assert _recovery_projection(restored) == before
        _assert_tenant_boundaries(restored)
        assert _worker_preflight(restored_url).returncode == 0

        # Exercise the application's real /ready handler (including Redis).
        # Only the DB engine is redirected to the disposable restored DB.
        monkeypatch.setattr(system_routes, "engine", restored)
        monkeypatch.setenv("ENVIRONMENT", "production")
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            ready = client.get("/ready")
            assert ready.status_code == 200, ready.text
            assert ready.json()["checks"]["database"]["status"] == "ok"
            assert ready.json()["checks"]["redis"]["status"] == "ok"

            # Simulate rollback to an older/unknown migration manifest.
            # The change exists only in the disposable restored database.
            with restored.begin() as connection:
                connection.execute(
                    text("INSERT INTO schema_migrations (version) VALUES (:version)"),
                    {"version": "999_pr13_unknown_release.sql"},
                )
            incompatible = client.get("/ready")
            assert incompatible.status_code == 503
            assert incompatible.json()["checks"]["database"]["status"] == "schema_incompatible"
            assert _worker_preflight(restored_url).returncode != 0

            with restored.begin() as connection:
                connection.execute(
                    text("DELETE FROM schema_migrations WHERE version = :version"),
                    {"version": "999_pr13_unknown_release.sql"},
                )
            assert client.get("/ready").status_code == 200
        assert _recovery_projection(restored) == before
    finally:
        if restored is not None:
            restored.dispose()
        if source is not None:
            source.dispose()
        try:
            for name in reversed(created):
                _drop_database(admin, name)
        finally:
            admin.dispose()
