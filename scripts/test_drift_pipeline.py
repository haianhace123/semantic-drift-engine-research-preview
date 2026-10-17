"""
Controlled semantic drift pipeline smoke test.

This script creates two temporary snapshots for an existing monitored page,
runs the production drift detector against them, verifies that a
SemanticDriftEvent can be persisted, and then removes all test artifacts.

The production crawler is intentionally not modified.

Usage:
    python scripts/test_drift_pipeline.py
"""

from __future__ import annotations

import hashlib
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Project root
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ---------------------------------------------------------------------------
# Application imports
# ---------------------------------------------------------------------------

from src.core.database import SessionLocal
from src.detector.change_detector import check_and_create_drift
from src.models import (
    MonitoredPage,
    PageSnapshot,
    SemanticDriftEvent,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TARGET_PAGE_URL = "https://slack.com/terms-of-service"

TEST_DIRECTORY = (
    PROJECT_ROOT
    / "data"
    / "raw"
    / "_drift_test"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def sha256(content: str) -> str:
    """Return the SHA-256 digest of UTF-8 encoded content."""
    return hashlib.sha256(
        content.encode("utf-8")
    ).hexdigest()


def build_test_content() -> tuple[str, str]:
    """
    Build two highly similar contents.

    The controlled change is intentionally small so the production detector
    follows the minor-change path and does not call the external LLM.
    """
    base = (
        "Temporal Intelligence test snapshot.\n"
        "This is a controlled semantic drift pipeline test.\n"
        "The monitored document contains stable legal and operational "
        "information used for regression testing.\n"
    )

    old_content = base + (
        "Version marker: baseline.\n"
    )

    new_content = base + (
        "Version marker: updated.\n"
    )

    return old_content, new_content


def resolve_target_page(db) -> MonitoredPage:
    """Load the target monitored page by exact URL."""
    page = (
        db.query(MonitoredPage)
        .filter(
            MonitoredPage.url == TARGET_PAGE_URL
        )
        .first()
    )

    if not page:
        raise RuntimeError(
            f"Monitored page not found: {TARGET_PAGE_URL}"
        )

    return page


def persist_snapshot(
    db,
    page: MonitoredPage,
    content: str,
    crawled_at: datetime,
    label: str,
) -> tuple[PageSnapshot, Path]:
    """
    Persist one temporary snapshot and its corresponding file.
    """
    TEST_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    digest = sha256(content)

    file_path = (
        TEST_DIRECTORY
        / f"{label}_{digest[:16]}.txt"
    )

    file_path.write_text(
        content,
        encoding="utf-8",
        newline="",
    )

    snapshot = PageSnapshot(
        monitored_page_id=page.id,
        organization_id=page.organization_id,
        content_hash=digest,
        raw_content=None,
        content_size=len(
            content.encode("utf-8")
        ),
        raw_content_path=str(file_path),
        crawled_at=crawled_at,
    )

    db.add(snapshot)
    db.flush()

    return snapshot, file_path


def cleanup_test_data(
    db,
    snapshot_ids: list[str],
    test_files: list[Path],
) -> None:
    """
    Remove only artifacts created by this smoke test.

    Drift events are removed first because they reference the temporary
    snapshots through foreign keys.
    """
    try:
        if snapshot_ids:
            (
                db.query(SemanticDriftEvent)
                .filter(
                    SemanticDriftEvent.old_snapshot_id.in_(
                        snapshot_ids
                    )
                    |
                    SemanticDriftEvent.new_snapshot_id.in_(
                        snapshot_ids
                    )
                )
                .delete(
                    synchronize_session=False
                )
            )

            (
                db.query(PageSnapshot)
                .filter(
                    PageSnapshot.id.in_(
                        snapshot_ids
                    )
                )
                .delete(
                    synchronize_session=False
                )
            )

        db.commit()

    finally:
        for file_path in test_files:
            try:
                if file_path.exists():
                    file_path.unlink()
            except OSError:
                pass

        try:
            if TEST_DIRECTORY.exists():
                # Remove the directory only when empty.
                TEST_DIRECTORY.rmdir()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Test entry point
# ---------------------------------------------------------------------------

def main() -> int:
    """
    Run the controlled drift pipeline smoke test.

    Returns:
        Process exit code:
            0 = success
            1 = failure
    """
    db = SessionLocal()

    snapshot_ids: list[str] = []
    test_files: list[Path] = []

    try:
        page = resolve_target_page(db)

        print(
            f"[TEST] Target page: {page.url}"
        )
        print(
            f"[TEST] Page ID: {page.id}"
        )
        print(
            f"[TEST] Organization ID: "
            f"{page.organization_id}"
        )

        old_content, new_content = (
            build_test_content()
        )

        now = datetime.now(
            timezone.utc
        )

        old_snapshot, old_file = (
            persist_snapshot(
                db=db,
                page=page,
                content=old_content,
                crawled_at=now,
                label="old",
            )
        )

        new_snapshot, new_file = (
            persist_snapshot(
                db=db,
                page=page,
                content=new_content,
                crawled_at=(
                    now
                    + timedelta(seconds=1)
                ),
                label="new",
            )
        )

        snapshot_ids.extend(
            [
                str(old_snapshot.id),
                str(new_snapshot.id),
            ]
        )

        test_files.extend(
            [
                old_file,
                new_file,
            ]
        )

        # Make snapshots visible to the production detector, which uses
        # its own database session.
        db.commit()

        print(
            f"[TEST] Old snapshot: "
            f"{old_snapshot.id}"
        )
        print(
            f"[TEST] New snapshot: "
            f"{new_snapshot.id}"
        )
        print(
            "[TEST] Running production "
            "drift detector..."
        )

        event = check_and_create_drift(
            str(page.id)
        )

        if not event:
            raise RuntimeError(
                "Drift detector did not create "
                "a SemanticDriftEvent."
            )

        print(
            "[TEST] Drift event created:"
        )
        print(
            f"       Event ID: {event.id}"
        )
        print(
            f"       Page ID: "
            f"{event.monitored_page_id}"
        )
        print(
            f"       Old snapshot: "
            f"{event.old_snapshot_id}"
        )
        print(
            f"       New snapshot: "
            f"{event.new_snapshot_id}"
        )
        print(
            f"       Summary: "
            f"{event.summary_text}"
        )

        # Verify the persisted event from a fresh query before cleanup.
        persisted_event = (
            db.query(SemanticDriftEvent)
            .filter(
                SemanticDriftEvent.id
                == event.id
            )
            .first()
        )

        if not persisted_event:
            raise RuntimeError(
                "Drift event was returned by the detector "
                "but could not be found in the database."
            )

        print(
            "[TEST] Database persistence verified."
        )

        cleanup_test_data(
            db=db,
            snapshot_ids=snapshot_ids,
            test_files=test_files,
        )

        print(
            "[TEST] Temporary test data cleaned up."
        )

        print(
            "[TEST] PASS - drift pipeline is working."
        )

        return 0

    except Exception as exc:
        db.rollback()

        print(
            "[TEST] FAIL - controlled drift test failed."
        )
        print(
            f"[TEST] Error: {exc}"
        )

        try:
            cleanup_test_data(
                db=db,
                snapshot_ids=snapshot_ids,
                test_files=test_files,
            )
        except Exception as cleanup_error:
            print(
                "[TEST] Cleanup failed: "
                f"{cleanup_error}"
            )

        return 1

    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(
        main()
    )