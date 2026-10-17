"""
Database migration runner for Semantic Drift Engine.

Migration policy
----------------

000_base_schema.sql
    Squashed baseline representing the current production schema.

001..003
    Historical migrations used by databases created before the baseline.

004+
    Normal forward-only migrations.

Fresh databases receive 000 and automatically record the historical
migrations represented by that baseline.

Existing databases that have already applied all historical migrations
may adopt 000 without executing the baseline SQL again.
"""

from __future__ import annotations

import logging
import os
import re
import sys

from pathlib import Path

from sqlalchemy import (
    create_engine,
    inspect,
    text,
)
from sqlalchemy.engine import Engine


PROJECT_ROOT = (
    Path(__file__).resolve().parents[1]
)

MIGRATIONS_DIR = (
    PROJECT_ROOT
    / "scripts"
    / "migrations"
)


if str(
    PROJECT_ROOT
) not in sys.path:
    sys.path.insert(
        0,
        str(
            PROJECT_ROOT
        ),
    )


BASELINE_MIGRATION = (
    "000_base_schema.sql"
)

BASELINE_INCLUDED_MIGRATIONS = (
    "001_auth_production.sql",
    "002_semantic_drift_production.sql",
    "003_entity_tenant_isolation.sql",
)

CORE_APPLICATION_TABLES = {
    "organizations",
    "users",
    "entities",
    "monitored_pages",
    "page_snapshots",
    "semantic_drift_events",
}

MIGRATION_LOCK_NAMESPACE = 0x534445
MIGRATION_LOCK_ID = 1


logging.basicConfig(
    level=logging.INFO,
    format=(
        "%(asctime)s "
        "%(levelname)s "
        "%(name)s: "
        "%(message)s"
    ),
)

logger = logging.getLogger(
    "semantic_drift.migrations"
)


def get_database_url() -> str:
    """
    Return the explicitly configured database URL.

    There is deliberately no fallback database. Migrating the wrong
    database is more dangerous than failing startup.
    """
    database_url = os.getenv(
        "DATABASE_URL",
        "",
    ).strip()

    if not database_url:
        raise RuntimeError(
            "DATABASE_URL is required "
            "to run database migrations."
        )

    return database_url


def create_migration_engine() -> Engine:
    """Create a short-lived migration engine."""
    return create_engine(
        get_database_url(),
        pool_pre_ping=True,
    )


def discover_migrations() -> list[Path]:
    """
    Discover SQL migrations in deterministic filename order.
    """
    if not MIGRATIONS_DIR.exists():
        raise RuntimeError(
            "Migration directory does not exist: "
            f"{MIGRATIONS_DIR}"
        )

    migrations = sorted(
        path
        for path
        in MIGRATIONS_DIR.iterdir()
        if (
            path.is_file()
            and path.suffix.lower()
            == ".sql"
        )
    )

    if not migrations:
        logger.info(
            "No SQL migrations found in %s.",
            MIGRATIONS_DIR,
        )

    return migrations


def ensure_migration_table(
    engine: Engine,
) -> None:
    """
    Create the migration ledger.

    This is infrastructure metadata, not application schema.
    """
    statement = text(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version VARCHAR(255) PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    with engine.begin() as connection:
        connection.execute(
            statement
        )


def applied_migrations(
    engine: Engine,
) -> set[str]:
    """Return every migration version recorded in the ledger."""
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                """
                SELECT version
                FROM schema_migrations
                ORDER BY version
                """
            )
        )

        return {
            str(
                row[0]
            )
            for row in rows
        }


def record_migrations(
    engine: Engine,
    versions: list[str]
    | tuple[str, ...],
) -> None:
    """
    Record migration versions without executing their SQL.

    This is used only for migrations already represented by the squashed
    baseline or when an already-migrated legacy database adopts baseline
    metadata.
    """
    if not versions:
        return

    with engine.begin() as connection:
        for version in versions:
            connection.execute(
                text(
                    """
                    INSERT INTO schema_migrations (
                        version
                    )
                    VALUES (
                        :version
                    )
                    ON CONFLICT (
                        version
                    )
                    DO NOTHING
                    """
                ),
                {
                    "version": version,
                },
            )


