"""Render the mockup SVGs to PNG with Edge/Chromium and report page errors."""

from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
FILES = ["01-studio.svg", "02-iterate.svg", "03-library.svg", "04-architecture.svg"]


def main() -> int:
    failed = False
    with sync_playwright() as pw:
        browser = None
        for channel in ("msedge", None):
            try:
                browser = pw.chromium.launch(channel=channel) if channel else pw.chromium.launch()
                break
            except Exception as exc:  # noqa: BLE001
                print(f"launch {channel or 'chromium'} failed: {exc}")
                browser = None
        if browser is None:
            print("no browser available")
            return 1
        page = browser.new_page(viewport={"width": 1640, "height": 1040}, device_scale_factor=1)
        messages: list[str] = []
        page.on("console", lambda msg: messages.append(f"{msg.type}: {msg.text}"))
        page.on("pageerror", lambda exc: messages.append(f"pageerror: {exc}"))
        for name in FILES:
            url = (HERE / name).as_uri()
            page.goto(url)
            page.wait_for_timeout(400)
            dims = page.evaluate("() => { const s = document.querySelector('svg'); return s ? [s.getBBox().width, s.getBBox().height, s.getBoundingClientRect().width, s.getBoundingClientRect().height] : null; }")
            out = HERE / name.replace(".svg", ".png")
            page.screenshot(path=str(out), full_page=False)
            issues = [m for m in messages if not m.startswith("log:")]
            print(f"{name}: svg={dims} png={out.name} console_issues={issues}")
            failed = failed or bool(issues) or dims is None
            messages.clear()
        browser.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
