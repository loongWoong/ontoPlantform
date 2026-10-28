#!/usr/bin/env python3
"""Accessibility gate for the v0.3 console (WP5).

Runs axe-core against the running console and fails on any serious or critical
violation.  The frontend keeps axe-core as a dev dependency; the Python
playwright package launches a local Chromium.

    python scripts/axe_v03.py --base-url http://127.0.0.1:8100 --output axe.json

Requires:
    pip install playwright
    cd frontend && npm install
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAV_LABELS = ["工作台", "本体", "数据", "能力", "应用", "对象实例", "运行", "AI 助手", "设置"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8100")
    parser.add_argument("--axe", default=str(
        ROOT / "frontend" / "node_modules" / "axe-core" / "axe.min.js"))
    parser.add_argument("--output", default=str(ROOT / "docs" / "axe-v0.3.json"))
    args = parser.parse_args()

    axe_path = Path(args.axe)
    if not axe_path.is_file():
        print(f"axe-core not found at {axe_path}; run npm install in frontend/")
        return 2
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed; pip install playwright")
        return 2

    report: dict = {"base_url": args.base_url, "pages": {},
                    "serious_or_critical": 0}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.goto(args.base_url, wait_until="networkidle")
        for index, label in enumerate(NAV_LABELS):
            page.locator("nav .nav-item").nth(index).click(force=True)
            page.wait_for_timeout(500)
            page.add_script_tag(path=str(axe_path))
            result = page.evaluate(
                "async () => await axe.run(document, "
                "{resultTypes: ['violations']})")
            rows = [{
                "id": item["id"], "impact": item["impact"],
                "nodes": len(item["nodes"]),
                "targets": [node["target"] for node in item["nodes"][:3]],
                "help": item["help"],
            } for item in result["violations"]]
            report["pages"][label] = rows
            report["serious_or_critical"] += sum(
                1 for row in rows if row["impact"] in ("serious", "critical"))
        page.locator("nav .nav-item").nth(6).click(force=True)
        page.wait_for_timeout(500)
        page.get_by_role("tab", name="事件与投递", exact=True).click()
        page.wait_for_timeout(700)
        page.add_script_tag(path=str(axe_path))
        result = page.evaluate(
            "async () => await axe.run(document, "
            "{resultTypes: ['violations']})")
        report["pages"]["运行/事件与投递"] = [{
            "id": item["id"], "impact": item["impact"],
            "nodes": len(item["nodes"]),
            "targets": [node["target"] for node in item["nodes"][:3]],
            "help": item["help"],
        } for item in result["violations"]]
        report["serious_or_critical"] += sum(
            1 for row in report["pages"]["运行/事件与投递"]
            if row["impact"] in ("serious", "critical"))
        browser.close()

    output = Path(args.output)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")
    total = sum(len(rows) for rows in report["pages"].values())
    print(f"{len(report['pages'])} views, {total} violations, "
          f"{report['serious_or_critical']} serious/critical")
    print(f"report: {output}")
    return 1 if report["serious_or_critical"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
