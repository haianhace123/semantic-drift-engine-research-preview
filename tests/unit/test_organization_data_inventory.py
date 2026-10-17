"""Read-only inventory boundaries must never claim cross-tenant completeness."""

import os
from types import SimpleNamespace

import pytest

from scripts.audit_organization_data_lifecycle_readonly import (
    snapshot_file_state,
    snapshot_location,
    MAX_HASH_BYTES,
    table_scope,
)


def test_snapshot_resolver_rejects_untrusted_absolute_and_relative_paths(tmp_path):
    root = tmp_path / "data" / "raw"
    root.mkdir(parents=True)
    inside = root / "customer.txt"
    inside.write_text("customer content", encoding="utf-8")
    assert snapshot_location(str(inside), root) == ("in_snapshot_root", inside)
    assert snapshot_location(None, root) == ("no_file_reference", None)
    assert snapshot_location("data/raw/customer.txt", root) == ("relative_path_unverified", None)
    assert snapshot_location(str(tmp_path / "secret.txt"), root) == ("outside_snapshot_root", None)
    assert snapshot_location(str(root / ".." / "secret.txt"), root) == ("outside_snapshot_root", None)
    assert snapshot_location("/etc/passwd", root) == ("outside_snapshot_root", None)
    assert snapshot_location(r"C:\private\secret", root) == ("outside_snapshot_root", None)
    assert snapshot_location(r"\\server\share\secret", root) == ("outside_snapshot_root", None)
    assert snapshot_location(r"C:drive-relative.txt", root) == ("outside_snapshot_root", None)


def test_snapshot_symlink_cannot_escape_root(tmp_path):
    root = tmp_path / "raw"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("private", encoding="utf-8")
    try:
        (root / "link.txt").symlink_to(secret)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows symlink creation privilege unavailable")
        raise
    assert snapshot_location(str(root / "link.txt"), root)[0] == "outside_snapshot_root"


def test_missing_and_verified_snapshot_aggregate_without_content(tmp_path):
    import hashlib
    root = tmp_path / "raw"
    root.mkdir()
    path = root / "one.txt"
    path.write_bytes(b"example")
    correct = hashlib.sha256(b"example").hexdigest()
    assert snapshot_file_state(str(path), correct, root) == "present_unverified"
    assert snapshot_file_state(str(path), correct, root, verify_hash=True) == "hash_verified"
    assert snapshot_file_state(str(path), "0" * 64, root, verify_hash=True) == "hash_mismatch"
    assert snapshot_file_state(str(root / "missing.txt"), correct, root) == "missing_file"


def test_table_scope_does_not_guess_indirect_organization_ownership():
    root = SimpleNamespace(name="organizations", c={"id": 1}, foreign_keys=[])
    direct = SimpleNamespace(name="page_snapshots", c={"organization_id": 1}, foreign_keys=[])
    indirect = SimpleNamespace(name="consent_logs", c={"user_id": 1}, foreign_keys=[])
    assert table_scope(root)["scope"] == "organization_root"
    assert table_scope(direct)["scope"] == "direct_organization_id"
    assert table_scope(indirect)["scope"] == "indirect_or_global_requires_review"


def test_oversized_snapshot_is_not_read(tmp_path):
    root = tmp_path / "raw"
    root.mkdir()
    file = root / "large.txt"
    with file.open("wb") as stream:
        stream.truncate(MAX_HASH_BYTES + 1)
    assert snapshot_file_state(str(file), None, root, verify_hash=True) == "file_too_large_for_hash_audit"
