"""Read-only schema gate for production process admission."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine, text

from src.services.schema_compatibility_service import (
    SchemaCompatibilityError,
    assert_schema_compatible,
    expected_migrations,
    validate_migration_versions,
)


def _manifest(path: Path) -> frozenset[str]:
    for name in (
        "000_base_schema.sql",
        "001_auth_production.sql",
        "002_semantic_drift_production.sql",
        "003_entity_tenant_isolation.sql",
    ):
        (path / name).write_text("-- test\n", encoding="utf-8")
    return expected_migrations(path)


def test_checked_in_migration_manifest_is_available() -> None:
    manifest = expected_migrations()
    assert "000_base_schema.sql" in manifest
    assert "047_temporal_lineage_invalidation_lifecycle.sql" in manifest


def test_missing_migration_manifest_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(SchemaCompatibilityError, match="incomplete"):
        expected_migrations(tmp_path)


def test_compatible_ledger_succeeds(tmp_path: Path) -> None:
    expected = _manifest(tmp_path)
    validate_migration_versions(expected, set(expected))


@pytest.mark.parametrize(
    ("alteration", "message"),
    [
        ("missing", "Missing"),
        ("unknown", "unknown"),
    ],
)
def test_missing_and_newer_migrations_rejected(
    tmp_path: Path, alteration: str, message: str
) -> None:
    expected = _manifest(tmp_path)
    recorded = set(expected)
    if alteration == "missing":
        recorded.remove("003_entity_tenant_isolation.sql")
    else:
        recorded.add("048_future_schema.sql")
    with pytest.raises(SchemaCompatibilityError, match=message):
        validate_migration_versions(expected, recorded)


def test_read_only_check_does_not_mutate_ledger(tmp_path: Path) -> None:
    expected = _manifest(tmp_path)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY)"))
        for version in expected:
            conn.execute(text("INSERT INTO schema_migrations (version) VALUES (:v)"), {"v": version})
    with engine.connect() as conn:
        assert_schema_compatible(conn, migrations_dir=tmp_path)
        assert conn.execute(text("SELECT COUNT(*) FROM schema_migrations")).scalar_one() == 4
    engine.dispose()


def test_missing_ledger_fails_closed(tmp_path: Path) -> None:
    _manifest(tmp_path)
    engine = create_engine("sqlite+pysqlite:///:memory:")
    try:
        with engine.connect() as conn:
            with pytest.raises(SchemaCompatibilityError, match="Cannot read"):
                assert_schema_compatible(conn, migrations_dir=tmp_path)
    finally:
        engine.dispose()


def test_preflight_never_runs_command_on_incompatible_schema(monkeypatch) -> None:
    from scripts import schema_preflight

    engine = MagicMock()
    monkeypatch.setattr(schema_preflight, "engine", engine)
    monkeypatch.setattr(
        schema_preflight,
        "assert_schema_compatible",
        lambda _connection: (_ for _ in ()).throw(SchemaCompatibilityError("bad")),
    )
    executed = []
    monkeypatch.setattr(schema_preflight.os, "execvp", lambda *args: executed.append(args))
    assert schema_preflight.main(["--", "python", "-m", "scripts.vector_worker"]) == 1
    assert executed == []


def test_preflight_execs_unmodified_command_when_compatible(monkeypatch) -> None:
    from scripts import schema_preflight

    engine = MagicMock()
    monkeypatch.setattr(schema_preflight, "engine", engine)
    monkeypatch.setattr(schema_preflight, "assert_schema_compatible", lambda _conn: None)
    executed = []
    monkeypatch.setattr(schema_preflight.os, "execvp", lambda *args: executed.append(args))
    assert schema_preflight.main(["--", "/bin/sh", "-c", "echo ok"]) == 0
    assert executed == [("/bin/sh", ["/bin/sh", "-c", "echo ok"])]


def test_preflight_rejects_empty_command() -> None:
    from scripts import schema_preflight

    assert schema_preflight.main(["--"]) == 2
