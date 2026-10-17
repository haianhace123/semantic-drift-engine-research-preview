"""Safety regression: tests must never clear the shared authentication limiter."""
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_integration_fixtures_never_scan_or_delete_global_rate_limit_keys():
    # A static guard is appropriate here: the forbidden behavior is the
    # dangerous fixture itself, which must be caught without connecting Redis.
    for file in (ROOT / "tests" / "integration").rglob("conftest.py"):
        source = file.read_text(encoding="utf-8")
        assert "sde:rl:*" not in source, file
        assert "flushdb(" not in source.lower(), file
        assert "flushall(" not in source.lower(), file


def test_no_obsolete_global_redis_cleanup_marker():
    marker = "no_redis_rate_limit_state"
    assert marker not in (ROOT / "pytest.ini").read_text(encoding="utf-8")
    for file in (ROOT / "tests" / "integration").rglob("test_*.py"):
        assert marker not in file.read_text(encoding="utf-8"), file
