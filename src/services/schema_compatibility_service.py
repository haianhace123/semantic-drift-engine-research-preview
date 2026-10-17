"""Read-only application/database migration compatibility contract.

The manifest packaged in the running binary is the only expected migration
set. A database with missing or unknown ledger versions is not presumed
compatible. In particular, an older binary must not silently use a schema
that was upgraded by a newer release.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import SQLAlchemyError


MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "scripts" / "migrations"


class SchemaCompatibilityError(RuntimeError):
    """The running binary cannot safely use the recorded database schema."""


def expected_migrations(migrations_dir: Path = MIGRATIONS_DIR) -> frozenset[str]:
    if not migrations_dir.is_dir():
        raise SchemaCompatibilityError("Application migration manifest is missing")
    versions = frozenset(path.name for path in migrations_dir.glob("*.sql") if path.is_file())
    if "000_base_schema.sql" not in versions or len(versions) < 4:
        raise SchemaCompatibilityError("Application migration manifest is incomplete")
    return versions


def validate_migration_versions(
    expected: frozenset[str],
    applied: set[str],
) -> None:
    missing = sorted(expected - applied)
    unknown = sorted(applied - expected)
    if missing or unknown:
        # Version names alone are diagnostic; never include connection details.
        raise SchemaCompatibilityError(
            "Database migration ledger is incompatible with this binary. "
            f"Missing: {missing}; unknown: {unknown}. "
            "Run the matching release migrations or restore a compatible binary."
        )


def assert_schema_compatible(
    connection: Connection,
    *,
    migrations_dir: Path = MIGRATIONS_DIR,
) -> None:
    """Fail closed without changing the ledger, schema or connection state."""
    expected = expected_migrations(migrations_dir)
    try:
        applied = {
            str(row[0])
            for row in connection.execute(
                text("SELECT version FROM schema_migrations")
            )
        }
    except SQLAlchemyError as exc:
        raise SchemaCompatibilityError(
            "Cannot read schema_migrations; apply migrations before starting services"
        ) from exc
    validate_migration_versions(expected, applied)
