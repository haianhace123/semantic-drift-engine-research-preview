"""Fail-closed production entrypoint for PostgreSQL-dependent SDE processes.

Usage: python -m scripts.schema_preflight -- COMMAND [ARGS...]
The command is exec'd only after the read-only schema check succeeds.
"""

from __future__ import annotations

import logging
import os
import sys

from sqlalchemy.exc import SQLAlchemyError

from src.core.database import engine
from src.services.schema_compatibility_service import (
    SchemaCompatibilityError,
    assert_schema_compatible,
)

logger = logging.getLogger("semantic_drift.schema_preflight")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--":
        args.pop(0)
    if not args:
        logger.error("Missing process command for schema preflight")
        return 2
    try:
        with engine.connect() as connection:
            assert_schema_compatible(connection)
    except SchemaCompatibilityError as exc:
        # Migration version names are safe diagnostics (not credentials).
        logger.error("Schema preflight refused startup: %s", exc)
        return 1
    except SQLAlchemyError:
        # Raw driver exceptions may carry connection details; do not print them.
        logger.error("Schema preflight could not reach PostgreSQL")
        return 1
    logger.info("Schema preflight passed")
    os.execvp(args[0], args)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
