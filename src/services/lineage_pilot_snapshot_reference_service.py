"""Bounded read-only reference to a committed SDE pilot PageSnapshot.

This reconstructs a *single-chunk pilot* reference from SDE authority, not
from PGVector and not from the pilot writer's published materialization.
It does not store evidence, grant admission, or verify an entire index.

Caller supplies a trusted SDE authority DB Session in a REPEATABLE READ,
READ ONLY transaction and a read-only snapshot-volume root. The resulting
reference must be rechecked against latest snapshot AFTER an independent
physical read, because the two databases have no shared atomic transaction.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from sqlalchemy import Uuid, bindparam, text
from sqlalchemy.orm import Session

CANONICALIZATION_VERSION = "sde-pilot-main-text-v1"
MAX_ARTIFACT_BYTES = 1_048_576
MAX_CHUNK_CHARS = 50_000
_SHA256 = re.compile(r"[a-f0-9]{64}\Z")


class PilotSnapshotReferenceError(ValueError):
    """Refuse unverifiable snapshot identity, content or unsafe artifact."""


@dataclass(frozen=True, slots=True)
class PilotSnapshotReference:
    organization_id: uuid.UUID
    source_id: uuid.UUID
    snapshot_id: uuid.UUID
    snapshot_sha256: str
    canonicalization_version: str
    chunk_content_sha256: str
    source_external_id: str
    chunk_external_id: str
    chunk_revision_external_id: str
    index_external_id: str
    collection_name: str


# Two SQL statements must use the SAME caller-supplied repeatable-read
# transaction. Read-only permission is enforced by the caller's DB role.
_PILOT_SOURCE = text("""
SELECT id FROM monitored_pages
WHERE organization_id = :organization_id
  AND id = :source_id
  AND url = :source_url
  AND is_active IS TRUE
""").bindparams(bindparam("organization_id", type_=Uuid(as_uuid=True)), bindparam("source_id", type_=Uuid(as_uuid=True)))
_LATEST_SNAPSHOT = text("""
SELECT id, content_hash, raw_content_path
FROM page_snapshots
WHERE organization_id = :organization_id
  AND monitored_page_id = :source_id
