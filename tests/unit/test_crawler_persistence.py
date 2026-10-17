from __future__ import annotations

from pathlib import Path

from src.crawler import persistence


def test_persist_snapshot_content_writes_exact_bytes(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        persistence.Paths,
        "get_data_dir",
        lambda: tmp_path,
    )

    content = (
        "Semantic drift contains Unicode: "
        "over time - knowledge decay."
    )

    snapshot = (
        persistence.persist_snapshot_content(
            source_name="OpenAI",
            page_type="tos",
            content=content,
        )
    )

    path = Path(
        snapshot.path
    )

    assert path.exists()
    assert path.parent == tmp_path

    assert (
        path.read_text(
            encoding="utf-8"
        )
        == content
    )

    assert (
        snapshot.size_bytes
        == len(
            content.encode(
                "utf-8"
            )
        )
    )

    assert (
        snapshot.size_bytes
        != 64
    )


def test_persist_snapshot_content_never_overwrites_history(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        persistence.Paths,
        "get_data_dir",
        lambda: tmp_path,
    )

    first = (
        persistence.persist_snapshot_content(
            source_name="Slack",
            page_type="tos",
            content="Version one",
        )
    )

    second = (
        persistence.persist_snapshot_content(
            source_name="Slack",
            page_type="tos",
            content="Version two",
        )
    )

    assert (
        first.path
        != second.path
    )

    assert (
        Path(
            first.path
        ).read_text(
            encoding="utf-8"
        )
        == "Version one"
    )

    assert (
        Path(
            second.path
        ).read_text(
            encoding="utf-8"
        )
        == "Version two"
    )


def test_persist_snapshot_content_rejects_empty_content(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(
        persistence.Paths,
        "get_data_dir",
        lambda: tmp_path,
    )

    try:
        persistence.persist_snapshot_content(
            source_name="Google",
            page_type="privacy",
            content="",
        )

    except ValueError as exc:
        assert (
            "cannot be empty"
            in str(exc)
        )

    else:
        raise AssertionError(
            "Empty snapshot content must be rejected."
        )