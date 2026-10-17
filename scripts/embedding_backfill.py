"""
Controlled snapshot embedding backfill.

This operator-facing script is intentionally separate from the persistent
vector worker. It defaults to dry-run mode and only calls the configured
embedding provider when --execute is supplied.

Examples:

    python -m scripts.embedding_backfill

    python -m scripts.embedding_backfill --limit 1 --execute

    python -m scripts.embedding_backfill \
        --snapshot-id 11111111-1111-1111-1111-111111111111 \
        --execute

    python -m scripts.embedding_backfill --include-failed --limit 5
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import or_
from sqlalchemy.orm import Session

from src.core.database import SessionLocal
from src.models.page_snapshot import (
    EMBEDDING_STATUS_COMPLETED,
    EMBEDDING_STATUS_FAILED,
    EMBEDDING_STATUS_PENDING,
    PageSnapshot,
)
from src.services.embedding_service import (
    EmbeddingProviderUnavailableError,
    EmbeddingService,
    EmbeddingServiceError,
    load_snapshot_content,
    normalize_embedding_text,
)


logger = logging.getLogger(
    "semantic_drift.embedding_backfill"
)

DEFAULT_LIMIT = 5
MAX_DRY_RUN_LIMIT = 50
MAX_EXECUTE_BATCH = 10
DEFAULT_MAX_ATTEMPTS = 3
PROVIDER_PROBE_TEXT = (
    "Semantic Drift Engine embedding provider probe."
)


@dataclass
class BackfillStats:
    selected: int = 0
    ready: int = 0
    completed: int = 0
    failed: int = 0
    deferred: int = 0
    provider_unavailable: bool = False

    def as_dict(
        self,
    ) -> dict[str, int | bool]:
        return {
            "selected": self.selected,
            "ready": self.ready,
            "completed": self.completed,
            "failed": self.failed,
            "deferred": self.deferred,
            "provider_unavailable": (
                self.provider_unavailable
            ),
        }


@dataclass(
    frozen=True
)
class ContentPreflight:
    ready: bool
    characters: int = 0
    error: str | None = None

@dataclass(
    frozen=True
)
class ProviderProbe:
    ready: bool
    error: str | None = None

def _parse_snapshot_id(
    value: str,
) -> UUID:
    try:
        return UUID(value)
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid snapshot UUID: {value}"
        ) from exc


def validate_backfill_request(
    *,
    limit: int,
    max_attempts: int,
    execute: bool,
    snapshot_ids: list[UUID] | None,
) -> None:
    if limit < 1:
        raise ValueError(
            "limit must be at least 1."
        )

    if max_attempts < 1:
        raise ValueError(
            "max_attempts must be at least 1."
        )

    if limit > MAX_DRY_RUN_LIMIT:
        raise ValueError(
            "limit cannot exceed "
            f"{MAX_DRY_RUN_LIMIT}."
        )

    requested_count = len(
        snapshot_ids or []
    )

    if (
        execute
        and requested_count
        > MAX_EXECUTE_BATCH
    ):
        raise ValueError(
            "execute mode cannot process "
            "more than "
            f"{MAX_EXECUTE_BATCH} explicit "
            "snapshot IDs at once."
        )

    if (
        execute
        and not requested_count
        and limit > MAX_EXECUTE_BATCH
    ):
        raise ValueError(
            "execute mode cannot process "
            "more than "
            f"{MAX_EXECUTE_BATCH} queue "
            "snapshots at once."
        )


def select_snapshot_ids(
    db: Session,
    *,
    limit: int,
    max_attempts: int,
    include_failed: bool,
    snapshot_ids: list[UUID] | None = None,
) -> list[str]:
    statuses = [
        EMBEDDING_STATUS_PENDING
    ]

    if include_failed:
        statuses.append(
            EMBEDDING_STATUS_FAILED
        )

    query = (
        db.query(
            PageSnapshot.id
        )
        .filter(
            PageSnapshot.embedding_status.in_(
                statuses
            ),
            or_(
                PageSnapshot
                .embedding_attempts
                .is_(
                    None
                ),
                PageSnapshot
                .embedding_attempts
                < max_attempts,
            ),
        )
    )

    if snapshot_ids:
        query = query.filter(
            PageSnapshot.id.in_(
                snapshot_ids
            )
        )

    row_limit = (
        len(
            snapshot_ids
        )
        if snapshot_ids
        else limit
    )

    rows = (
        query.order_by(
            PageSnapshot.crawled_at.asc(),
            PageSnapshot.id.asc(),
        )
        .limit(
            row_limit
        )
        .all()
    )

    return [
        str(
            row[0]
        )
        for row in rows
    ]


def preflight_snapshot_content(
    snapshot: PageSnapshot,
) -> ContentPreflight:
    """
    Verify that a snapshot has usable embedding input
    without mutating lifecycle state or consuming an
    embedding attempt.
    """
    try:
        content = (
            load_snapshot_content(
                snapshot
            )
        )

        normalized = (
            normalize_embedding_text(
                content
            )
        )

    except EmbeddingServiceError as exc:
        return ContentPreflight(
            ready=False,
            error=str(
                exc
            ),
        )

    return ContentPreflight(
        ready=True,
        characters=len(
            normalized
        ),
    )

def probe_embedding_provider(
    embedding_service: EmbeddingService,
) -> ProviderProbe:
    """
    Verify that the configured embedding provider can successfully
    produce a valid embedding before any real snapshot lifecycle is
    mutated.

    The probe deliberately uses generate_embedding() rather than
    embed_snapshot(), so a provider outage does not consume a
    snapshot's embedding retry budget.
    """
    try:
        embedding_service.generate_embedding(
            PROVIDER_PROBE_TEXT
        )

    except EmbeddingProviderUnavailableError as exc:
        return ProviderProbe(
            ready=False,
            error=str(exc),
        )

    return ProviderProbe(
        ready=True
    )

def _log_candidate(
    snapshot: PageSnapshot,
    preflight: ContentPreflight,
) -> None:
    if preflight.ready:
        logger.info(
            "Backfill candidate: "
            "snapshot=%s "
            "status=%s "
            "attempts=%s "
            "crawled_at=%s "
            "content_chars=%s "
            "ready=true",
            snapshot.id,
            snapshot.embedding_status,
            int(
                snapshot.embedding_attempts
                or 0
            ),
            snapshot.crawled_at,
            preflight.characters,
        )

        return

    logger.warning(
        "Backfill candidate: "
        "snapshot=%s "
        "status=%s "
        "attempts=%s "
        "crawled_at=%s "
        "ready=false "
        "content_error=%s",
        snapshot.id,
        snapshot.embedding_status,
        int(
            snapshot.embedding_attempts
            or 0
        ),
        snapshot.crawled_at,
        preflight.error,
    )


def run_backfill(
    *,
    limit: int = DEFAULT_LIMIT,
    max_attempts: int = (
        DEFAULT_MAX_ATTEMPTS
    ),
    include_failed: bool = False,
    snapshot_ids: list[UUID] | None = None,
    execute: bool = False,
    embedding_service: (
        EmbeddingService | None
    ) = None,
) -> BackfillStats:
    validate_backfill_request(
        limit=limit,
        max_attempts=max_attempts,
        execute=execute,
        snapshot_ids=snapshot_ids,
    )

    discovery_db = (
        SessionLocal()
    )

    try:
        selected_ids = (
            select_snapshot_ids(
                discovery_db,
                limit=limit,
                max_attempts=(
                    max_attempts
                ),
                include_failed=(
                    include_failed
                ),
                snapshot_ids=(
                    snapshot_ids
                ),
            )
        )

    finally:
        discovery_db.close()

    stats = BackfillStats(
        selected=len(
            selected_ids
        )
    )

    if not selected_ids:
        logger.info(
            "No eligible snapshots "
            "found for backfill."
        )

        return stats

    if not execute:
        logger.info(
            "DRY RUN: %s snapshot(s) "
            "selected. No embedding "
            "provider calls will be made.",
            len(
                selected_ids
            ),
        )

        db = SessionLocal()

        try:
            snapshots = (
                db.query(
                    PageSnapshot
                )
                .filter(
                    PageSnapshot.id.in_(
                        selected_ids
                    )
                )
                .order_by(
                    PageSnapshot
                    .crawled_at
                    .asc(),
                    PageSnapshot
                    .id
                    .asc(),
                )
                .all()
            )

            for snapshot in snapshots:
                preflight = (
                    preflight_snapshot_content(
                        snapshot
                    )
                )

                _log_candidate(
                    snapshot,
                    preflight,
                )

                if preflight.ready:
                    stats.ready += 1
                else:
                    stats.deferred += 1

        finally:
            db.close()

        stats.deferred += (
            stats.ready
        )

        logger.info(
            "Dry run complete: %s",
            stats.as_dict(),
        )

        return stats

    service = (
        embedding_service
        or EmbeddingService()
    )

    logger.warning(
        "EXECUTE MODE: probing embedding "
        "provider before touching "
        "%s selected snapshot(s).",
        len(
            selected_ids
        ),
    )

    provider_probe = (
        probe_embedding_provider(
            service
        )
    )

    if not provider_probe.ready:
        stats.provider_unavailable = True
        stats.deferred = len(
            selected_ids
        )

        logger.error(
            "Embedding provider probe "
            "failed; controlled backfill "
            "stopped before any snapshot "
            "attempt was consumed: "
            "reason=%s "
            "snapshots_deferred=%s",
            provider_probe.error,
            stats.deferred,
        )

        return stats

    logger.info(
        "Embedding provider probe passed."
    )

    logger.warning(
        "EXECUTE MODE: processing "
        "%s selected snapshot(s).",
        len(
            selected_ids
        ),
    )

    for (
        index,
        snapshot_id,
    ) in enumerate(
        selected_ids
    ):
        db = SessionLocal()

        try:
            snapshot = (
                db.query(
                    PageSnapshot
                )
                .filter(
                    PageSnapshot.id
                    == snapshot_id
                )
                .one_or_none()
            )

            if snapshot is None:
                stats.deferred += 1

                logger.warning(
                    "Snapshot disappeared "
                    "before backfill: %s",
                    snapshot_id,
                )

                continue

            allowed_statuses = {
                EMBEDDING_STATUS_PENDING
            }

            if include_failed:
                allowed_statuses.add(
                    EMBEDDING_STATUS_FAILED
                )

            attempts = int(
                snapshot.embedding_attempts
                or 0
            )

            if (
                snapshot.embedding_status
                not in allowed_statuses
            ):
                stats.deferred += 1

                logger.info(
                    "Snapshot is no longer "
                    "eligible: "
                    "snapshot=%s "
                    "status=%s",
                    snapshot.id,
                    snapshot.embedding_status,
                )

                continue

            if attempts >= max_attempts:
                stats.deferred += 1

                logger.info(
                    "Snapshot reached maximum "
                    "attempts: "
                    "snapshot=%s "
                    "attempts=%s",
                    snapshot.id,
                    attempts,
                )

                continue

            preflight = (
                preflight_snapshot_content(
                    snapshot
                )
            )

            _log_candidate(
                snapshot,
                preflight,
            )

            if not preflight.ready:
                stats.deferred += 1

                logger.warning(
                    "Snapshot skipped before "
                    "provider call; retry budget "
                    "preserved: %s",
                    snapshot.id,
                )

                continue

            stats.ready += 1

            service.embed_snapshot(
                db,
                snapshot,
            )

            db.refresh(
                snapshot
            )

            if (
                snapshot.embedding_status
                == EMBEDDING_STATUS_COMPLETED
            ):
                stats.completed += 1

                logger.info(
                    "Backfill embedding "
                    "completed: "
                    "snapshot=%s "
                    "model=%s",
                    snapshot.id,
                    snapshot.embedding_model,
                )

            else:
                stats.deferred += 1

                logger.warning(
                    "Embedding returned without "
                    "completed status: "
                    "snapshot=%s "
                    "status=%s",
                    snapshot.id,
                    snapshot.embedding_status,
                )

        except (
            EmbeddingProviderUnavailableError
        ) as exc:
            stats.failed += 1

            stats.provider_unavailable = (
                True
            )

            remaining = (
                len(
                    selected_ids
                )
                - index
                - 1
            )

            stats.deferred += (
                remaining
            )

            logger.error(
                "Embedding provider "
                "unavailable; stopping "
                "backfill batch: "
                "snapshot=%s "
                "reason=%s "
                "remaining_deferred=%s",
                snapshot_id,
                exc,
                remaining,
            )

            try:
                db.rollback()
            except Exception:
                logger.exception(
                    "Backfill rollback failed "
                    "after provider outage: %s",
                    snapshot_id,
                )

            break

        except Exception:
            stats.failed += 1

            logger.exception(
                "Snapshot embedding failed "
                "during controlled backfill: %s",
                snapshot_id,
            )

            try:
                db.rollback()
            except Exception:
                logger.exception(
                    "Backfill rollback failed: %s",
                    snapshot_id,
                )

        finally:
            db.close()

    logger.info(
        "Backfill complete: %s",
        stats.as_dict(),
    )

    return stats


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect or execute a small, "
            "controlled snapshot embedding "
            "backfill."
        )
    )

    parser.add_argument(
        "--snapshot-id",
        dest="snapshot_ids",
        action="append",
        type=_parse_snapshot_id,
        default=None,
        help=(
            "Restrict backfill to an exact "
            "snapshot UUID. Repeat the option "
            "for multiple snapshots."
        ),
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=DEFAULT_LIMIT,
        help=(
            "Maximum queue snapshots to "
            "inspect/process "
            f"(default {DEFAULT_LIMIT}; "
            f"dry-run max {MAX_DRY_RUN_LIMIT}; "
            f"execute max {MAX_EXECUTE_BATCH})."
        ),
    )

    parser.add_argument(
        "--max-attempts",
        type=int,
        default=(
            DEFAULT_MAX_ATTEMPTS
        ),
        help=(
            "Only select snapshots below "
            "this embedding-attempt count."
        ),
    )

    parser.add_argument(
        "--include-failed",
        action="store_true",
        help=(
            "Include failed snapshots below "
            "the attempt limit. By default "
            "only pending snapshots are "
            "eligible."
        ),
    )

    parser.add_argument(
        "--execute",
        action="store_true",
        help=(
            "Call the configured embedding "
            "provider and persist results. "
            "Without this flag the command "
            "is always a dry run."
        ),
    )

    return parser


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s "
            "%(levelname)s "
            "%(name)s: "
            "%(message)s"
        ),
    )


def main() -> int:
    configure_logging()

    parser = build_parser()
    args = parser.parse_args()

    try:
        stats = run_backfill(
            limit=args.limit,
            max_attempts=(
                args.max_attempts
            ),
            include_failed=(
                args.include_failed
            ),
            snapshot_ids=(
                args.snapshot_ids
            ),
            execute=args.execute,
        )

    except ValueError as exc:
        parser.error(
            str(
                exc
            )
        )

    except Exception:
        logger.exception(
            "Embedding backfill aborted."
        )

        return 1

    if stats.provider_unavailable:
        return 2

    if stats.failed:
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(
        main()
    )