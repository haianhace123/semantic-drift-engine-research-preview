"""Guard the shipped brand mark and browser icon without requiring a browser."""

from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[2]
STATIC = ROOT / "src" / "static"
ICON_URL = "/dashboard/assets/brand/logo.svg"
AUTH_PAGES = (
    "login.html",
    "signup.html",
    "forgot-password.html",
    "reset-password.html",
    "oauth-link.html",
)


class _Icons(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.icons: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "link" and attributes.get("rel") == "icon":
            assert attributes.get("type") == "image/svg+xml"
            self.icons.append(attributes.get("href", ""))


def test_canonical_svg_is_a_renderable_static_mark() -> None:
    svg = STATIC / "assets" / "brand" / "logo.svg"
    root = ElementTree.fromstring(svg.read_bytes())
    ns = "{http://www.w3.org/2000/svg}"
    assert root.tag == ns + "svg"
    assert root.get("viewBox")
    assert root.find(ns + "path") is not None
    assert root.find(ns + "rect") is not None
    for element in root.iter():
        assert element.tag != ns + "script"
        assert not any(key.lower().endswith("onload") for key in element.attrib)


def test_every_entry_uses_the_same_favicon() -> None:
    for file in (
        ROOT / "frontend" / "index.html",
        *(STATIC / name for name in AUTH_PAGES),
        STATIC / "privacy.html",
        STATIC / "terms.html",
    ):
        parser = _Icons()
        parser.feed(file.read_text(encoding="utf-8"))
        assert parser.icons == [ICON_URL], str(file)


def test_workspace_renders_real_logo_instead_of_css_decoration() -> None:
    tsx = (ROOT / "frontend/src/workspace/TemporalWorkspace.tsx").read_text(encoding="utf-8")
    css = (ROOT / "frontend/src/styles/workspace.css").read_text(encoding="utf-8")
    assert 'src="/dashboard/assets/brand/logo.svg"' in tsx
    assert 'className="workspace-mark"' in tsx
    assert '.workspace-mark::before' not in css
    assert '.workspace-mark::after' not in css


def test_named_virtualenvs_never_enter_docker_build_context() -> None:
    for name in (".dockerignore", ".gitignore"):
        contents = (ROOT / name).read_text(encoding="utf-8")
        assert ".venv*/" in contents, name
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert "**/.venv*/" in dockerignore
    assert "frontend/node_modules/" in dockerignore
    assert "\n.env\n" in dockerignore


def test_every_auth_flow_has_a_visible_mobile_brand_slot() -> None:
    for name in AUTH_PAGES:
        markup = (STATIC / name).read_text(encoding="utf-8")
        assert 'class="auth-mobile-brand"' in markup, name
        # The existing shared CSS hides desktop context on narrow viewports.
        # Its mobile slot must therefore carry the same graphic asset.
        slot = markup.split('class="auth-mobile-brand"', 1)[1]
        assert f'src="{ICON_URL}"' in slot, name


def test_dashboard_static_route_serves_svg_with_image_content_type() -> None:
    from starlette.applications import Starlette
    from starlette.staticfiles import StaticFiles
    from starlette.testclient import TestClient

    app = Starlette()
    app.mount("/dashboard", StaticFiles(directory=str(STATIC)))
    with TestClient(app) as client:
        response = client.get(ICON_URL)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert b"<svg" in response.content
