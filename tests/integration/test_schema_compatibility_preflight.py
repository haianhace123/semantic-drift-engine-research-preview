"""PostgreSQL-backed proof the schema preflight never mutates the migration ledger."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from disposable_postgres_admin_guard import require_disposable_postgres_admin
from sqlalchemy import create_engine, text

from src.services.schema_compatibility_service import (
    SchemaCompatibilityError,
    assert_schema_compatible,
)

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="PostgreSQL integration tests are disabled",
)


def test_preflight_rejects_unknown_and_missing_migrations_without_writes(
    tmp_path: Path,
) -> None:
    require_disposable_postgres_admin()
    schema = f"sde_pr12_{uuid.uuid4().hex[:12]}"
    admin = create_engine(os.environ["DATABASE_URL"])
    scoped = None
    try:
        with admin.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        scoped = create_engine(
            os.environ["DATABASE_URL"],
            connect_args={"options": f"-csearch_path={schema}"},
        )
        versions = [
            "000_base_schema.sql",
            "001_auth_production.sql",
            "002_semantic_drift_production.sql",
            "003_entity_tenant_isolation.sql",
        ]
        for version in versions:
            (tmp_path / version).write_text("-- test\n", encoding="utf-8")
        with scoped.begin() as conn:
            conn.execute(text("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY)"))
            for version in versions[:-1]:
                conn.execute(
                    text("INSERT INTO schema_migrations (version) VALUES (:v)"),
                    {"v": version},
                )

        with scoped.connect() as conn:
            with pytest.raises(SchemaCompatibilityError, match="Missing"):
                assert_schema_compatible(conn, migrations_dir=tmp_path)
        with scoped.begin() as conn:
            conn.execute(
                text("INSERT INTO schema_migrations (version) VALUES (:v)"),
                {"v": versions[-1]},
            )
        with scoped.connect() as conn:
            assert_schema_compatible(conn, migrations_dir=tmp_path)
        with scoped.begin() as conn:
            conn.execute(
                text("INSERT INTO schema_migrations (version) VALUES ('099_newer.sql')")
            )
        with scoped.connect() as conn:
            with pytest.raises(SchemaCompatibilityError, match="unknown"):
                assert_schema_compatible(conn, migrations_dir=tmp_path)
            assert conn.execute(
                text("SELECT COUNT(*) FROM schema_migrations")
            ).scalar_one() == 5
    finally:
        if scoped is not None:
            scoped.dispose()
        try:
            with admin.begin() as conn:
                conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        finally:
            admin.dispose()
