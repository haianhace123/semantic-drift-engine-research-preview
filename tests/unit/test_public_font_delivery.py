"""Static public typography and font delivery build contract."""
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "src" / "static"
FONT_URL = "/dashboard/app/public-fonts.css"


class _Styles(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "link" and attrs.get("rel") == "stylesheet":
            self.links.append(attrs.get("href"))


def test_public_pages_load_shared_local_font_before_surface_styles():
    for page, style in (
        ("login.html", "auth.css"),
        ("signup.html", "auth.css"),
        ("forgot-password.html", "auth.css"),
        ("reset-password.html", "auth.css"),
        ("oauth-link.html", "auth.css"),
        ("privacy.html", "legal.css"),
        ("terms.html", "legal.css"),
    ):
        parser = _Styles()
        parser.feed((STATIC / page).read_text(encoding="utf-8"))
        assert parser.links == [FONT_URL, f"/dashboard/css/{style}"], page


def test_static_fonts_and_color_tokens_match_workspace_authority():
    foundation = (ROOT / "frontend/src/styles/foundation.css").read_text(encoding="utf-8")
    auth = (STATIC / "css/auth.css").read_text(encoding="utf-8")
    legal = (STATIC / "css/legal.css").read_text(encoding="utf-8")
    assert '"IBM Plex Sans"' in foundation
    assert '"IBM Plex Sans"' in auth
    assert '"IBM Plex Sans"' in legal
    assert "--auth-accent: #d8e04f" in auth
    assert "--legal-accent: #d8e04f" in legal
    assert "--legal-bg: #090b0d" in legal
    assert "font-size: 16px;" in auth  # Form controls on narrow screens.
    assert "--legal-border: #2b333b" in legal


def test_vite_emits_stable_self_hosted_public_fonts():
    config = (ROOT / "frontend/vite.config.ts").read_text(encoding="utf-8")
    for item in (
        "publicPageFonts()", "@fontsource", "ibm-plex-sans",
        "ibm-plex-mono", 'fileName: "public-fonts.css"',
        'fileName: `fonts/${fileName}`', "process.cwd()",
        "readFileSync", "/dashboard/app/fonts/",
    ):
        assert item in config
    assert "https://" not in config
