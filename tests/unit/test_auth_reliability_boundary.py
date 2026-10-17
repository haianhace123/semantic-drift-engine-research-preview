"""Source-level and isolated contracts for SDE auth release readiness."""
from __future__ import annotations

import ast
import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[2]
ROUTES = ROOT / "src/api/auth_routes.py"
CAPABILITIES = ROOT / "src/services/auth_capability_service.py"


def _auth_route_nodes(*names):
    tree = ast.parse(ROUTES.read_text(encoding="utf-8-sig"))
    return [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.Assign))
            and (getattr(n, "name", None) in names or
                 isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names
                 for t in n.targets))]


def _run_rate_limit(client, *, limit=3, window=60):
    namespace = {"settings": types.SimpleNamespace(REDIS_URL="redis://isolated" )}
    exec(compile(ast.Module(body=_auth_route_nodes("redis_rate_limit", "AUTH_RATE_LIMIT_LUA"),
                            type_ignores=[]), str(ROUTES), "exec"), namespace)
    fake_module = types.SimpleNamespace(from_url=lambda *args, **kwargs: client)
    previous = sys.modules.get("redis")
    sys.modules["redis"] = fake_module
    try:
        return namespace["redis_rate_limit"]("sde:test:isolated", limit, window)
    finally:
        if previous is None:
            sys.modules.pop("redis", None)
        else:
            sys.modules["redis"] = previous


@pytest.mark.parametrize("counter,expected", [(1, True), (3, True), (4, False)])
def test_rate_limit_one_atomic_eval_with_ttl_and_close(counter, expected):
    client = Mock()
    client.eval.return_value = [counter, 60]
    allowed, ttl = _run_rate_limit(client)
    assert (allowed, ttl) == (expected, 60)
    client.eval.assert_called_once()
    script, numkeys, key, window = client.eval.call_args.args
    assert (numkeys, key, window) == (1, "sde:test:isolated", 60)
    assert "redis.call('INCR', KEYS[1])" in script
    assert "redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))" in script
    assert "ttl < 0" in script  # repair stranded legacy keys
    client.incr.assert_not_called()
    client.expire.assert_not_called()
    client.close.assert_called_once()


def test_rate_limit_closes_redis_client_on_failure():
    client = Mock()
    client.eval.side_effect = RuntimeError("Redis unavailable")
    with pytest.raises(RuntimeError, match="unavailable"):
        _run_rate_limit(client)
    client.close.assert_called_once()


def test_rate_limit_rejects_zero_window_before_network():
    with pytest.raises(ValueError, match="positive"):
        _run_rate_limit(Mock(), window=0)


def test_ensure_rate_limit_failure_is_explicit_503_not_silent_allow():
    source = ROUTES.read_text(encoding="utf-8-sig")
    segment = source[source.index("def ensure_rate_limit("):source.index("# USER SERIALIZATION")]
    assert 'raise auth_error("AUTH_022", 503)' in segment
    assert "Authentication rate limiter unavailable" in segment
    assert not segment[segment.index("except Exception:"):].strip().endswith("return")


def _capability_namespace(monkeypatch, *, expected=False, status="healthy", configured=True):
    monkeypatch.setenv("SDE_EMAIL_WORKER_EXPECTED", "true" if expected else "false")
    module = ast.parse(CAPABILITIES.read_text(encoding="utf-8-sig"))
    node = next(x for x in module.body if isinstance(x, ast.FunctionDef)
                and x.name == "get_auth_capabilities")
    node.args.kw_defaults = [ast.Constant(value=None) for _ in node.args.kw_defaults]
    ast.fix_missing_locations(node)
    from email.utils import parseaddr
    ns = {"os": os, "parseaddr": parseaddr}
    ns["get_worker_operational_overview"] = lambda db: types.SimpleNamespace(workers=(
        types.SimpleNamespace(worker_kind="email", expected=True, status=status),
    ))
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(CAPABILITIES), "exec"), ns)
    settings = types.SimpleNamespace(GOOGLE_CLIENT_ID="id", GOOGLE_CLIENT_SECRET="secret",
                                     GITHUB_CLIENT_ID="", GITHUB_CLIENT_SECRET="",
                                     RESEND_API_KEY="key" if configured else "",
                                     EMAIL_FROM="SDE <support@sde-test.example.org>")
    return ns["get_auth_capabilities"], settings


@pytest.mark.parametrize("expected,status,configured,ready", [
    (False, "healthy", True, False),
    (True, "unavailable", True, False),
    (True, "degraded", True, False),
    (True, "healthy", False, False),
    (True, "healthy", True, True),
])
def test_email_recovery_truth_is_worker_dependent(monkeypatch, expected, status, configured, ready):
    fn, settings = _capability_namespace(monkeypatch, expected=expected,
                                          status=status, configured=configured)
    result = fn(object(), settings=settings)
    assert result == {"email_recovery": ready, "google": True, "github": False}


def test_password_reset_refuses_unavailable_worker_before_reading_user():
    source = ROUTES.read_text(encoding="utf-8-sig")
    part = source[source.index('def forgot_password('):source.index('# RESET PASSWORD')]
    assert part.index('if not get_auth_capabilities(') < part.index('db.query(')
    assert part.index('raise auth_error("AUTH_021", 503)') < part.index('new_opaque_token()')


def test_browser_handles_503_as_unavailable_not_sent():
    source = (ROOT / 'src/static/js/auth-pages.js').read_text(encoding='utf-8-sig')
    assert "'/api/auth/capabilities'" in source
    assert "payload?.email_recovery === true" in source
    assert "!emailRecoveryReady" in source
    assert "error?.status === 503" in source
    assert "button.dataset.oauthUnavailable === 'true'" in source


def test_public_capabilities_never_return_account_identifier_or_provider_secret():
    source = CAPABILITIES.read_text(encoding='utf-8-sig')
    assert '"email_recovery": bool(ready)' in source
    assert '"google": google' in source
    assert '"github": github' in source
    assert '"email":' not in source
    assert '"secret":' not in source


def test_placeholder_sender_does_not_enable_password_recovery(monkeypatch):
    fn, settings = _capability_namespace(monkeypatch, expected=True,
                                          status="healthy", configured=True)
    settings.EMAIL_FROM = "Semantic Drift Engine <no-reply@example.com>"
    assert fn(object(), settings=settings)["email_recovery"] is False
