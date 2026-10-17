from __future__ import annotations

from unittest.mock import MagicMock

import scripts.migrate as migrate


def test_run_migrations_holds_advisory_lock_for_entire_run(
    monkeypatch,
) -> None:
    events: list[str] = []

    lock_connection = MagicMock()
    lock_connection.execute.side_effect = (
        lambda *args, **kwargs: events.append("lock")
    )
    lock_connection.close.side_effect = (
        lambda: events.append("close")
    )

    engine = MagicMock()
    engine.connect.return_value = lock_connection
    engine.dispose.side_effect = (
        lambda: events.append("dispose")
    )

    monkeypatch.setattr(
        migrate,
        "create_migration_engine",
        lambda: engine,
    )
    monkeypatch.setattr(
        migrate,
        "ensure_migration_table",
        lambda _engine: events.append("ledger"),
    )
    monkeypatch.setattr(
        migrate,
        "discover_migrations",
        lambda: [],
    )
    monkeypatch.setattr(
        migrate,
        "applied_migrations",
        lambda _engine: {migrate.BASELINE_MIGRATION},
    )
    monkeypatch.setattr(
        migrate,
        "bootstrap_or_adopt_baseline",
        lambda _engine, _migrations, completed: completed,
    )

    migrate.run_migrations()

    assert events == [
        "lock",
        "ledger",
        "close",
        "dispose",
    ]

    statement = str(
        lock_connection.execute.call_args.args[0]
    )

    assert "pg_advisory_lock" in statement
    assert (
        lock_connection.execute.call_args.args[1]
        == {
            "namespace": migrate.MIGRATION_LOCK_NAMESPACE,
            "lock_id": migrate.MIGRATION_LOCK_ID,
        }
    )


def test_run_migrations_releases_lock_session_on_failure(
    monkeypatch,
) -> None:
    events: list[str] = []

    lock_connection = MagicMock()
    lock_connection.close.side_effect = (
        lambda: events.append("close")
    )

    engine = MagicMock()
    engine.connect.return_value = lock_connection
    engine.dispose.side_effect = (
        lambda: events.append("dispose")
    )

    monkeypatch.setattr(
        migrate,
        "create_migration_engine",
        lambda: engine,
    )

    def fail_after_lock(_engine) -> None:
        events.append("failure")
        raise RuntimeError("migration failure")

    monkeypatch.setattr(
        migrate,
        "ensure_migration_table",
        fail_after_lock,
    )

    try:
        migrate.run_migrations()
    except RuntimeError as exc:
        assert str(exc) == "migration failure"
    else:
        raise AssertionError(
            "Expected migration failure."
        )

    assert events == [
        "failure",
        "close",
        "dispose",
    ]
