"""Fail-closed, read-only check for non-maintenance PostgreSQL connections.

This is an operator guard before a stopped-writer knowledge backup, not a
transactional lock: new connections and external writers remain possible.
Report counts only, never SQL statements, client addresses or credentials.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool


STATEMENT = text("""
SELECT
    count(*) FILTER (WHERE pid <> pg_backend_pid() AND backend_type = 'client backend') AS others,
    count(*) FILTER (WHERE pid <> pg_backend_pid() AND backend_type = 'client backend'
                     AND state IS NULL) AS invisible,
    (SELECT rolsuper FROM pg_roles WHERE rolname = current_user)
        OR pg_has_role(current_user, 'pg_read_all_stats', 'member') AS visibility
FROM pg_stat_activity
WHERE datname = current_database()
""")


def inspect(database_url: str) -> dict[str, object]:
    """Use a disposable connection, not the application connection pool."""
    if not database_url:
        raise ValueError("DATABASE_URL must be explicitly provided")
    engine = create_engine(database_url, poolclass=NullPool, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection:
            # Do not write database state, including temporary objects.
            connection.execute(text("SET TRANSACTION READ ONLY"))
            row = connection.execute(STATEMENT).mappings().one()
            other = int(row["others"] or 0)
            invisible = int(row["invisible"] or 0)
            full_visibility = bool(row["visibility"])
        return {
            "mode": "read_only",
            "database_writes": False,
            "backup_writes": False,
            "other_database_connections": other,
            "invisible_session_states": invisible,
            "visibility_sufficient": full_visibility,
            "connection_gate_pass": (other == 0 and invisible == 0 and full_visibility),
            "limits": "Point-in-time observation only; does not block subsequent connections, filesystem writes or other databases.",
        }
    finally:
        engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-quiescent", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = inspect(os.environ.get("DATABASE_URL", ""))
        print(json.dumps(report, sort_keys=True))
        if args.require_quiescent and not report["connection_gate_pass"]:
            return 2
        return 0
    except Exception as exc:
        # Do not print database URLs or exception messages that may contain secrets.
        print(json.dumps({"mode": "read_only", "result": "error", "type": type(exc).__name__,
                          "connection_gate_pass": False}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
