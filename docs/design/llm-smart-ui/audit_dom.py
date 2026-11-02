"""DOM-level audit of the rendered SVGs (real browser text metrics)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
FILES = ["01-studio.svg", "02-iterate.svg", "03-library.svg", "04-architecture.svg"]

JS = r"""
() => {
  const svg = document.querySelector('svg');
  const texts = [...svg.querySelectorAll('text')].map((t, i) => {
    const b = t.getBBox();
    return {i, s: t.textContent, x: b.x, y: b.y, w: b.width, h: b.height,
            size: parseFloat(getComputedStyle(t).fontSize), fill: getComputedStyle(t).fill};
  });
  const panels = [...svg.querySelectorAll('[data-panel]')].map(r => {
    const b = r.getBBox();
    return {name: r.dataset.panel, x: b.x, y: b.y, w: b.width, h: b.height};
  });
  const canvas = {w: svg.viewBox.baseVal.width, h: svg.viewBox.baseVal.height};
  return {texts, panels, canvas,
          fonts: {
            yahei: document.fonts.check('12px "Microsoft YaHei"'),
            consolas: document.fonts.check('12px Consolas'),
            simsun: document.fonts.check('12px SimSun'),
            newsreader: document.fonts.check('12px Newsreader')
          }};
}
"""


def overlap(a: dict, b: dict) -> float:
    ix = max(0.0, min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"]))
    iy = max(0.0, min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"]))
    return ix * iy


def contains(panel: dict, text: dict) -> bool:
    return (text["x"] >= panel["x"] - 1 and text["y"] >= panel["y"] - 1
            and text["x"] + text["w"] <= panel["x"] + panel["w"] + 1
            and text["y"] + text["h"] <= panel["y"] + panel["h"] + 1)


def main() -> int:
    failed = False
    with sync_playwright() as pw:
        browser = None
        for channel in ("msedge", None):
            try:
                browser = pw.chromium.launch(channel=channel) if channel else pw.chromium.launch()
                break
            except Exception:  # noqa: BLE001
                browser = None
        if browser is None:
            print("no browser")
            return 1
        page = browser.new_page(viewport={"width": 1640, "height": 1040})
        for name in FILES:
            page.goto((HERE / name).as_uri())
            page.wait_for_timeout(150)
            data = page.evaluate(JS)
            texts, panels, canvas = data["texts"], data["panels"], data["canvas"]
            issues = []
            # 1. canvas bounds
            for t in texts:
                if t["x"] < -2 or t["y"] < -2 or t["x"] + t["w"] > canvas["w"] + 2 or t["y"] + t["h"] > canvas["h"] + 2:
                    issues.append(f"outside canvas: {t['s']!r} {t['x']:.0f},{t['y']:.0f},{t['w']:.0f}x{t['h']:.0f}")
            # 2. text-text overlap (width/height both > 1.5)
            for i in range(len(texts)):
                for j in range(i + 1, len(texts)):
                    a, b = texts[i], texts[j]
                    if overlap(a, b) > 2.0 and min(a["w"], b["w"]) > 1 and min(a["h"], b["h"]) > 1:
                        issues.append(f"text overlap: {a['s']!r} <> {b['s']!r}")
            # 3. containment: every text should sit in a panel, or in the free zones
            for t in texts:
                if any(contains(pn, t) for pn in panels):
                    continue
                cx, cy = t["x"] + t["w"] / 2, t["y"] + t["h"] / 2
                free = cx < 236 or cy < 176 or (name == "03-library.svg" and cy < 245)
                if not free and name != "04-architecture.svg":
                    issues.append(f"not in any panel: {t['s']!r} @({cx:.0f},{cy:.0f})")
            # 4. panel containment (text must not cross panel border)
            for t in texts:
                cx, cy = t["x"] + t["w"] / 2, t["y"] + t["h"] / 2
                host = [pn for pn in panels if contains(pn, t)]
                if not host:
                    continue
            report = {
                "texts": len(texts), "panels": len(panels),
                "fonts": data["fonts"],
                "min_font": min(t["size"] for t in texts),
                "issues": issues,
            }
            print(f"{name}: texts={len(texts)} panels={len(panels)} fonts={data['fonts']} min_font={report['min_font']}")
            for it in issues:
                print("  -", it)
            failed = failed or bool(issues)
            (HERE / (name.replace(".svg", ".dom-audit.json"))).write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        browser.close()
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