def application_tables(
    engine: Engine,
) -> set[str]:
    """Return public PostgreSQL table names."""
    inspector = inspect(
        engine
    )

    return set(
        inspector.get_table_names(
            schema="public"
        )
    )


def application_schema_exists(
    engine: Engine,
) -> bool:
    """
    Return True when the database already contains the core app schema.

    schema_migrations alone does not count as application schema.
    """
    tables = application_tables(
        engine
    )

    return CORE_APPLICATION_TABLES.issubset(
        tables
    )


def partial_application_schema_exists(
    engine: Engine,
) -> bool:
    """
    Detect a partially-created application schema.

    Automatically baselining a partial schema is unsafe because the
    runner cannot determine which historical state it represents.
    """
    tables = application_tables(
        engine
    )

    present_core_tables = (
        CORE_APPLICATION_TABLES
        & tables
    )

    return bool(
        present_core_tables
    ) and not CORE_APPLICATION_TABLES.issubset(
        tables
    )


# SQL files may contain an explicit outer BEGIN/COMMIT from their original
# psql execution format. Leaving the COMMIT in place would commit DDL before
# the schema_migrations ledger entry, creating a crash window.
_MIGRATION_BEGIN = re.compile(r"^BEGIN\s*;\s*$", re.IGNORECASE)
_MIGRATION_COMMIT = re.compile(r"^COMMIT\s*;\s*$", re.IGNORECASE)


def prepare_transactional_migration_sql(sql: str) -> str:
    """Remove a complete outer transaction wrapper without altering its body.

    The runner owns the transaction containing *both* the migration DDL and
    its ledger insert. Partial or ambiguous standalone transaction controls
    fail closed rather than executing SQL with an unexpected commit boundary.
    """
    lines = sql.splitlines(keepends=True)
    begin_lines = [
        index
        for index, line in enumerate(lines)
        if _MIGRATION_BEGIN.fullmatch(line.strip())
    ]
    commit_lines = [
        index
        for index, line in enumerate(lines)
        if _MIGRATION_COMMIT.fullmatch(line.strip())
    ]

    if not begin_lines and not commit_lines:
        return sql

    executable_lines = [
        index
        for index, line in enumerate(lines)
        if line.strip() and not line.lstrip().startswith("--")
    ]
    if (
        len(begin_lines) != 1
        or len(commit_lines) != 1
        or not executable_lines
        or begin_lines[0] != executable_lines[0]
        or commit_lines[0] != executable_lines[-1]
    ):
        raise RuntimeError(
            "Migration has an unsupported transaction wrapper. "
            "Expected only one outer BEGIN; and COMMIT; pair."
        )

    # Retain surrounding comments and all statements (including DO blocks).
    # Deliberately don't execute the file's COMMIT; psycopg commits only after
    # the migration ledger insert succeeds.
    lines[begin_lines[0]] = "-- Transaction BEGIN managed by migration runner\n"
    lines[commit_lines[0]] = "-- Transaction COMMIT managed by migration runner\n"
    return "".join(lines)


def apply_migration(
    engine: Engine,
    migration: Path,
) -> None:
    """
    Execute one authored SQL migration and record it.

    One database transaction owns both DDL and the ledger entry. If either
    execution or ledger persistence fails, the transaction is rolled back.
    """
    sql = migration.read_text(
        encoding="utf-8",
    ).strip()

    if not sql:
        raise RuntimeError(
            "Migration is empty: "
            f"{migration.name}"
        )

    sql = prepare_transactional_migration_sql(sql)

    logger.info(
        "Applying migration %s.",
        migration.name,
    )

    raw_connection = (
        engine.raw_connection()
    )

    try:
        cursor = (
            raw_connection.cursor()
        )

        try:
            cursor.execute(
                sql
            )

            cursor.execute(
                """
                INSERT INTO schema_migrations (
                    version
                )
                VALUES (%s)
                ON CONFLICT (
                    version
                )
                DO NOTHING
                """,
                (
                    migration.name,
                ),
            )

            raw_connection.commit()

        except Exception:
            raw_connection.rollback()
            raise

        finally:
            cursor.close()

    finally:
        raw_connection.close()

    logger.info(
        "Migration %s applied.",
        migration.name,
    )