ORDER BY crawled_at DESC, id DESC
LIMIT 1
""").bindparams(bindparam("organization_id", type_=Uuid(as_uuid=True)), bindparam("source_id", type_=Uuid(as_uuid=True)))


def _canonical_text(raw: bytes) -> str:
    """Frozen pilot v1 transform, intentionally independent of writer imports."""
    try:
        content = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise PilotSnapshotReferenceError("Artifact is not valid UTF-8") from exc
    soup = BeautifulSoup(content, "html.parser")
    for tag in soup(["script", "style", "template", "nav", "footer", "header"]):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    extracted = " ".join(main.stripped_strings)
    if not extracted or len(extracted) > MAX_CHUNK_CHARS:
        raise PilotSnapshotReferenceError("Canonical chunk is empty or too large")
    return extracted


def _validated_url(value: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise PilotSnapshotReferenceError("Expected source URL is not canonical")
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError as exc:
        raise PilotSnapshotReferenceError("Expected source URL is malformed") from exc
    if (
        url.scheme != "https"
        or not url.hostname
        or not url.hostname.endswith(".github.io")
        or port not in (None, 443)
        or url.username or url.password or url.query or url.fragment
    ):
        raise PilotSnapshotReferenceError("Only enrolled HTTPS GitHub Pages pilot sources are supported")
    return value


def _artifact_bytes(path: object, *, expected_sha256: str, snapshot_root: Path) -> bytes:
    if not isinstance(path, str) or not path:
        raise PilotSnapshotReferenceError("Missing snapshot artifact path")
    if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
        raise PilotSnapshotReferenceError("Invalid persisted snapshot SHA-256")
    if not isinstance(snapshot_root, Path):
        raise TypeError("snapshot_root must be a Path")
    try:
        root = snapshot_root.resolve(strict=True)
        candidate = Path(path).resolve(strict=True)
        if Path(path).is_symlink():
            raise PilotSnapshotReferenceError("Artifact symlinks are not accepted")
        if not root.is_dir() or not candidate.is_relative_to(root) or not candidate.is_file():
            raise PilotSnapshotReferenceError("Artifact is outside the trusted snapshot volume")
        if candidate.stat().st_size > MAX_ARTIFACT_BYTES:
            raise PilotSnapshotReferenceError("Snapshot artifact exceeds size limit")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(candidate, flags)
        with os.fdopen(fd, "rb") as stream:
            data = stream.read(MAX_ARTIFACT_BYTES + 1)
    except PilotSnapshotReferenceError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise PilotSnapshotReferenceError("Snapshot artifact cannot be read safely") from exc
    if len(data) > MAX_ARTIFACT_BYTES:
        raise PilotSnapshotReferenceError("Snapshot artifact exceeds size limit")
    if not hmac.compare_digest(hashlib.sha256(data).hexdigest(), expected_sha256):
        raise PilotSnapshotReferenceError("Snapshot artifact SHA-256 differs from SDE authority")
    return data


def load_pilot_snapshot_reference(
    db: Session,
    *,
    organization_id: uuid.UUID,
    source_id: uuid.UUID,
    expected_source_url: str,
    expected_snapshot_id: uuid.UUID,
    expected_snapshot_sha256: str,
    snapshot_root: Path,
) -> PilotSnapshotReference:
    """Read committed SDE source/snapshot and derive exact pilot chunk digest.

    This is intentionally NOT a generic RAG chunk reference. No custom
    splitting, embedding normalization, or arbitrary materializer is covered.
    The caller must use a trusted read-only SDE database connection and must
    recheck latest authority after doing the independent PGVector read.
    """
    for name, value in (
        ("organization_id", organization_id),
        ("source_id", source_id),
        ("expected_snapshot_id", expected_snapshot_id),
    ):
        if not isinstance(value, uuid.UUID):
            raise TypeError(f"{name} must be UUID")
    url = _validated_url(expected_source_url)
    if not isinstance(expected_snapshot_sha256, str) or not _SHA256.fullmatch(expected_snapshot_sha256):
        raise PilotSnapshotReferenceError("Expected snapshot SHA-256 is not canonical")
    params = {"organization_id": organization_id, "source_id": source_id, "source_url": url}
    if db.execute(_PILOT_SOURCE, params).one_or_none() is None:
        raise PilotSnapshotReferenceError("Active pilot source identity not found")
    row = db.execute(_LATEST_SNAPSHOT, params).one_or_none()
    if row is None:
        raise PilotSnapshotReferenceError("Pilot source has no committed snapshot")
    snapshot_id, sha256, artifact_path = row
    if str(snapshot_id) not in {str(expected_snapshot_id), expected_snapshot_id.hex}:
        raise PilotSnapshotReferenceError("Expected snapshot is not the latest SDE snapshot")
    if not isinstance(sha256, str) or not hmac.compare_digest(sha256, expected_snapshot_sha256):
        raise PilotSnapshotReferenceError("SDE snapshot hash differs from attested expected hash")
    raw = _artifact_bytes(artifact_path, expected_sha256=sha256, snapshot_root=snapshot_root)
    canonical_text = _canonical_text(raw)
    digest = hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()
    source_external_id = f"sde-pilot-source:{source_id}"
    chunk_external_id = f"sde-pilot-chunk:{source_id}:0"
    index_external_id = f"sde-pilot-index:{source_id}"
    return PilotSnapshotReference(
        organization_id=organization_id,
        source_id=source_id,
        snapshot_id=expected_snapshot_id,
        snapshot_sha256=sha256,
        canonicalization_version=CANONICALIZATION_VERSION,
        chunk_content_sha256=digest,
        source_external_id=source_external_id,
        chunk_external_id=chunk_external_id,
        chunk_revision_external_id=f"{chunk_external_id}:{digest[:16]}",
        index_external_id=index_external_id,
        collection_name=f"sde_pilot_{organization_id.hex}_{source_id.hex}",
    )
