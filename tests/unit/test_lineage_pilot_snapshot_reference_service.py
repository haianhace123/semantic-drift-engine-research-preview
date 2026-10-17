from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from src.services.lineage_pilot_snapshot_reference_service import (
    CANONICALIZATION_VERSION,
    PilotSnapshotReferenceError,
    _canonical_text,
    load_pilot_snapshot_reference,
)

ORG = uuid.UUID("edc45ed4-1063-4b0a-8f5e-f0ca34e1d702")
SOURCE = uuid.UUID("f5e25ef6-8d23-4a65-9b74-4101804041f2")
SNAP = uuid.UUID("783140a8-e2af-42ad-8e1b-9f7d90f2e231")
URL = "https://product15-example.github.io/policy/"


@pytest.fixture
def authority(tmp_path: Path):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as con:
        con.execute(text("CREATE TABLE monitored_pages (id TEXT, organization_id TEXT, url TEXT, is_active BOOLEAN)"))
        con.execute(text("CREATE TABLE page_snapshots (id TEXT, organization_id TEXT, monitored_page_id TEXT, crawled_at TEXT, content_hash TEXT, raw_content_path TEXT)"))
        con.execute(text("INSERT INTO monitored_pages VALUES (:id,:org,:url,1)"), {"id": SOURCE.hex, "org": ORG.hex, "url": URL})
    artifact_dir = tmp_path / "data"
    artifact_dir.mkdir()
    html = b"<html><main><h1>Policy</h1><p>Rule v2</p></main><footer>Ignore</footer></html>"
    file = artifact_dir / "artifact.txt"
    file.write_bytes(html)
    sha = hashlib.sha256(html).hexdigest()
    with engine.begin() as con:
        con.execute(text("INSERT INTO page_snapshots VALUES (:id,:org,:source,'2026-10-13',:sha,:path)"), {
            "id": SNAP.hex, "org": ORG.hex, "source": SOURCE.hex, "sha": sha, "path": str(file),
        })
    yield engine, artifact_dir, file, sha
    engine.dispose()


def load(authority, **overrides):
    engine, root, file, sha = authority
    values = {
        "organization_id": ORG, "source_id": SOURCE,
        "expected_source_url": URL, "expected_snapshot_id": SNAP,
        "expected_snapshot_sha256": sha, "snapshot_root": root,
    }
    values.update(overrides)
    with Session(engine) as session:
        return load_pilot_snapshot_reference(session, **values)


def test_exact_snapshot_bound_reference(authority):
    ref = load(authority)
    assert ref.canonicalization_version == CANONICALIZATION_VERSION
    assert ref.chunk_content_sha256 == hashlib.sha256(b"Policy Rule v2").hexdigest()
    assert ref.chunk_revision_external_id == f"sde-pilot-chunk:{SOURCE}:0:{ref.chunk_content_sha256[:16]}"
    assert ref.collection_name == f"sde_pilot_{ORG.hex}_{SOURCE.hex}"
    assert ref.snapshot_sha256 != ref.chunk_content_sha256


@pytest.mark.parametrize("values", [
    {"organization_id": uuid.uuid4()},
    {"source_id": uuid.uuid4()},
    {"expected_source_url": "https://evil.com/"},
    {"expected_source_url": "https://evil.github.io/policy/?secret=a"},
    {"expected_source_url": "http://product15-example.github.io/policy/"},
    {"expected_snapshot_id": uuid.uuid4()},
    {"expected_snapshot_sha256": "0" * 64},
    {"expected_snapshot_sha256": "a"},
])
def test_wrong_authority_values_fail_closed(authority, values):
    with pytest.raises(PilotSnapshotReferenceError):
        load(authority, **values)


def test_latest_snapshot_only(authority):
    engine = authority[0]
    with engine.begin() as con:
        con.execute(text("INSERT INTO page_snapshots VALUES (:id,:org,:source,'2026-10-14',:sha,:path)"), {
            "id": uuid.uuid4().hex, "org": ORG.hex, "source": SOURCE.hex,
            "sha": authority[3], "path": str(authority[2]),
        })
    with pytest.raises(PilotSnapshotReferenceError, match="not the latest"):
        load(authority)