def bootstrap_or_adopt_baseline(
    engine: Engine,
    migrations: list[Path],
    completed: set[str],
) -> set[str]:
    """
    Handle the 000 squashed baseline safely.

    Fresh database:
        execute 000, then mark 001..003 as included.

    Existing migrated database:
        if 001..003 are already recorded and all core tables exist,
        record 000 without executing it.

    Partial/ambiguous database:
        fail closed.
    """
    migration_by_name = {
        migration.name: migration
        for migration in migrations
    }

    baseline = migration_by_name.get(
        BASELINE_MIGRATION
    )

    if baseline is None:
        raise RuntimeError(
            "Required baseline migration "
            f"{BASELINE_MIGRATION} "
            "was not found."
        )

    if BASELINE_MIGRATION in completed:
        return completed

    if partial_application_schema_exists(
        engine
    ):
        raise RuntimeError(
            "Database contains a partial application schema "
            "but has not adopted the baseline. "
            "Automatic migration is unsafe."
        )

    if not application_schema_exists(
        engine
    ):
        logger.info(
            "Fresh database detected; "
            "applying baseline %s.",
            BASELINE_MIGRATION,
        )

        apply_migration(
            engine,
            baseline,
        )

        record_migrations(
            engine,
            BASELINE_INCLUDED_MIGRATIONS,
        )

        logger.info(
            "Baseline includes historical migrations: %s.",
            ", ".join(
                BASELINE_INCLUDED_MIGRATIONS
            ),
        )

        return applied_migrations(
            engine
        )

    historical_complete = all(
        version in completed
        for version
        in BASELINE_INCLUDED_MIGRATIONS
    )

    if not historical_complete:
        missing = [
            version
            for version
            in BASELINE_INCLUDED_MIGRATIONS
            if version
            not in completed
        ]

        raise RuntimeError(
            "Existing application schema cannot safely "
            "adopt the baseline because historical "
            "migration records are incomplete: "
            + ", ".join(
                missing
            )
        )

    logger.info(
        "Existing migrated database detected; "
        "recording baseline %s without re-executing it.",
        BASELINE_MIGRATION,
    )

    record_migrations(
        engine,
        [
            BASELINE_MIGRATION,
        ],
    )

    return applied_migrations(
        engine
    )


def run_migrations() -> None:
    """
    Bring the configured database to the latest known schema.
    """
    engine = (
        create_migration_engine()
    )

    lock_connection = None

    try:
        lock_connection = (
            engine.connect()
        )

        lock_connection.execute(
            text(
                """
                SELECT pg_advisory_lock(
                    :namespace,
                    :lock_id
                )
                """
            ),
            {
                "namespace": MIGRATION_LOCK_NAMESPACE,
                "lock_id": MIGRATION_LOCK_ID,
            },
        )

        ensure_migration_table(
            engine
        )

        migrations = (
            discover_migrations()
        )

        completed = (
            applied_migrations(
                engine
            )
        )

        completed = (
            bootstrap_or_adopt_baseline(
                engine,
                migrations,
                completed,
            )
        )

        pending = [
            migration
            for migration
            in migrations
            if migration.name
            not in completed
        ]

        if not pending:
            logger.info(
                "Database schema is current."
            )

            return

        for migration in pending:
            apply_migration(
                engine,
                migration,
            )

        logger.info(
            "Applied %d migration%s.",
            len(
                pending
            ),
            (
                ""
                if len(
                    pending
                )
                == 1
                else "s"
            ),
        )

    finally:
        if lock_connection is not None:
            lock_connection.close()

        engine.dispose()


def main() -> int:
    try:
        run_migrations()

    except Exception:
        logger.exception(
            "Database migration failed."
        )

        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )