"""Regression contracts for factual pre-auth feedback and password policy."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "src" / "static"


def test_login_does_not_advertise_a_live_authenticated_session():
    page = (STATIC / "login.html").read_text(encoding="utf-8")
    assert "Secure HttpOnly session" not in page
    assert "Protected session" not in page
    assert "auth-form-meta--security" not in page
    assert 'id="rememberMe"' in page
    assert 'href="/terms.html"' in page
    assert 'href="/privacy.html"' in page
    assert "By continuing, you agree" not in page


def test_password_feedback_matches_server_requirements_and_not_strength_claims():
    source = (STATIC / "js" / "auth-pages.js").read_text(encoding="utf-8")
    # Five conditions mirror src.api.auth_routes.validate_password.
    for requirement in (
        "function missingPasswordRequirements(value)",
        "value.length >= 12",
        "/[A-Z]/.test(value)",
        "/[a-z]/.test(value)",
        "/\\d/.test(value)",
        "/[^A-Za-z0-9]/.test(value)",
    ):
        assert requirement in source
    assert "Meets password requirements." in source
    assert "Still needed:" in source
    assert source.count("const missingRequirements = missingPasswordRequirements(password)") == 2
    assert "'Very strong'" not in source
    assert "'Very weak'" not in source
    assert 'id="passwordStrength"' in (STATIC / "signup.html").read_text(encoding="utf-8")
    assert 'id="passwordStrength"' in (STATIC / "reset-password.html").read_text(encoding="utf-8")


def test_linking_warning_is_not_a_success_indicator():
    html = (STATIC / "oauth-link.html").read_text(encoding="utf-8")
    css = (STATIC / "css" / "auth.css").read_text(encoding="utf-8")
    assert 'class="auth-link-advisory"' in html
    assert '.auth-security-note::before' not in css
    assert 'id="confirmLinkBtn"' in html
