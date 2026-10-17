"""Source-level guardrails for account/UI contracts.

These do not replace browser and isolated API integration acceptance.
"""
from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[2]


def source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_oauth_only_identity_is_an_explicit_authenticated_contract():
    server = source("src/api/auth_routes.py")
    client = source("frontend/src/api/auth.ts")
    account = source("frontend/src/account/AccountSecurity.tsx")
    assert '"password_login_enabled":' in server
    assert "bool(user.password_hash)" in server
    assert "password_login_enabled?: boolean;" in client
    assert "user.password_login_enabled !== false" in account
    assert "{passwordLoginEnabled ? (" in account
    assert "no password credential" in account
    ast.parse(server)


def test_account_export_url_is_not_revoked_during_download_click():
    account = source("frontend/src/account/AccountSecurity.tsx")
    assert "document.body.appendChild(anchor)" in account
    assert "window.setTimeout(() => URL.revokeObjectURL(url), 10_000)" in account


def test_sources_discard_stale_inventory_when_request_fails():
    sources = source("frontend/src/workspace/SourceMonitoring.tsx")
    assert "setPage(null);\n        setProblem(errorMessage(error))" in sources


def test_account_popover_uses_disclosure_not_incomplete_aria_menu():
    account = source("frontend/src/account/AccountMenu.tsx")
    assert 'role="menu"' not in account
    assert 'role="menuitem"' not in account
    assert 'ref={triggerRef}' in account
    assert "triggerRef.current?.focus()" in account
    smoke = source("scripts/smoke_workspace_navigation.py")
    assert 'menu.get_by_role("button", name="Sign out")' in smoke


def test_workspace_updates_browser_title_on_view_change():
    workspace = source("frontend/src/workspace/TemporalWorkspace.tsx")
    assert "document.title = `${viewTitle} · Semantic Drift Engine`;" in workspace
    assert "}, [route.view]);" in workspace


def test_alert_errors_use_alert_semantics():
    alerts = source("frontend/src/alerts/AlertOperations.tsx")
    assert 'className="delivery-ledger-error"' in alerts
    assert 'role="alert"' in alerts


def test_auth_colors_align_with_workspace_tokens():
    auth = source("src/static/css/auth.css")
    foundation = source("frontend/src/styles/foundation.css")
    for a,b in (("--auth-bg", "--canvas"),
                ("--auth-surface", "--surface"),
                ("--auth-accent", "--temporal"),
                ("--auth-text", "--text")):
        def token(source_text, name):
            import re
            match = re.search(r"^\s*"+re.escape(name)+r":\s*(#[0-9a-fA-F]+);", source_text, re.M)
            assert match, name
            return match.group(1).lower()
        assert token(auth, a) == token(foundation, b)
