"""Source evidence semantics independent of a PostgreSQL connection."""

from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

import pytest

from src.models import MonitoredPage, PageSnapshot, SemanticDriftEvent
from src.services.source_diagnostics_service import read_source_diagnostics


class FakeQuery:
    def __init__(self, *, rows=None, item=None):
        self.rows = rows or []
        self.item = item

    def filter(self, *clauses):
        return self

    def order_by(self, *clauses):
        return self

    def limit(self, amount):
        assert amount == 2
        return self

    def all(self):
        return self.rows

    def one_or_none(self):
        return self.item


class FakeDb:
    def __init__(self, *, exists=True, snapshots=(), event=None):
        self.exists = exists
        self.snapshots = snapshots
        self.event = event
        self.event_queries = 0

    def query(self, model):
        if model is MonitoredPage.id:
            return FakeQuery(item=object() if self.exists else None)
        if model is PageSnapshot:
            return FakeQuery(rows=self.snapshots)
        if model is SemanticDriftEvent:
            self.event_queries += 1
            return FakeQuery(item=self.event)
        raise AssertionError(f"Unexpected read: {model}")


def snapshot(content_hash):
    return SimpleNamespace(
        id=uuid.uuid4(), crawled_at=datetime.now(timezone.utc),
        embedding_status="pending", content_hash=content_hash,
    )


def read(db):
    return read_source_diagnostics(
        db, organization_id=uuid.uuid4(), monitored_page_id=uuid.uuid4(),
    )


def test_missing_source_returns_none_before_reading_evidence():
    db = FakeDb(exists=False)
    assert read(db) is None
    assert db.event_queries == 0


def test_empty_and_single_snapshot_do_not_claim_comparison():
    assert read(FakeDb())["latest_snapshot"] is None
    one = read(FakeDb(snapshots=[snapshot("a" * 64)]))
    assert one["latest_snapshot"]["embedding_status"] == "pending"
    assert one["latest_comparison"] is None


def test_identical_hashes_do_not_fetch_or_invent_semantic_event():
    db = FakeDb(snapshots=[snapshot("a" * 64), snapshot("a" * 64)])
    result = read(db)
    assert result["latest_comparison"]["content_changed"] is False
    assert result["latest_comparison"]["analysis_status"] is None
    assert db.event_queries == 0


def test_changed_pair_without_event_is_not_reported_as_failed_analysis():
    db = FakeDb(snapshots=[snapshot("b" * 64), snapshot("a" * 64)])
    result = read(db)
    assert result["latest_comparison"]["content_changed"] is True
    assert result["latest_comparison"]["event_id"] is None
    assert result["latest_comparison"]["analysis_status"] is None
    assert db.event_queries == 1


@pytest.mark.parametrize("status", ["failed", "pending", "completed"])
def test_exact_pair_surfaces_only_persisted_analysis_state(status):
    event = SimpleNamespace(
        id=uuid.uuid4(), analysis_status=status, vector_analysis_status="pending",
    )
    db = FakeDb(snapshots=[snapshot("b" * 64), snapshot("a" * 64)], event=event)
    result = read(db)
    assert result["latest_comparison"]["event_id"] == str(event.id)
    assert result["latest_comparison"]["analysis_status"] == status
    assert result["latest_comparison"]["vector_analysis_status"] == "pending"
