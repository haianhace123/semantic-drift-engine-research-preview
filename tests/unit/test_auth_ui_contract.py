"""Static auth surface checks, independent of database and browser runtime."""

from html.parser import HTMLParser
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "src" / "static"
AUTH_PAGES = ("login", "signup", "forgot-password", "reset-password", "oauth-link")


class _HTMLContracts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids: list[str] = []
        self.classes: list[str] = []
        self.inline_style = False
        self.inline_handlers = False
        self.stylesheet = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "id" in a:
            self.ids.append(a["id"])
        if "class" in a:
            self.classes.extend(a["class"].split())
        if tag == "style" or "style" in a:
            self.inline_style = True
        if any(k.lower().startswith("on") for k, _ in attrs):
            self.inline_handlers = True
        if tag == "link" and a.get("href") == "/dashboard/css/auth.css":
            self.stylesheet = True


def test_all_auth_surfaces_have_one_shared_visual_authority():
    for name in AUTH_PAGES:
        parser = _HTMLContracts()
        parser.feed((STATIC / f"{name}.html").read_text(encoding="utf-8"))
        assert parser.stylesheet, name
        assert "auth-context" in parser.classes, name
        assert "auth-layout" in parser.classes, name
        assert "auth-hero" not in parser.classes, name
        assert not parser.inline_style, name
        assert not parser.inline_handlers, name
        assert len(parser.ids) == len(set(parser.ids)), name


def test_oauth_link_is_unavailable_until_server_supplies_identity():
    markup = (STATIC / "oauth-link.html").read_text(encoding="utf-8")
    parser = _HTMLContracts()
    parser.feed(markup)
    for expected in (
        "oauthLinkCard", "linkEmail", "linkProvider", "confirmLinkBtn",
        "cancelLinkBtn", "error", "success",
    ):
        assert expected in parser.ids
    assert 'id="confirmLinkBtn" class="auth-primary-button" type="button" disabled' in markup
    assert 'role="alert"' in markup
    script = (STATIC / "js" / "auth-pages.js").read_text(encoding="utf-8")
    oauth_handler = script.split("async function setupOAuthLink() {", 1)[1]
    assert "api('/api/auth/oauth/link-status'" in oauth_handler
    assert "emailLabel.textContent = status.email" in oauth_handler
    assert "providerLabel.textContent = providerName" in oauth_handler
    assert "confirmButton.disabled = false" in oauth_handler


def test_recovery_forms_keep_existing_controller_ids():
    for name, form, submit, state in (
        ("forgot-password", "forgotForm", "forgotBtn", "forgotSentPanel"),
        ("reset-password", "resetForm", "resetBtn", "resetInvalidPanel"),
    ):
        parser = _HTMLContracts()
        parser.feed((STATIC / f"{name}.html").read_text(encoding="utf-8"))
        assert {form, submit, state} <= set(parser.ids)
