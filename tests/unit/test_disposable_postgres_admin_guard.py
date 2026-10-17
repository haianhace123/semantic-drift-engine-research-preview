"""Safety contract for opt-in PostgreSQL integration tests with admin DDL."""
from __future__ import annotations

import pytest

from tests.integration.disposable_postgres_admin_guard import (
    DISPOSABLE_DATABASE_URL,
    require_disposable_postgres_admin,
)


@pytest.fixture
def approved_environment() -> dict[str, str]:
    return {
        "ENVIRONMENT": "test",
        "RUN_DB_INTEGRATION_TESTS": "1",
        "SDE_DISPOSABLE_PG_DDL_APPROVED": "YES",
        "DATABASE_URL": DISPOSABLE_DATABASE_URL,
        "TEST_DATABASE_URL": DISPOSABLE_DATABASE_URL,
    }


def test_explicit_isolated_postgres_admin_environment_is_accepted(approved_environment):
    require_disposable_postgres_admin(approved_environment)


@pytest.mark.parametrize(
    "key,bad_value",
    [
        ("ENVIRONMENT", "production"),
        ("RUN_DB_INTEGRATION_TESTS", "0"),
        ("SDE_DISPOSABLE_PG_DDL_APPROVED", "NO"),
        ("DATABASE_URL", "postgresql://postgres:postgres@db:5432/semantic_drift"),
        ("TEST_DATABASE_URL", "postgresql://postgres:postgres@db:5432/semantic_drift"),
        ("DATABASE_URL", "postgresql://sde_integration:sde_local_test_only@remote.example:5432/sde_integration"),
        ("DATABASE_URL", "sqlite:///:memory:"),
    ],
)
def test_wrong_test_environment_fails_closed(approved_environment, key, bad_value):
    approved_environment[key] = bad_value
    with pytest.raises(RuntimeError, match="blocked:postgres_admin_test"):
        require_disposable_postgres_admin(approved_environment)


@pytest.mark.parametrize(
    "key",
    ["ENVIRONMENT", "RUN_DB_INTEGRATION_TESTS", "SDE_DISPOSABLE_PG_DDL_APPROVED", "DATABASE_URL", "TEST_DATABASE_URL"],
)
def test_missing_required_environment_fails_closed(approved_environment, key):
    approved_environment.pop(key)
    with pytest.raises(RuntimeError, match="blocked:postgres_admin_test"):
        require_disposable_postgres_admin(approved_environment)


def test_unrelated_env_does_not_override_safety(approved_environment):
    approved_environment["RUN_DR_ACCEPTANCE_TESTS"] = "1"
    approved_environment.pop("SDE_DISPOSABLE_PG_DDL_APPROVED")
    with pytest.raises(RuntimeError, match="blocked:postgres_admin_test"):
        require_disposable_postgres_admin(approved_environment)
