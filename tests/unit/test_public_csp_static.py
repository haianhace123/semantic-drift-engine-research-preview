"""Security contract for unauthenticated browser surfaces and API boundaries.

The behavior tests exercise the actual app headers and legal resource routes.
"""
from html.parser import HTMLParser
from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "src" / "static"
PUBLIC_ROUTES = (
    "/login.html",
    "/signup.html",
    "/forgot-password.html",
    "/reset-password.html",
    "/oauth-link.html",
    "/privacy.html",
    "/terms.html",
)


class _ActiveContent(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.script_sources: list[str | None] = []
        self.inline_style_count = 0
        self.inline_handler_count = 0
        self.stylesheets: list[str] = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "script":
            self.script_sources.append(values.get("src"))
        if tag == "style" or "style" in values:
            self.inline_style_count += 1
        self.inline_handler_count += sum(k.lower().startswith("on") for k, _ in attrs)
        if tag == "link" and values.get("rel") == "stylesheet":
            self.stylesheets.append(values.get("href", ""))


def test_no_public_browser_entry_requires_inline_javascript():
    for route in PUBLIC_ROUTES:
        parser = _ActiveContent()
        parser.feed((STATIC / route.lstrip("/")).read_text(encoding="utf-8"))
        assert all(src and src.startswith("/dashboard/js/") for src in parser.script_sources), route
        assert parser.inline_handler_count == 0, route
        assert parser.inline_style_count == 0, route


def test_legal_pages_share_local_styles_scripts_and_brand():
    for name in ("privacy.html", "terms.html"):
        markup = (STATIC / name).read_text(encoding="utf-8")
        parser = _ActiveContent()
        parser.feed(markup)
        assert parser.stylesheets == [
            "/dashboard/app/public-fonts.css",
            "/dashboard/css/legal.css",
        ]
        assert parser.script_sources == ["/dashboard/js/legal-pages.js"]
        assert 'src="/dashboard/assets/brand/logo.svg"' in markup
    assert (STATIC / "css" / "legal.css").is_file()
    assert (STATIC / "js" / "legal-pages.js").is_file()
