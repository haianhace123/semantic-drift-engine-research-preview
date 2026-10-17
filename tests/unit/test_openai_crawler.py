from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.crawler.http_transport import CrawlerNetworkError
from src.crawler.openai_crawler import OpenAICrawler
from src.crawler.persistence import SnapshotFile


def _crawler() -> OpenAICrawler:
    return OpenAICrawler(
        organization_id=uuid.uuid4(),
    )


def test_fetch_html_delegates_to_hardened_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crawler = _crawler()
    fetch = MagicMock(
        return_value="<html>terms</html>"
    )

    monkeypatch.setattr(
        "src.crawler.openai_crawler.fetch_crawler_html",
        fetch,
    )

    result = crawler._fetch_html()

    assert result == "<html>terms</html>"

    fetch.assert_called_once_with(
        crawler.url,
        headers=crawler.headers,
        timeout_seconds=30.0,
        allowed_hosts={"openai.com"},
    )


def test_fetch_html_returns_none_on_transport_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    crawler = _crawler()
    fetch = MagicMock(
        side_effect=CrawlerNetworkError(
            "network failed"
        )
    )

    monkeypatch.setattr(
        "src.crawler.openai_crawler.fetch_crawler_html",
        fetch,
    )

    assert crawler._fetch_html() is None

    fetch.assert_called_once_with(
        crawler.url,
        headers=crawler.headers,
        timeout_seconds=30.0,
        allowed_hosts={"openai.com"},
    )


def test_crawl_removes_snapshot_when_database_persistence_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    crawler = _crawler()
    snapshot_path = tmp_path / "snapshot.txt"
    snapshot_path.write_text(
        "snapshot",
        encoding="utf-8",
    )
    snapshot_file = SnapshotFile(
        path=str(snapshot_path),
        size_bytes=snapshot_path.stat().st_size,
    )

    monkeypatch.setattr(
        crawler,
        "_fetch_html",
        lambda: "<html>" + ("content " * 30) + "</html>",
    )
    monkeypatch.setattr(
        "src.crawler.openai_crawler.persist_snapshot_content",
        lambda **kwargs: snapshot_file,
    )
    monkeypatch.setattr(
        crawler,
        "_save_to_database",
        lambda **kwargs: None,
    )

    assert crawler.crawl() is None
    assert not snapshot_path.exists()


def test_crawl_preserves_snapshot_when_detector_fails_after_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    crawler = _crawler()
    snapshot_path = tmp_path / "snapshot.txt"
    snapshot_path.write_text(
        "snapshot",
        encoding="utf-8",
    )
    snapshot_file = SnapshotFile(
        path=str(snapshot_path),
        size_bytes=snapshot_path.stat().st_size,
    )

    persisted_snapshot = MagicMock()
    persisted_snapshot.id = uuid.uuid4()
    persisted_snapshot.monitored_page_id = uuid.uuid4()

    monkeypatch.setattr(
        crawler,
        "_fetch_html",
        lambda: "<html>" + ("content " * 30) + "</html>",
    )
    monkeypatch.setattr(
        "src.crawler.openai_crawler.persist_snapshot_content",
        lambda **kwargs: snapshot_file,
    )
    monkeypatch.setattr(
        crawler,
        "_save_to_database",
        lambda **kwargs: persisted_snapshot,
    )
    monkeypatch.setattr(
        "src.crawler.openai_crawler.check_and_create_drift",
        MagicMock(
            side_effect=RuntimeError(
                "detector failed"
            )
        ),
    )

    assert crawler.crawl() is None
    assert snapshot_path.exists()
