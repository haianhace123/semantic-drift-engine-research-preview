from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from scripts import migrate


def test_all_checked_in_migrations_have_supported_transaction_boundaries() -> None:
    migrations = migrate.discover_migrations()
    assert migrations
    for migration in migrations:
        prepared = migrate.prepare_transactional_migration_sql(
            migration.read_text(encoding="utf-8")
        )
        assert prepared.strip()
        assert not any(
            line.strip().upper() in {"BEGIN;", "COMMIT;"}
            for line in prepared.splitlines()
        ), migration.name


def test_explicit_wrapper_preserves_sql_body_and_comments() -> None:
    source = """-- setup
BEGIN;
CREATE TABLE example (id INTEGER);
-- inner note
DO $$ BEGIN RAISE NOTICE 'ok'; END $$;
COMMIT;
-- finished
"""
    prepared = migrate.prepare_transactional_migration_sql(source)
    assert "CREATE TABLE example (id INTEGER);" in prepared
    assert "DO $$ BEGIN RAISE NOTICE 'ok'; END $$;" in prepared
    assert "-- setup" in prepared and "-- finished" in prepared
    assert "\nBEGIN;\n" not in prepared
    assert "\nCOMMIT;\n" not in prepared


def test_unwrapped_sql_is_unchanged() -> None:
    source = "CREATE TABLE example (id INTEGER);\n"
    assert migrate.prepare_transactional_migration_sql(source) == source


@pytest.mark.parametrize(
    "source",
    [
        "BEGIN;\nSELECT 1;",
        "SELECT 1;\nCOMMIT;",
        "BEGIN;\nSELECT 1;\nCOMMIT;\nCOMMIT;",
        "SELECT 1;\nBEGIN;\nSELECT 2;\nCOMMIT;",
        "BEGIN;\nCOMMIT;\nSELECT 1;",
    ],
)
def test_partial_or_ambiguous_wrapper_is_rejected(source: str) -> None:
    with pytest.raises(RuntimeError, match="unsupported transaction wrapper"):
        migrate.prepare_transactional_migration_sql(source)


def test_migration_and_ledger_use_single_commit(tmp_path: Path) -> None:
    migration = tmp_path / "100_test.sql"
    migration.write_text("BEGIN;\nCREATE TABLE x (id INT);\nCOMMIT;\n")
    cursor = MagicMock()
    connection = MagicMock()
    connection.cursor.return_value = cursor
    engine = MagicMock()
    engine.raw_connection.return_value = connection

    migrate.apply_migration(engine, migration)

    assert cursor.execute.call_count == 2
    first_sql = cursor.execute.call_args_list[0].args[0]
    assert "CREATE TABLE x" in first_sql
    assert "\nCOMMIT;" not in first_sql
    assert "INSERT INTO schema_migrations" in cursor.execute.call_args_list[1].args[0]
    connection.commit.assert_called_once_with()
    connection.rollback.assert_not_called()
    cursor.close.assert_called_once_with()
    connection.close.assert_called_once_with()


def test_ledger_failure_rolls_back_entire_migration(tmp_path: Path) -> None:
    migration = tmp_path / "101_test.sql"
    migration.write_text("BEGIN;\nCREATE TABLE x (id INT);\nCOMMIT;\n")
    cursor = MagicMock()
    cursor.execute.side_effect = [None, RuntimeError("ledger failed")]
    connection = MagicMock()
    connection.cursor.return_value = cursor
    engine = MagicMock()
    engine.raw_connection.return_value = connection

    with pytest.raises(RuntimeError, match="ledger failed"):
        migrate.apply_migration(engine, migration)

    connection.commit.assert_not_called()
    connection.rollback.assert_called_once_with()
    cursor.close.assert_called_once_with()
    connection.close.assert_called_once_with()
