"""Recover failed semantic drift analyses without creating replacement events.

Dry run by default. Enable provider calls explicitly with --execute.
"""

from __future__ import annotations

import argparse
import logging
import signal
import time
import uuid

from src.services.semantic_analysis_recovery_service import (
    recover_event,
    recovery_candidates,
    reconcile_missing_snapshot_pairs,
    missing_snapshot_pair_candidates,
)

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--reconcile-missing", action="store_true",
                        help="Register missing changed snapshot pairs as durable pending events")
    parser.add_argument("--gap-scan-limit", type=int, default=2000,
                        help="Maximum recent new snapshots inspected each cycle (up to 10000)")
    parser.add_argument("--loop", action="store_true", help="Repeat recovery cycles")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--base-retry-seconds", type=int, default=60)
    parser.add_argument("--poll-seconds", type=int, default=30)
    parser.add_argument("--organization-id", type=uuid.UUID)
    parser.add_argument("--event-id", type=uuid.UUID)
    parser.add_argument(
        "--requalify", action="store_true",
        help="One explicit operator retry after fixing a permanent/exhausted failure",
    )
    return parser


def run_cycle(args: argparse.Namespace) -> dict[str, int]:
    outcomes: dict[str, int] = {}
    if args.reconcile_missing:
        if args.execute:
            registered = reconcile_missing_snapshot_pairs(
                limit=args.limit, scan_limit=args.gap_scan_limit,
                organization_id=args.organization_id,
            )
            if registered:
                outcomes["gap_registered"] = len(registered)
        else:
            missing = missing_snapshot_pair_candidates(
                limit=args.limit, scan_limit=args.gap_scan_limit,
                organization_id=args.organization_id,
            )
            if missing:
                outcomes["gap_dry_run"] = len(missing)
    ids = (
        [args.event_id]
        if args.event_id is not None
        else recovery_candidates(
            limit=args.limit, organization_id=args.organization_id,
            max_attempts=args.max_attempts,
            base_retry_seconds=args.base_retry_seconds,
        )
    )
    for event_id in ids:
        if not args.execute:
            logger.info("Due semantic analysis recovery (dry run): event=%s", event_id)
            outcome = "dry_run"
        else:
            result = recover_event(
                event_id,
                organization_id=args.organization_id,
                max_attempts=args.max_attempts,
                base_retry_seconds=args.base_retry_seconds,
                requalify=args.requalify,
            )
            outcome = result.outcome
            logger.info(
                "Semantic recovery event=%s outcome=%s attempts=%s",
                result.event_id, result.outcome, result.attempts,
            )
        outcomes[outcome] = outcomes.get(outcome, 0) + 1
    logger.info("Semantic recovery cycle results=%s", outcomes)
    return outcomes


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO)
    args = build_parser().parse_args(argv)
    if (args.limit < 1 or args.max_attempts < 1
        or args.base_retry_seconds < 0 or args.poll_seconds < 1
        or args.gap_scan_limit < args.limit or args.gap_scan_limit > 10000):
        raise SystemExit("Invalid recovery scheduling arguments.")
    if args.loop and not args.execute:
        raise SystemExit("--loop requires --execute; default is a read-only dry run.")
    if args.reconcile_missing and args.event_id is not None:
        raise SystemExit("--reconcile-missing cannot be combined with --event-id.")
    if args.requalify and (
        not args.execute or args.loop or args.event_id is None
        or args.organization_id is None
    ):
        raise SystemExit(
            "--requalify requires --execute, --event-id and --organization-id "
            "and cannot be used in --loop mode."
        )
    stopped = False

    def stop(_signal, _frame):
        nonlocal stopped
        stopped = True

    if args.loop:
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
    while not stopped:
        run_cycle(args)
        if not args.loop:
            break
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
