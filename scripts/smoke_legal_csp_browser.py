"""Browser verification of restrictive script CSP on legal and login pages.

No credentials or database writes. Screenshots are saved to a local /tmp path.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

from scripts.smoke_auth_browser import wait_for_api


PAGES = ("/login.html", "/terms.html", "/privacy.html")
VIEWPORTS = (("desktop", 1440, 900), ("mobile", 390, 844))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--output-dir", default="/tmp/sde-legal-csp-smoke")
    args = parser.parse_args()
    wait_for_api(args.base_url)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    records = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for route in PAGES:
                for label, width, height in VIEWPORTS:
                    context = browser.new_context(
                        viewport={"width": width, "height": height},
                        reduced_motion="reduce",
                    )
                    page = context.new_page()
                    page.add_init_script("""
                        window.__sdeCspViolations = [];
                        document.addEventListener('securitypolicyviolation', (event) => {
                            window.__sdeCspViolations.push({
                                directive: event.violatedDirective,
                                blocked: event.blockedURI,
                            });
                        });
                    """)
                    errors = []
                    page.on("pageerror", lambda error: errors.append(f"JS: {error}"))
                    page.on("console", lambda message: (
                        errors.append(f"CSP: {message.text}")
                        if message.type == "error" and "Content Security Policy" in message.text
                        else None
                    ))
                    page.on("response", lambda response: (
                        errors.append(f"Asset HTTP {response.status}: {response.url}")
                        if response.status >= 400 and "/dashboard/" in response.url
                        else None
                    ))
                    try:
                        response = page.goto(
                            args.base_url.rstrip("/") + route,
                            wait_until="networkidle",
                            timeout=20000,
                        )
                        if not response or response.status != 200:
                            errors.append("Document did not return HTTP 200")
                        if response and "script-src 'self'" not in response.headers.get("content-security-policy", ""):
                            errors.append("Enforced script CSP not observed")
                        violations = page.evaluate("window.__sdeCspViolations || []")
                        for violation in violations:
                            errors.append(f"CSP violation: {violation}")
                        if route != "/login.html":
                            logo = page.locator(".legal-brand img")
                            if not logo.is_visible() or not logo.evaluate("e => e.complete && e.naturalWidth > 0"):
                                errors.append("Legal brand mark not rendered")
                            if page.locator("h1").count() != 1:
                                errors.append("Missing legal document heading")
                            if not page.locator("#backLink").is_visible():
                                errors.append("Back navigation missing")
                        overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
                        if overflow > 2:
                            errors.append(f"Horizontal overflow: {overflow}")
                        screenshot = f"{route.strip('/').replace('.html', '')}-{label}.png"
                        page.screenshot(path=str(output / screenshot), full_page=True)
                    except Exception as exc:
                        errors.append(f"Browser failure: {type(exc).__name__}: {exc}")
                    records.append({"page": route, "viewport": label, "errors": errors})
                    print(f"{'PASS' if not errors else 'FAIL'} {route} {label}: {'; '.join(errors) if errors else 'CSP/assets/layout'}")
                    context.close()
        finally:
            browser.close()
    (output / "summary.json").write_text(json.dumps(records, indent=2), encoding="utf-8")
    return 1 if any(entry["errors"] for entry in records) else 0


if __name__ == "__main__":
    raise SystemExit(main())