def test_changed_artifact_fails_closed(authority):
    authority[2].write_bytes(b"<main>Attacker changed bytes</main>")
    with pytest.raises(PilotSnapshotReferenceError, match="SHA-256 differs"):
        load(authority)


def test_artifact_outside_allowed_root_refused(authority, tmp_path):
    wrong_root = tmp_path / "other"
    wrong_root.mkdir()
    with pytest.raises(PilotSnapshotReferenceError, match="outside"):
        load(authority, snapshot_root=wrong_root)


def test_symlink_escape_refused(authority, tmp_path):
    engine, root, file, sha = authority
    outside = tmp_path / "secret.txt"
    outside.write_bytes(file.read_bytes())
    symlink_path = root / "linked-artifact.txt"
    try:
        symlink_path.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        if getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symbolic link privilege unavailable")
        raise
    # Point the disposable SQLite snapshot at the link; preserve the original
    # artifact rather than deleting a file that Windows may temporarily lock.
    with engine.begin() as con:
        con.execute(
            text("UPDATE page_snapshots SET raw_content_path = :path WHERE id = :id"),
            {"path": str(symlink_path), "id": SNAP.hex},
        )
    with pytest.raises(PilotSnapshotReferenceError, match="symlinks"):
        load(authority)


def test_oversize_rejected(authority):
    authority[2].write_bytes(b"a" * 1_048_577)
    with pytest.raises(PilotSnapshotReferenceError, match="size limit"):
        load(authority)


def test_non_utf8_rejected(authority):
    from src.services.lineage_pilot_snapshot_reference_service import _canonical_text
    with pytest.raises(PilotSnapshotReferenceError, match="UTF-8"):
        _canonical_text(b"\xff")


def test_pilot_canonicalization_exact():
    assert _canonical_text(b"<body><nav>N</nav><main><b>A</b><p>B</p></main></body>") == "A B"


def test_deactivated_source_is_refused(authority):
    with authority[0].begin() as con:
        con.execute(text("UPDATE monitored_pages SET is_active=0"))
    with pytest.raises(PilotSnapshotReferenceError, match="source identity"):
        load(authority)


def test_missing_artifact_is_refused(authority):
    authority[2].unlink()
    with pytest.raises(PilotSnapshotReferenceError, match="cannot be read"):
        load(authority)


def test_absent_snapshot_is_refused(authority):
    with authority[0].begin() as con:
        con.execute(text("DELETE FROM page_snapshots"))
    with pytest.raises(PilotSnapshotReferenceError, match="no committed snapshot"):
        load(authority)


def test_bad_url_port_fails_closed(authority):
    with pytest.raises(PilotSnapshotReferenceError, match="malformed"):
        load(authority, expected_source_url="https://product15-example.github.io:abc/policy/")


def test_reference_lookup_executes_no_writes(authority):
    from sqlalchemy import event
    witnessed = []
    def assert_read_only(conn, cursor, statement, parameters, context, executemany):
        witnessed.append(statement.lstrip().split(None, 1)[0].upper())
        assert witnessed[-1] == "SELECT"
    event.listen(authority[0], "before_cursor_execute", assert_read_only)
    try:
        load(authority)
    finally:
        event.remove(authority[0], "before_cursor_execute", assert_read_only)
    assert witnessed == ["SELECT", "SELECT"]


def test_public_html_crawler_normalization_produces_readable_reference():
    from src.parser.html_cleaner import HTMLCleaner

    # The crawler persists cleaned text; the read-only reference must accept it
    # without importing an operator that can write physical/authority evidence.
    original_html = "<html><nav>Ignore</nav><main><h1>Policy</h1><p>Rule v2</p></main></html>"
    stored_artifact = HTMLCleaner.clean(original_html).encode("utf-8")
    extracted = _canonical_text(stored_artifact)
    assert "Policy" in extracted
    assert "Rule v2" in extracted
