"""Public auth typography / keyboard baseline, independent of frontend build."""
from __future__ import annotations

import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSS = ROOT / "src/static/css/auth.css"
AUTH_PAGES = ("login.html", "signup.html", "forgot-password.html", "reset-password.html", "oauth-link.html")


def _luminance(color: str) -> float:
    numbers = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [n / 12.92 if n <= 0.04045 else ((n + 0.055) / 1.055) ** 2.4 for n in numbers]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(a: str, b: str) -> float:
    a, b = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def test_muted_auth_text_maintains_wcag_aa_contrast_on_raised_surface():
    css = CSS.read_text(encoding="utf-8")
    muted = re.search(r"--auth-text-muted:\s*(#[0-9a-fA-F]{6})", css).group(1)
    raised = re.search(r"--auth-surface-raised:\s*(#[0-9a-fA-F]{6})", css).group(1)
    assert _contrast(muted, raised) >= 4.5


def test_auth_uses_only_locally_served_plex_font_weights():
    css = CSS.read_text(encoding="utf-8")
    weights = {int(w) for w in re.findall(r"font-weight:\s*(\d+)\s*;", css)}
    assert weights <= {400, 500, 600}, weights


def test_operational_auth_labels_are_not_below_12_pixels():
    css = CSS.read_text(encoding="utf-8")
    declared = [int(w) for w in re.findall(r"font-size:\s*(\d+)px\s*;", css)]
    assert declared
    assert all(w == 0 or w >= 12 for w in declared)


def test_auth_interactive_elements_have_keyboard_focus_indicator():
    css = CSS.read_text(encoding="utf-8")
    assert "body.auth-page :is(a, button, input):focus-visible" in css
    assert "outline: 2px solid var(--auth-accent-hover)" in css


def test_public_auth_pages_use_the_same_authoritative_stylesheet():
    for name in AUTH_PAGES:
        html = (ROOT / "src/static" / name).read_text(encoding="utf-8")
        assert '/dashboard/css/auth.css' in html, name
        assert '/dashboard/app/public-fonts.css' in html, name
