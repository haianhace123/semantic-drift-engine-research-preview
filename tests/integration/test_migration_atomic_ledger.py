"""Real PostgreSQL proof that DDL and the schema ledger commit atomically."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from disposable_postgres_admin_guard import require_disposable_postgres_admin
from sqlalchemy import create_engine, text

from scripts import migrate


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DB_INTEGRATION_TESTS") != "1",
    reason="database integration tests are disabled",
)


def test_migration_ddl_rolls_back_if_ledger_insert_fails(
    tmp_path: Path,
) -> None:
    require_disposable_postgres_admin()
    # Keep test schema isolated; do not mutate the application's migration ledger.
    url = os.environ["DATABASE_URL"]
    schema = f"sde_pr11_{uuid.uuid4().hex[:12]}"
    admin = create_engine(url)
    scoped = None
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))

        scoped = create_engine(
            url,
            connect_args={"options": f"-csearch_path={schema}"},
        )
        migrate.ensure_migration_table(scoped)
        with scoped.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE schema_migrations "
                    "ADD CONSTRAINT pr11_reject_test "
                    "CHECK (version <> '100_rejected.sql')"
                )
            )

        rejected = tmp_path / "100_rejected.sql"
        rejected.write_text(
            "BEGIN;\nCREATE TABLE pr11_recovery_probe (id INT);\nCOMMIT;\n",
            encoding="utf-8",
        )
        with pytest.raises(Exception, match="pr11_reject_test"):
            migrate.apply_migration(scoped, rejected)

        with scoped.connect() as connection:
            assert connection.execute(
                text("SELECT to_regclass('pr11_recovery_probe')")
            ).scalar_one() is None
            assert connection.execute(
                text("SELECT count(*) FROM schema_migrations")
            ).scalar_one() == 0

        # The same DDL can now succeed with a different valid ledger version.
        approved = tmp_path / "101_approved.sql"
        approved.write_text(rejected.read_text(encoding="utf-8"), encoding="utf-8")
        migrate.apply_migration(scoped, approved)

        with scoped.connect() as connection:
            assert connection.execute(
                text("SELECT to_regclass('pr11_recovery_probe')")
            ).scalar_one() is not None
            assert connection.execute(
                text("SELECT version FROM schema_migrations")
            ).scalar_one() == "101_approved.sql"
    finally:
        if scoped is not None:
            scoped.dispose()
        try:
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        finally:
            admin.dispose()
