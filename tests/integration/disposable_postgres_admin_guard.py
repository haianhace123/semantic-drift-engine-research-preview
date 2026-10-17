"""Hard stop before PostgreSQL DDL that creates or destroys test databases.

The explicit opt-in is necessary but not sufficient. A test must also use the
fixed, disposable SDE Compose database identity; ordinary development, pilot,
and production database URLs are refused, even when RUN_DB_INTEGRATION_TESTS=1.
The enclosing test harness must independently attest Docker isolation.
"""
from __future__ import annotations

import os
from collections.abc import Mapping


DISPOSABLE_DATABASE_URL = (
    "postgresql://sde_integration:sde_local_test_only@db:5432/sde_integration"
)


def require_disposable_postgres_admin(environment: Mapping[str, str] | None = None) -> None:
    """Fail closed before opening a PostgreSQL connection or running any DDL."""
    env = os.environ if environment is None else environment
    required = {
        "ENVIRONMENT": "test",
        "RUN_DB_INTEGRATION_TESTS": "1",
        "SDE_DISPOSABLE_PG_DDL_APPROVED": "YES",
        "DATABASE_URL": DISPOSABLE_DATABASE_URL,
        "TEST_DATABASE_URL": DISPOSABLE_DATABASE_URL,
    }
    if any(env.get(key) != expected for key, expected in required.items()):
        raise RuntimeError("blocked:postgres_admin_test_requires_disposable_database")
