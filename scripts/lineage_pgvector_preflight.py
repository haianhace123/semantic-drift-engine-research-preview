"""CLI for read-only PGVector collection and chunk identity diagnostics."""

from __future__ import annotations

import argparse
import json
import uuid
from collections.abc import Sequence

from sqlalchemy.exc import SQLAlchemyError

from src.services.lineage_downstream_connection_service import (
    LineageDownstreamConnectionError,
    create_lineage_downstream_session,
    resolve_lineage_downstream_connection,
)
from src.services.lineage_pgvector_preflight_service import (
    PgVectorPreflightResult,
    inspect_pgvector_causal_target,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only physical preflight for SDE PGVector causal execution."
    )
    parser.add_argument("--organization-id", type=uuid.UUID, required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--index", required=True)
    parser.add_argument("--connection-ref", required=True)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--chunk-id")
    parser.add_argument("--chunk-revision")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if (args.chunk_id is None) != (args.chunk_revision is None):
        result = PgVectorPreflightResult(
            "not_ready", "invalid_arguments",
            "--chunk-id and --chunk-revision must be provided together.",
        )
        print(json.dumps(result.to_dict()))
        return 2

    db = None
    engine = None
    try:
        connection = resolve_lineage_downstream_connection(
            connection_ref=args.connection_ref
        )
        db, engine = create_lineage_downstream_session(connection)
        result = inspect_pgvector_causal_target(
            db,
            organization_id=args.organization_id,
            source_external_id=args.source,
            index_external_id=args.index,
            collection_name=args.collection,
            chunk_id=args.chunk_id,
            chunk_revision_external_id=args.chunk_revision,
        )
    except (LineageDownstreamConnectionError, SQLAlchemyError, TypeError, ValueError):
        # Never include the DSN, credentials, or SQL/driver exception in output.
        result = PgVectorPreflightResult(
            "not_ready", "downstream_unavailable",
            "Unable to inspect the downstream database. Check registry, schema and read permissions.",
        )
    finally:
        if db is not None:
            db.rollback()
            db.close()
        if engine is not None:
            engine.dispose()
    print(json.dumps(result.to_dict()))
    return 0 if result.ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
