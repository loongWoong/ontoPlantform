"""Generate the Smart UI Studio mockups (SVG) with a deterministic geometry audit.

Run:  python docs/design/llm-smart-ui/build_mockups.py
Output: 01-studio.svg, 02-iterate.svg, 03-library.svg, 04-architecture.svg
The audit fails (exit 1) on: text overflow (declared max width / canvas),
top-level panel overlap, or out-of-canvas geometry.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import ImageFont

OUT = Path(__file__).resolve().parent
CANVAS = (1600, 1000)
ARCH_CANVAS = (1600, 760)

SERIF = "'Newsreader','Songti SC','SimSun',Georgia,serif"
SANS = "'Microsoft YaHei','Segoe UI',sans-serif"
MONO = "'Cascadia Mono',Consolas,monospace"

T = dict(
    bg="#f7f6f3", surface="#ffffff", surface2="#fbfaf8", sunken="#f1efe9",
    ink="#1a1814", ink2="#4b473f", muted="#6b665c", faint="#736d61",
    line="#e6e2d9", line2="#d5d0c4", linesoft="#efede7",
    redbg="#fbeceb", redink="#98372e", amberbg="#f8f1dc", amberink="#86610f",
    greenbg="#edf2ea", greenink="#3d6744", bluebg="#eaf1f4", blueink="#2d657d",
    clay="#9a5944", sage="#5a6d57", ochre="#89692f",
)

FONT_FILES = {
    ("sans", 400): r"C:\Windows\Fonts\msyh.ttc",
    ("sans", 600): r"C:\Windows\Fonts\msyhbd.ttc",
    ("serif", 400): r"C:\Windows\Fonts\simsun.ttc",
    ("serif", 500): r"C:\Windows\Fonts\simsun.ttc",
    ("mono", 400): r"C:\Windows\Fonts\consola.ttf",
    ("mono", 600): r"C:\Windows\Fonts\consolab.ttf",
}

_cache: dict = {}


def font_for(kind: str, size: float, weight: int):
    key = (kind, round(size, 1), 600 if weight >= 600 else 400)
    if key not in _cache:
        path = FONT_FILES.get((kind, 600 if weight >= 600 else 400)) or FONT_FILES[(kind, 400)]
        _cache[key] = ImageFont.truetype(path, max(1, int(round(size))))
    return _cache[key]


def esc(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def n(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".") if isinstance(value, float) else str(value)


class Painter:
    def __init__(self, width: int, height: int):
        self.w, self.h = width, height
        self.parts: list[str] = []
        self.panels: list[tuple[str, float, float, float, float]] = []
        self.errors: list[str] = []
        self.texts = 0

    # ----- primitives -------------------------------------------------
    def rect(self, x, y, w, h, fill="none", stroke="none", sw=1.0, rx=0.0,
             dash: str | None = None, opacity=None, panel: str | None = None):
        attrs = [f'x="{n(x)}"', f'y="{n(y)}"', f'width="{n(w)}"', f'height="{n(h)}"']
        if fill != "none":
            attrs.append(f'fill="{fill}"')
        if stroke != "none":
            attrs += [f'stroke="{stroke}"', f'stroke-width="{n(sw)}"']
        if rx:
            attrs.append(f'rx="{n(rx)}"')
        if dash:
            attrs.append(f'stroke-dasharray="{dash}"')
        if opacity is not None:
            attrs.append(f'opacity="{opacity}"')
        if panel:
            attrs.append(f'data-panel="{panel}"')
        self.parts.append("<rect " + " ".join(attrs) + "/>")
        if panel:
            self.panels.append((panel, x, y, w, h))
        if x < -0.5 or y < -0.5 or x + w > self.w + 0.5 or y + h > self.h + 0.5:
            self.errors.append(f"rect {panel or ''} out of canvas: ({x},{y},{w},{h})")

    def line(self, x1, y1, x2, y2, stroke=T["line"], sw=1.0, dash=None, cap="butt"):
        attrs = [f'x1="{n(x1)}"', f'y1="{n(y1)}"', f'x2="{n(x2)}"', f'y2="{n(y2)}"',
                 f'stroke="{stroke}"', f'stroke-width="{n(sw)}"']
        if dash:
            attrs.append(f'stroke-dasharray="{dash}"')
        if cap != "butt":
            attrs.append(f'stroke-linecap="{cap}"')
        self.parts.append("<line " + " ".join(attrs) + "/>")

    def path(self, d: str, stroke="none", fill="none", sw=1.0, dash=None, opacity=None, cap="butt"):
        attrs = [f'd="{d}"']
        if fill != "none":
            attrs.append(f'fill="{fill}"')
        if stroke != "none":
            attrs += [f'stroke="{stroke}"', f'stroke-width="{n(sw)}"']
        if dash:
            attrs.append(f'stroke-dasharray="{dash}"')
        if opacity is not None:
            attrs.append(f'opacity="{opacity}"')
        if cap != "butt":
            attrs.append(f'stroke-linecap="{cap}"')
        self.parts.append("<path " + " ".join(attrs) + "/>")

    def circle(self, cx, cy, r, fill="none", stroke="none", sw=1.0, opacity=None):
        attrs = [f'cx="{n(cx)}"', f'cy="{n(cy)}"', f'r="{n(r)}"']
        if fill != "none":
            attrs.append(f'fill="{fill}"')
        if stroke != "none":
            attrs += [f'stroke="{stroke}"', f'stroke-width="{n(sw)}"']
        if opacity is not None:
            attrs.append(f'opacity="{opacity}"')
        self.parts.append("<circle " + " ".join(attrs) + "/>")

    def txt(self, x, y, s, size=11.0, color=T["ink"], kind="sans", weight=400,
            anchor="start", ls=0.0, maxw=None, italic=False, opacity=None):
        self.texts += 1
        f = font_for(kind, size, weight)
        width = f.getlength(s) + ls * len(s)
        if width < 0:
            width = f.getlength(s)
        x0 = {"start": x, "middle": x - width / 2, "end": x - width}[anchor]
        if maxw is not None and width > maxw + 1.2:
            self.errors.append(f"text overflow ({width:.1f} > {maxw:.1f}): {s!r} @({x:.0f},{y:.0f})")
        asc, desc = size * 0.80, size * 0.24
        if x0 < -1 or x0 + width > self.w + 1 or y - asc < -1 or y + desc > self.h + 1:
            self.errors.append(f"text out of canvas: {s!r} bbox=({x0:.0f},{y-asc:.0f},{width:.0f})")
        attrs = [f'x="{n(x)}"', f'y="{n(y)}"', f'fill="{color}"',
                 f'font-size="{n(size)}"', f'font-family="{kind_family(kind)}"',
                 f'font-weight="{weight}"']
        if anchor != "start":
            attrs.append(f'text-anchor="{anchor}"')
        if ls:
            attrs.append(f'letter-spacing="{n(ls)}"')
        if italic:
            attrs.append('font-style="italic"')
        if opacity is not None:
            attrs.append(f'opacity="{opacity}"')
        self.parts.append("<text " + " ".join(attrs) + ">" + esc(s) + "</text>")

    def check(self, cx, cy, color=T["greenink"], r=6.0, bg=T["greenbg"]):
        self.circle(cx, cy, r, fill=bg, stroke=color, sw=1.0)
        self.path(f"M{n(cx-2.6)} {n(cy)} L{n(cx-0.6)} {n(cy+2.2)} L{n(cx+3)} {n(cy-2.2)}",
                  stroke=color, sw=1.3, cap="round")

    def badge(self, cx, cy, label, r=6.5, fill=T["clay"]):
        self.circle(cx, cy, r, fill=fill)
        self.txt(cx, cy + 2.6, label, 7.6, "#ffffff", "sans", 600, "middle")

    def pill(self, x, y, w, h, label, size=9.4, fg=T["ink2"], bg=T["sunken"],
             border=None, weight=400, kind="sans"):
        self.rect(x, y, w, h, fill=bg, stroke=border or "none", sw=1.0, rx=h / 2)
        self.txt(x + w / 2, y + h / 2 + size * 0.36, label, size, fg, kind, weight, "middle", maxw=w - 8)

    def tag(self, x, y, w, h, label, tone="plain", size=9.2):
        bg, fg = {
            "ok": (T["greenbg"], T["greenink"]), "warn": (T["amberbg"], T["amberink"]),
            "err": (T["redbg"], T["redink"]), "info": (T["bluebg"], T["blueink"]),
            "plain": (T["sunken"], T["ink2"]),
        }[tone]
        self.rect(x, y, w, h, fill=bg, rx=4)
        self.txt(x + w / 2, y + h / 2 + size * 0.36, label, size, fg, "sans", 600, "middle", maxw=w - 6)

    def button(self, x, y, w, h, label, primary=True, size=10.6):
        if primary:
            self.rect(x, y, w, h, fill=T["ink"], rx=6)
            self.txt(x + w / 2, y + h / 2 + size * 0.36, label, size, "#ffffff", "sans", 500, "middle", maxw=w - 10)
        else:
            self.rect(x, y, w, h, fill=T["surface"], stroke=T["line2"], rx=6)
            self.txt(x + w / 2, y + h / 2 + size * 0.36, label, size, T["ink"], "sans", 500, "middle", maxw=w - 10)

    def finish(self) -> str:
        # panel overlap audit
        for i in range(len(self.panels)):
            name, x, y, w, h = self.panels[i]
            for j in range(i + 1, len(self.panels)):
                name2, x2, y2, w2, h2 = self.panels[j]
                ix = max(0, min(x + w, x2 + w2) - max(x, x2))
                iy = max(0, min(y + h, y2 + h2) - max(y, y2))
                if ix > 1 and iy > 1:
                    self.errors.append(f"panel overlap: {name} / {name2} ({ix:.0f}x{iy:.0f})")
        body = "\n".join(self.parts)
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" '
                f'viewBox="0 0 {self.w} {self.h}" role="img">\n'
                f'<rect width="{self.w}" height="{self.h}" fill="{T["bg"]}"/>\n{body}\n</svg>\n')


def kind_family(kind: str) -> str:
    return {"sans": SANS, "serif": SERIF, "mono": MONO}[kind]


# =====================================================================
# shared shell
# =====================================================================

def shell(p: Painter, active: str, crumb: str, title: str, desc: str, actions, kicker="AI ADAPTIVE UI · STUDIO"):
    # sidebar
    p.rect(0, 0, 236, p.h, fill=T["surface2"], panel="sidebar")
    p.line(236, 0, 236, p.h, T["line"])
    p.rect(24, 22, 24, 24, fill=T["ink"], rx=7)
    p.txt(36, 39.4, "O", 13, "#ffffff", "serif", 500, "middle")
    p.txt(58, 34, "OntoPlatform", 13.2, T["ink"], "sans", 600)
    p.txt(58, 49, "业务世界控制台", 9.2, T["muted"])
    p.rect(14, 68, 208, 62, fill=T["sunken"], rx=8)
    p.txt(28, 86, "WORKSPACE", 7.8, T["faint"], "mono", 400, ls=1.2)
    p.txt(28, 104, "收费站全链路演练", 11.4, T["ink"], "sans", 600)
    p.txt(28, 120, "7c33935df1d1 · 全功能", 8.2, T["muted"], "mono")

    p.txt(24, 160, "业务世界", 7.8, T["faint"], "mono", 400, ls=1.2)
    nav = [("本体", "cube"), ("数据", "db"), ("能力", "bolt"), ("应用", "grid"),
           ("对象实例", "target"), ("智能视图", "spark")]
    y = 174
    for label, icon in nav:
        is_active = label == active
        if is_active:
            p.rect(12, y - 2, 212, 30, fill=T["sunken"], rx=6)
            p.rect(12, y - 2, 2.5, 30, fill=T["ink"])
        glyph(p, icon, 26, y + 12, T["ink"] if is_active else T["muted"])
        p.txt(48, y + 17, label, 11.6, T["ink"] if is_active else T["ink2"], "sans", 600 if is_active else 400)
        if label == "智能视图":
            p.txt(206, y + 16, "AI", 7.4, T["clay"], "mono", 600, "end")
        y += 30

    p.line(20, 372, 216, 372, T["linesoft"])
    p.txt(24, 394, "运行", 7.8, T["faint"], "mono", 400, ls=1.2)
    for i, label in enumerate(["运行日志", "AI 助手", "审计"]):
        p.txt(24, 418 + i * 28, label, 11.0, T["ink2"])
    p.txt(24, 520, "平台", 7.8, T["faint"], "mono", 400, ls=1.2)
    for i, label in enumerate(["设置", "帮助"]):
        p.txt(24, 544 + i * 28, label, 11.0, T["ink2"])
    p.line(20, 936, 216, 936, T["linesoft"])
    p.txt(24, 958, "v0.3.0 · local", 8.2, T["faint"], "mono")

    # topbar
    p.rect(236, 0, p.w - 236, 58, fill=T["bg"])
    p.line(236, 58, p.w, 58, T["line"])
    p.txt(260, 36, "工作台", 10.4, T["muted"])
    p.txt(302, 36, "/", 10.4, T["faint"])
    p.txt(314, 36, "收费站全链路演练", 10.4, T["muted"])
    p.txt(414, 36, "/", 10.4, T["faint"])
    p.txt(426, 36, "智能视图", 10.4, T["muted"])
    p.txt(482, 36, "·", 10.4, T["faint"])
    p.txt(492, 36, crumb, 10.4, T["ink"], "sans", 600)

    p.rect(1160, 14, 180, 30, fill=T["surface"], stroke=T["line"], rx=6)
    p.txt(1176, 33, "搜索或跳转", 9.4, T["faint"])
    p.txt(1322, 33, "⌘K", 8.6, T["faint"], "mono")
    p.rect(1356, 14, 122, 30, fill=T["surface"], stroke=T["line"], rx=6)
    p.circle(1370, 29, 3.4, fill=T["sage"])
    p.txt(1380, 33, "gpt-4o-mini", 9.2, T["ink2"], "mono")
    p.circle(1520, 29, 13, fill="#e9e4d8")
    p.txt(1520, 33, "L", 10.4, T["ink"], "sans", 600, "middle")
    p.circle(1560, 29, 12, fill="none", stroke=T["line2"])
    p.txt(1560, 32.6, "?", 9.6, T["muted"], "sans", 600, "middle")

    # page header
    p.line(262, 96, 276, 96, T["line2"])
    p.txt(282, 99.4, kicker, 8.6, T["muted"], "mono", 400, ls=1.4)
    p.txt(262, 136, title, 29, T["ink"], "serif", 500, ls=-0.5, maxw=920)
    p.txt(262, 159, desc, 12.0, T["muted"], maxw=880)
    if actions:
        actions(p)
    p.line(262, 176, 1574, 176, T["line"])


def glyph(p: Painter, name: str, x: float, y: float, color: str):
    """Small hand-drawn 12px glyphs; stroke based, 1.4px, no icon font."""
    if name == "cube":
        p.path(f"M{x} {y-5} L{x+5} {y-2} L{x+5} {y+3} L{x} {y+6} L{x-5} {y+3} L{x-5} {y-2} Z", stroke=color, sw=1.3)
        p.path(f"M{x} {y+.5} L{x+5} {y-2} M{x} {y+.5} L{x-5} {y-2} M{x} {y+.5} L{x} {y+6}", stroke=color, sw=1.1)
    elif name == "db":
        p.path(f"M{x-5} {y-4} A5 2.2 0 0 1 {x+5} {y-4} A5 2.2 0 0 1 {x-5} {y-4} Z", stroke=color, sw=1.3)
        p.path(f"M{x-5} {y-4} L{x-5} {y+3} A5 2.2 0 0 0 {x+5} {y+3} L{x+5} {y-4}", stroke=color, sw=1.3)
        p.path(f"M{x-5} {y-0.5} A5 2.2 0 0 0 {x+5} {y-0.5}", stroke=color, sw=1.1)
    elif name == "bolt":
        p.path(f"M{x+1} {y-6} L{x-4} {y+1} L{x} {y+1} L{x-1} {y+6} L{x+4} {y-1} L{x} {y-1} Z", fill=color)
    elif name == "grid":
        for dx, dy in ((-5, -5), (1, -5), (-5, 1), (1, 1)):
            p.rect(x + dx, y + dy, 4.4, 4.4, fill="none", stroke=color, sw=1.2, rx=0.6)
    elif name == "target":
        p.circle(x, y, 5.4, fill="none", stroke=color, sw=1.3)
        p.circle(x, y, 1.8, fill=color)
        p.line(x - 7, y, x - 4.4, y, color, 1.2); p.line(x + 4.4, y, x + 7, y, color, 1.2)
        p.line(x, y - 7, x, y - 4.4, color, 1.2); p.line(x, y + 4.4, x, y + 7, color, 1.2)
    elif name == "spark":
        p.path(f"M{x} {y-6} L{x+1.6} {y-1.6} L{x+6} {y} L{x+1.6} {y+1.6} L{x} {y+6} "
               f"L{x-1.6} {y+1.6} L{x-6} {y} L{x-1.6} {y-1.6} Z", fill=color)
    elif name == "check":
        p.circle(x, y, 6, fill=T["greenbg"], stroke=T["greenink"], sw=1)
        p.path(f"M{x-2.4} {y} L{x-0.5} {y+2} L{x+2.8} {y-2}", stroke=T["greenink"], sw=1.2)


# =====================================================================
# runtime preview (shared by screen 1 and 2)
# =====================================================================

def runtime_chrome(p: Painter, x, y, title, subtitle, role, actions_chip, tag_text, tag_tone):
    w = 600
    p.rect(652, 270, 600, 516, fill=T["surface2"], rx=4)
    p.rect(652, 270, 600, 30, fill=T["sunken"])
    p.line(652, 300, 1252, 300, T["line"])
    p.txt(666, 289.5, "VIBE", 8.4, T["ink"], "mono", 600, ls=1.4)
    p.txt(704, 289.5, "本体驱动 · 智能视图", 7.6, T["muted"])
    chips = [(1030, "鲁C56789", 58), (1094, role, 44), (1144, actions_chip, 66)]
    for cx, label, cw in chips:
        p.rect(cx, 277, cw, 17, fill=T["surface"], stroke=T["line"], rx=3)
        p.txt(cx + cw / 2, 288.6, label, 7.2, T["ink2"], "mono", 400, "middle", maxw=cw - 6)
    p.txt(666, 328, title, 15, T["ink"], "serif", 500, maxw=380)
    p.txt(666, 344, subtitle, 7.3, T["muted"], "mono", maxw=470)
    p.tag(1160, 314, 82, 18, tag_text, tag_tone, 7.6)


def filter_bar(p: Painter, y=354):
    p.rect(666, y, 572, 28, fill=T["surface"], stroke=T["clay"], sw=1.0, rx=4, dash="4 3")
    p.badge(659, y - 6, "1", 6.2)
    p.txt(678, y + 17.6, "时间范围", 7.4, T["muted"])
    p.txt(722, y + 17.6, "近 7 天", 8.0, T["ink"], "sans", 600)
    p.path(f"M748 {y+13} l3 3 l3 -3", stroke=T["muted"], sw=1.0)
    p.line(768, y + 7, 768, y + 21, T["line"])
    p.txt(780, y + 17.6, "站点", 7.4, T["muted"])
    p.txt(806, y + 17.6, "全部", 8.0, T["ink"], "sans", 600)
    p.line(838, y + 7, 838, y + 21, T["line"])
    p.txt(850, y + 17.6, "状态", 7.4, T["muted"])
    p.txt(876, y + 17.6, "异常 + 待核", 8.0, T["ink"], "sans", 600)
    p.txt(1204, y + 17.6, "筛选", 7.4, T["muted"], "sans", 600)
    p.path(f"M1196 {y+11} h-6 M1190 {y+8} l-3 3 l3 3", stroke=T["muted"], sw=1.0)


def kpis(p: Painter, values):
    x = 666
    for i, (label, value, delta, tone) in enumerate(values):
        cx = x + i * 145
        p.rect(cx, 392, 137, 58, fill=T["surface"], stroke=T["line"], rx=4)
        p.txt(cx + 12, 406, label, 7.2, T["muted"], "mono")
        p.txt(cx + 12, 427, value, 13.6, T["ink"], "sans", 600, maxw=113)
        color = {"ok": T["greenink"], "warn": T["amberink"], "clay": T["clay"], "muted": T["muted"]}[tone]
        p.txt(cx + 12, 442, delta, 7.0, color, maxw=113)


def chart_card(p: Painter, title, badge=True):
    p.rect(666, 462, 286, 160, fill=T["surface"], stroke=T["line"], rx=4)
    p.txt(678, 480, title, 9.0, T["ink"], "sans", 600, maxw=240)
    p.txt(678, 493, "近 7 天 · sum(fee) · 异常交易", 7.0, T["muted"], "mono", maxw=250)
    if badge:
        p.badge(659, 478, "2", 6.2)
    heights = [54, 30, 42, 18, 60, 26]
    labels = ["济西", "青东", "淄博", "潍坊", "德州", "临沂"]
    base_y = 596
    for i, (h, label) in enumerate(zip(heights, labels)):
        bx = 700 + i * 37
        p.rect(bx, base_y - h, 20, h, fill=T["clay"] if i in (0, 4) else "#cdc6b7", rx=1.5)
        p.txt(bx + 10, base_y + 12, label, 7.0, T["muted"], "sans", 400, "middle")
    p.line(684, base_y + 3, 938, base_y + 3, T["line"])


def map_card(p: Painter, badge=True):
    p.rect(960, 462, 278, 160, fill=T["surface"], stroke=T["line"], rx=4)
    p.txt(972, 480, "异常站点分布", 9.0, T["ink"], "sans", 600)
    p.txt(972, 493, "济南西 / 德州 拥堵", 7.0, T["muted"], "mono")
    if badge:
        p.badge(953, 478, "3", 6.2)
    p.rect(972, 502, 254, 104, fill=T["sunken"], rx=4)
    p.path("M980 588 C 1012 572 1034 556 1064 558 S 1112 540 1142 546 S 1196 520 1218 528",
           stroke=T["line2"], sw=2.0)
    p.path("M984 520 C 1016 528 1046 546 1074 566 S 1136 584 1176 580 S 1210 586 1222 582",
           stroke=T["line2"], sw=1.4, dash="4 3")
    p.path("M1038 506 L1046 596", stroke=T["line2"], sw=1.0, dash="3 3")
    for cx, cy, fill in [(1020, 556, T["clay"]), (1092, 538, T["clay"]),
                         (1150, 570, T["ochre"]), (1198, 532, T["sage"])]:
        p.circle(cx, cy, 4.2, fill=fill, stroke=T["surface"], sw=1.4)
    p.circle(986, 616, 2.6, fill=T["clay"]); p.txt(993, 618.5, "拥堵 2", 7.0, T["muted"])
    p.circle(1040, 616, 2.6, fill=T["ochre"]); p.txt(1047, 618.5, "繁忙 1", 7.0, T["muted"])
    p.circle(1094, 616, 2.6, fill=T["sage"]); p.txt(1101, 618.5, "畅通 1", 7.0, T["muted"])


def table_flat(p: Painter):
    p.rect(666, 632, 572, 150, fill=T["surface"], stroke=T["line"], rx=4)
    p.txt(678, 650, "最近通行记录", 9.0, T["ink"], "sans", 600)
    p.txt(1226, 650, "20 条 · 按时间倒序", 7.0, T["muted"], "mono", 400, "end")
    p.line(666, 658, 1238, 658, T["linesoft"])
    heads = [(678, "时间"), (730, "车牌"), (796, "出口站"), (878, "金额"), (970, "状态"), (1080, "处置")]
    for hx, label in heads:
        p.txt(hx, 671, label, 7.0, T["muted"], "mono")
    rows = [("07:08", "鲁D56789", "济南西", "¥316.80", "异常", "err"),
            ("07:15", "鲁A45678", "德州", "¥287.28", "异常", "err"),
            ("07:21", "鲁A23456", "青岛东", "¥71.10", "正常", "ok"),
            ("07:30", "鲁B56789", "临沂", "¥164.16", "待核", "warn")]
    for i, (ts, plate, station, fee, status, tone) in enumerate(rows):
        ry = 690 + i * 22
        p.txt(678, ry, ts, 8.0, T["ink2"], "mono")
        p.txt(730, ry, plate, 8.0, T["ink2"], "mono")
        p.txt(796, ry, station, 8.0, T["ink2"])
        p.txt(878, ry, fee, 8.0, T["ink2"] if fee != "¥316.80" else T["clay"], "mono", 600 if fee == "¥316.80" else 400)
        p.tag(966, ry - 9, 34, 15, status, tone, 7.2)
        p.txt(1080, ry, "查看 →", 7.4, T["muted"])
        if i < 3:
            p.line(678, ry + 9, 1226, ry + 9, T["linesoft"])


def table_grouped(p: Painter):
    p.rect(666, 632, 572, 150, fill=T["surface"], stroke=T["line"], rx=4)
    p.txt(678, 650, "异常交易明细 · 按站点分组", 9.0, T["ink"], "sans", 600)
    p.txt(1226, 650, "共 18 笔 · 阈值 ¥300", 7.0, T["muted"], "mono", 400, "end")
    p.line(666, 658, 1238, 658, T["linesoft"])
    # group 1
    p.rect(666, 662, 572, 15, fill=T["sunken"])
    p.txt(678, 674, "济南西收费站 · 6 笔 · 拥堵", 7.6, T["ink"], "sans", 600)
    p.txt(1226, 674, "已剪除处置", 7.2, T["faint"], "sans", 400, "end")
    rows = [("07:08", "鲁D56789", "¥316.80", "异常"),
            ("07:22", "鲁A12345", "¥402.10", "异常")]
    for i, (ts, plate, fee, status) in enumerate(rows):
        ry = 690 + i * 21
        p.txt(678, ry, ts, 7.8, T["ink2"], "mono")
        p.txt(730, ry, plate, 7.8, T["ink2"], "mono")
        p.txt(796, ry, "→ 济南西", 7.8, T["muted"])
        p.txt(878, ry, fee, 7.8, T["clay"], "mono", 600)
        p.tag(966, ry - 8, 34, 14, status, "err", 7.2)
        p.rect(1086, ry - 9, 66, 15, fill=T["sunken"], rx=3)
        p.txt(1119, ry + 1.6, "锁定", 7.0, T["faint"], "sans", 400, "middle")
    p.rect(666, 724, 572, 15, fill=T["sunken"])
    p.txt(678, 736, "德州收费站 · 4 笔 · 拥堵", 7.6, T["ink"], "sans", 600)
    ry = 752
    p.txt(678, ry, "07:15", 7.8, T["ink2"], "mono")
    p.txt(730, ry, "鲁A45678", 7.8, T["ink2"], "mono")
    p.txt(796, ry, "→ 德州", 7.8, T["muted"])
    p.txt(878, ry, "¥287.28", 7.8, T["ink2"], "mono")
    p.tag(966, ry - 8, 34, 14, "异常", "err", 7.2)
    p.rect(1086, ry - 9, 66, 15, fill=T["sunken"], rx=3)
    p.txt(1119, ry + 1.6, "锁定", 7.0, T["faint"], "sans", 400, "middle")
    p.badge(1164, 706, "3", 6.2)


# =====================================================================
# screen 1 · studio result
# =====================================================================

def screen_studio() -> Painter:
    p = Painter(*CANVAS)

    def actions(pp: Painter):
        pp.rect(1240, 90, 334, 34, fill=T["sunken"], stroke=T["line"], rx=8)
        pp.rect(1352, 92, 110, 30, fill=T["surface"], stroke=T["line2"], rx=6)
        pp.txt(1296, 110, "运行视图", 10.2, T["muted"], "sans", 400, "middle")
        pp.txt(1407, 110, "AI 生成台", 10.4, T["ink"], "sans", 600, "middle")
        pp.txt(1518, 110, "生成记录", 10.2, T["muted"], "sans", 400, "middle")

    shell(p, "智能视图", "AI 生成台", "用一句话生成业务视图",
          "LLM 只负责提议：蓝图先过 Schema、组件白名单与权限裁决，再由确定性编译器落到真实数据。",
          actions)

    # ---- left: prompt + recent
    p.rect(262, 192, 340, 430, fill=T["surface"], stroke=T["line"], rx=10, panel="left-prompt")
    p.txt(282, 222, "描述你想要的视图", 13.4, T["ink"], "sans", 600)
    p.txt(282, 239, "业务语言 + 约束，越具体越稳", 9.8, T["muted"])
    p.line(282, 252, 582, 252, T["linesoft"])
    p.rect(282, 264, 300, 108, fill=T["surface2"], stroke=T["line"], rx=6)
    p.txt(294, 286, "给值班站长做一个通行交易异常分析看板：", 10.6, T["ink2"], maxw=276)
    p.txt(294, 303, "按站点分组，突出异常金额，右侧配地图，", 10.6, T["ink2"], maxw=276)
    p.txt(294, 320, "底部放最近 20 笔交易。", 10.6, T["ink2"], maxw=276)
    p.line(294, 334, 295, 348, T["ink"], 1.2)
    p.txt(282, 392, "⌘↵ 生成", 8.8, T["muted"], "mono")
    p.txt(582, 392, "48 / 500", 8.8, T["faint"], "mono", 400, "end")

    chips = ["异常分析", "站点日报", "拥堵巡检", "趋势对比"]
    x = 282
    for c in chips:
        p.pill(x, 404, 52, 23, c, 9.2, T["ink2"], T["sunken"])
        x += 60

    # options
    def segment(sx, sy, sw, sh, labels, active):
        p.rect(sx, sy, sw, sh, fill=T["surface"], stroke=T["line"], rx=6)
        seg = sw / len(labels)
        for i, label in enumerate(labels):
            if i == active:
                p.rect(sx + i * seg + 1, sy + 1, seg - 2, sh - 2, fill=T["sunken"], rx=5)
            p.txt(sx + i * seg + seg / 2, sy + sh / 2 + 3.6, label, 9.2,
                  T["ink"] if i == active else T["muted"], "sans", 600 if i == active else 400, "middle", maxw=seg - 8)

    p.txt(282, 458, "生成模式", 9.8, T["muted"])
    segment(374, 444, 208, 26, ["标准", "快速", "深度"], 0)
    p.txt(282, 490, "输出", 9.8, T["muted"])
    segment(374, 476, 208, 26, ["只读", "含动作"], 1)
    p.txt(282, 522, "角色", 9.8, T["muted"])
    segment(374, 508, 208, 26, ["owner", "editor", "viewer"], 1)

    p.rect(282, 546, 300, 38, fill=T["ink"], rx=7)
    p.txt(432, 570, "生成视图", 11.6, "#ffffff", "sans", 500, "middle")
    p.txt(560, 570, "⌘↵", 8.8, "#d8d2c4", "mono", 400, "end")
    p.txt(282, 602, "生成后可继续修改；发布走应用生命周期。", 8.8, T["muted"])

    # recent
    p.rect(262, 638, 340, 318, fill=T["surface"], stroke=T["line"], rx=10, panel="left-recent")
    p.txt(282, 668, "最近生成", 13.4, T["ink"], "sans", 600)
    p.txt(582, 668, "查看全部 →", 9.2, T["muted"], "sans", 400, "end")
    items = [
        ("v3", "通行交易异常分析", "2 分钟前 · gpt-4o-mini · 4.2s", "已发布", "ok"),
        ("v2", "站点日报与拥堵巡检", "今天 09:18 · gpt-4o-mini · 3.8s", "草稿", "plain"),
        ("v1", "收费站运行总览", "昨天 17:20 · gpt-4o-mini · 2.9s", "已降级", "warn"),
    ]
    for i, (ver, title, meta, status, tone) in enumerate(items):
        iy = 684 + i * 74
        p.rect(282, iy + 1, 34, 18, fill=T["sunken"], rx=4)
        p.txt(299, iy + 14, ver, 8.4, T["ink2"], "mono", 600, "middle")
        p.txt(324, iy + 14, title, 11.2, T["ink"], "sans", 600, maxw=190)
        p.txt(282, iy + 34, meta, 8.2, T["muted"], "mono", maxw=240)
        p.tag(526, iy, 56, 20, status, tone, 8.2)
        p.line(282, iy + 64, 582, iy + 64, T["linesoft"])
    p.txt(282, 936, "保留 20 条记录 · 发布后不可原地编辑", 8.4, T["faint"])

    # ---- center: preview
    p.rect(620, 192, 664, 764, fill=T["surface"], stroke=T["line"], rx=10, panel="center-preview")
    p.txt(640, 222, "预览 · 草稿 v3", 13.6, T["ink"], "sans", 600)
    p.txt(640, 239, "TollTransaction · 鲁C56789 · editor", 8.8, T["muted"], "mono")
    p.line(640, 252, 1264, 252, T["linesoft"])
    p.tag(1074, 200, 70, 22, "校验通过", "ok", 8.6)
    p.rect(1152, 200, 88, 22, fill=T["surface"], stroke=T["line"], rx=5)
    for i, label in enumerate(["桌", "平", "手"]):
        sx = 1152 + i * 29.3
        if i == 0:
            p.rect(sx + 1, 201, 27.3, 20, fill=T["sunken"], rx=4)
        p.txt(sx + 14.6, 215, label, 8.2, T["ink"] if i == 0 else T["muted"], "sans", 400, "middle")
    p.txt(1258, 216, "↗", 10.4, T["muted"], "sans", 400, "middle")

    p.rect(640, 258, 624, 540, fill=T["sunken"], stroke=T["line"], rx=8)
    runtime_chrome(p, 652, 270, "通行交易异常分析",
                   "收费站全链路演练（全功能） · 40 笔交易 · 6 站点",
                   "editor", "2/3 可执行", "L0→L4 已编译", "ok")
    filter_bar(p)
    kpis(p, [
        ("异常交易", "18 笔", "-12% 较上周", "ok"),
        ("异常金额", "¥46,200", "+8% 较上周", "clay"),
        ("涉及站点", "4 / 6", "2 站拥堵", "muted"),
        ("平均处置", "6.4 分", "待处置 6 笔", "warn"),
    ])
    chart_card(p, "按站点异常金额")
    map_card(p)
    table_flat(p)

    # footer under stage
    p.txt(640, 826, "本轮改动", 8.8, T["muted"], "mono", 400, ls=1.0)
    for i, (label, bw) in enumerate([("新增筛选器", 86), ("按站点分组", 86), ("新增地图", 76)]):
        bx = 700 + (0 if i == 0 else 94 if i == 1 else 188)
        p.pill(bx, 812, bw, 20, label, 8.6, T["clay"], "#f7f1ec", "#e3cfc5")
        p.badge(bx + 10, 822, str(i + 1), 5.6)
    p.txt(640, 853, "program sha256:6f1c9d… · 896 B · 7 视角 · tools 6/6 closed", 8.4, T["muted"], "mono", maxw=600)
    p.txt(640, 869, "意图 anomaly_analysis · 置信度 0.82 · 模型 gpt-4o-mini · 1,840 tokens", 8.4, T["muted"], "mono", maxw=600)
    p.txt(640, 885, "预览与运行共用同一渲染器；角色与动作由平台裁决。", 9.0, T["muted"], maxw=600)
    p.button(640, 902, 96, 32, "继续修改", False, 9.8)
    p.button(744, 902, 104, 32, "保存草稿", True, 9.8)
    p.button(856, 902, 88, 32, "对比 v2", False, 9.8)

    # ---- right: trace
    p.rect(1302, 192, 272, 764, fill=T["surface"], stroke=T["line"], rx=10, panel="right-trace")
    p.txt(1322, 222, "生成轨迹", 13.6, T["ink"], "sans", 600)
    p.txt(1322, 239, "4.2s · 1,840 tokens · ¥0.03", 8.4, T["muted"], "mono")
    p.line(1322, 252, 1554, 252, T["linesoft"])

    steps = [
        ("L0 本体上下文", "12ms", 266, None),
        ("L1–L2 能力与模板", "8ms", 306, None),
        ("LLM 提议", "4.2s", 346, "gpt-4o-mini · 提议 BlueprintIntent"),
        ("平台裁决", "21ms", 398, None),
        ("L3–L4 编译", "9ms", 438, None),
        ("L5 沙箱渲染", "140ms", 478, None),
    ]
    for i, (label, ms, sy, sub) in enumerate(steps):
        if sub:
            p.rect(1318, sy - 6, 240, 54, fill="#f6f2e8", rx=6)
            p.rect(1318, sy - 6, 2, 54, fill=T["ochre"])
        cy = sy + 8
        p.check(1334, cy, T["greenink"], 6.2)
        p.txt(1352, sy + 12, label, 10.2, T["ink"], "sans", 600 if sub else 400)
        p.txt(1554, sy + 12, ms, 8.0, T["muted"], "mono", 400, "end")
        if sub:
            p.txt(1352, sy + 26, sub, 7.6, T["muted"], maxw=196)
        if i < len(steps) - 1:
            p.line(1334, cy + 7, 1334, steps[i + 1][2] - 1, T["line2"], 1.0, dash="2 3")

    p.txt(1322, 536, "平台裁决", 8.8, T["muted"], "mono", 400, ls=1.0)
    p.line(1322, 544, 1554, 544, T["linesoft"])
    checks = [("Schema 校验", "通过", "ok"), ("组件白名单", "通过", "ok"),
              ("数据范围", "只读", "plain"), ("动作权限", "剪除 1", "warn")]
    for i, (label, tag, tone) in enumerate(checks):
        cy = 562 + i * 26
        p.check(1334, cy, T["greenink"], 5.2)
        p.txt(1350, cy + 3.4, label, 9.8, T["ink2"])
        p.tag(1500, cy - 8, 54, 17, tag, tone, 8.2)
    p.txt(1350, 676, "set_status 需要 owner，已剪除", 8.0, T["muted"])

    p.txt(1322, 700, "模型调用", 8.8, T["muted"], "mono", 400, ls=1.0)
    p.line(1322, 708, 1554, 708, T["linesoft"])
    for i, line in enumerate(["provider openai · gpt-4o-mini", "prompt hash 9a3f… · retries 0",
                              "budget 2,050 / 8,000 tokens"]):
        p.txt(1322, 724 + i * 16, line, 8.0, T["muted"], "mono", maxw=232)

    p.rect(1322, 780, 232, 120, fill=T["surface2"], stroke=T["line"], rx=8)
    p.txt(1334, 802, "LLM 只能提议", 9.8, T["ink"], "sans", 600)
    for i, line in enumerate(["不接触业务数据与写操作；", "蓝图必须通过 Schema、权限、", "预算裁决后才编译为程序。"]):
        p.txt(1334, 818 + i * 15, line, 8.2, T["muted"], maxw=208)
    p.txt(1334, 884, "查看生成协议 ↗", 8.4, T["ink2"])
    return p


# =====================================================================
# screen 2 · iterate
# =====================================================================

def screen_iterate() -> Painter:
    p = Painter(*CANVAS)

    def actions(pp: Painter):
        pp.button(1370, 94, 92, 32, "对比 v3", False, 9.8)
        pp.button(1470, 94, 104, 32, "发布", True, 9.8)

    shell(p, "智能视图", "AI 生成台", "多轮修改，保留每一次版本",
          "每次修改生成新版本；旧版本可回看、对比、回滚。发布仍然走应用生命周期。",
          actions, kicker="AI ADAPTIVE UI · REFINE")

    # left: thread
    p.rect(262, 192, 340, 764, fill=T["surface"], stroke=T["line"], rx=10, panel="left-thread")
    p.txt(282, 222, "修改意见", 13.4, T["ink"], "sans", 600)
    p.txt(282, 239, "第 2 轮 · 基于 v3 生成 v4", 9.8, T["muted"])
    p.line(282, 252, 582, 252, T["linesoft"])
    p.rect(282, 264, 300, 54, fill=T["sunken"], rx=8)
    p.txt(294, 284, "把异常交易按站点分组，", 10.2, T["ink2"], maxw=276)
    p.txt(294, 299, "顶部加时间范围筛选。", 10.2, T["ink2"], maxw=276)
    p.txt(282, 330, "你 · 10:41", 8.2, T["faint"], "mono")

    p.rect(282, 346, 18, 18, fill=T["ink"], rx=4)
    p.txt(291, 358.6, "AI", 7.2, "#ffffff", "sans", 600, "middle")
    p.txt(308, 360, "已生成 v4 草稿", 9.8, T["ink"], "sans", 600)
    for i, line in enumerate(["新增时间范围筛选（近 7 天）；", "异常表按站点分组；", "set_status 对 editor 已剪除。"]):
        p.txt(308, 376 + i * 14, line, 8.6, T["muted"], maxw=256)
    p.txt(282, 424, "4.6s · 2,140 tokens", 8.0, T["faint"], "mono")

    p.rect(282, 440, 300, 106, fill=T["surface2"], stroke=T["line"], rx=8)
    p.txt(294, 460, "本轮改动 · 3 处", 8.6, T["muted"], "mono")
    for i, label in enumerate(["新增时间范围筛选器", "异常表按站点分组", "剪除 1 个越权动作"]):
        cy = 482 + i * 20
        p.badge(300, cy - 3, str(i + 1), 6.5)
        p.txt(312, cy, label, 8.8, T["ink2"], maxw=250)

    p.line(282, 566, 582, 566, T["linesoft"])
    p.txt(282, 586, "上下文已锁定", 8.6, T["muted"], "mono", 400, ls=1.0)
    for i, (label, value) in enumerate([
        ("对象", "TollTransaction · 鲁C56789"),
        ("角色", "editor · 动作剪除 1"),
        ("模型", "gpt-4o-mini · 预算 8k"),
    ]):
        cy = 608 + i * 20
        p.txt(282, cy, label, 8.2, T["faint"])
        p.txt(324, cy, value, 8.4, T["ink2"], maxw=250)
    p.txt(282, 678, "修改只重算差异局部，不重新理解", 8.2, T["muted"], maxw=276)
    p.txt(282, 692, "全部本体与数据。", 8.2, T["muted"], maxw=276)

    p.txt(282, 790, "继续修改", 8.6, T["muted"], "mono", 400, ls=1.0)
    p.rect(282, 800, 300, 58, fill=T["surface"], stroke=T["clay"], rx=6, dash="4 3")
    p.txt(294, 818, "再给金额加阈值高亮：", 9.4, T["ink2"], maxw=276)
    p.txt(294, 833, "超过 ¥300 的行标红。", 9.4, T["ink2"], maxw=276)
    p.button(282, 866, 300, 34, "生成 v5 · ⌘↵", True, 10.2)
    for i, c in enumerate(["换角色", "精简", "加地图"]):
        p.pill(282 + i * 62, 910, 54, 22, c, 8.8, T["ink2"], T["sunken"])

    # center: preview v4
    p.rect(620, 192, 664, 764, fill=T["surface"], stroke=T["line"], rx=10, panel="center-preview")
    p.txt(640, 222, "预览 · v4 草稿", 13.6, T["ink"], "sans", 600)
    p.txt(640, 239, "TollTransaction · 鲁C56789 · editor · 对比 v3", 8.8, T["muted"], "mono")
    p.line(640, 252, 1264, 252, T["linesoft"])
    p.tag(1066, 200, 58, 22, "待发布", "warn", 8.6)
    p.rect(1132, 200, 88, 22, fill=T["surface"], stroke=T["line"], rx=5)
    p.txt(1152, 215, "对比 v3", 8.2, T["ink2"], "sans", 400, "middle")
    p.rect(1228, 200, 34, 22, fill=T["surface"], stroke=T["line2"], rx=5)
    p.txt(1245, 215, "↗", 9.4, T["ink2"], "sans", 400, "middle")

    p.rect(640, 258, 624, 540, fill=T["sunken"], stroke=T["line"], rx=8)
    runtime_chrome(p, 652, 270, "异常交易 · 按站点分组",
                   "收费站全链路演练（全功能） · 40 笔交易 · 6 站点",
                   "editor", "2/3 可执行", "草稿 v4", "warn")
    filter_bar(p)
    kpis(p, [
        ("异常交易", "18 笔", "-12% 较上周", "ok"),
        ("异常金额", "¥46,200", "+8% 较上周", "clay"),
        ("涉及站点", "4 / 6", "2 站拥堵", "muted"),
        ("待处置", "6 笔", "阈值 ¥300", "warn"),
    ])
    chart_card(p, "异常金额 · 按站点分组")
    map_card(p)
    table_grouped(p)

    # footer: versions + diff + actions
    p.txt(640, 828, "版本", 8.8, T["muted"], "mono", 400, ls=1.0)
    for i, label in enumerate(["v1", "v2", "v3", "v4"]):
        bx = 690 + i * 50
        if label == "v4":
            p.rect(bx, 812, 42, 22, fill=T["ink"], rx=4)
            p.txt(bx + 21, 827, label, 8.4, "#ffffff", "mono", 600, "middle")
        else:
            p.rect(bx, 812, 42, 22, fill=T["surface"], stroke=T["line"], rx=4)
            p.txt(bx + 21, 827, label, 8.4, T["ink2"], "mono", 400, "middle")
    p.txt(930, 828, "与 v3 对比：+ 筛选器 · 表格分组 · 1 动作剪除", 8.6, T["muted"], maxw=330)
    p.txt(640, 852, "program sha256:4d2e… · 2,140 tokens · 剪除 1 项动作", 8.4, T["muted"], "mono", maxw=600)
    p.txt(640, 868, "旧版本保留；发布生成不可变快照。", 8.8, T["muted"])
    p.button(640, 888, 92, 32, "对比 v3", False, 9.6)
    p.button(740, 888, 100, 32, "保存草稿", False, 9.6)
    p.button(848, 888, 120, 32, "发布到应用", True, 9.6)

    # right: validation + publish
    p.rect(1302, 192, 272, 764, fill=T["surface"], stroke=T["line"], rx=10, panel="right-publish")
    p.txt(1322, 222, "裁决与发布", 13.6, T["ink"], "sans", 600)
    p.txt(1322, 239, "v4 草稿 · owner 审批", 9.6, T["muted"])
    p.line(1322, 252, 1554, 252, T["linesoft"])
    rows = [("Schema 校验", "通过", "ok"), ("组件白名单", "通过", "ok"),
            ("数据范围", "只读", "plain"), ("动作权限", "剪除 1", "warn"),
            ("预算 / 时延", "通过", "ok")]
    for i, (label, tag, tone) in enumerate(rows):
        cy = 272 + i * 26
        p.check(1334, cy, T["greenink"] if tone != "warn" else T["ochre"], 5.2,
                T["greenbg"] if tone != "warn" else T["amberbg"])
        p.txt(1350, cy + 3.4, label, 9.6, T["ink2"])
        p.tag(1500, cy - 8, 54, 17, tag, tone, 8.2)
    p.txt(1350, 402, "set_status 需要 owner 角色", 8.0, T["muted"])
    p.line(1322, 416, 1554, 416, T["linesoft"])

    p.txt(1322, 438, "发布到应用", 8.8, T["muted"], "mono", 400, ls=1.0)
    p.txt(1322, 462, "应用", 8.2, T["muted"])
    p.rect(1322, 468, 238, 30, fill=T["surface"], stroke=T["line2"], rx=5)
    p.txt(1334, 487, "收费稽核 · 演示", 9.2, T["ink"], "sans", 500)
    p.path("M1536 480 l4 4 l4 -4", stroke=T["muted"], sw=1.0)
    p.txt(1322, 516, "版本", 8.2, T["muted"])
    p.rect(1322, 522, 110, 30, fill=T["surface"], stroke=T["line2"], rx=5)
    p.txt(1334, 541, "v4", 9.2, T["ink"], "mono", 500)
    p.txt(1450, 516, "角色", 8.2, T["muted"])
    p.rect(1450, 522, 110, 30, fill=T["surface"], stroke=T["line2"], rx=5)
    p.txt(1462, 541, "owner", 9.2, T["ink"], "mono", 500)

    p.rect(1322, 564, 238, 62, fill=T["amberbg"], rx=6)
    p.txt(1334, 584, "发布后不可原地编辑；", 8.4, T["amberink"], maxw=214)
    p.txt(1334, 599, "调整需克隆新版本后重新发布。", 8.4, T["amberink"], maxw=214)
    p.txt(1334, 615, "published 语义与应用一致。", 8.0, T["amberink"], opacity=0.85, maxw=214)
    p.rect(1322, 640, 12, 12, fill=T["ink"], rx=3)
    p.path("M1324.5 646 l2 2.2 l4 -4", stroke="#ffffff", sw=1.2)
    p.txt(1340, 650, "保存 UI 快照（program hash）", 8.2, T["ink2"], maxw=210)
    p.button(1322, 668, 110, 32, "保存草稿", False, 9.4)
    p.button(1444, 668, 116, 32, "发布到应用", True, 9.4)
    p.line(1322, 716, 1554, 716, T["linesoft"])
    p.txt(1322, 738, "发布影响", 8.8, T["muted"], "mono", 400, ls=1.0)
    for i, line in enumerate(["应用：收费稽核 · 演示 (draft)", "运行时：新版本 v4 快照", "回滚：可切回 v3"]):
        p.txt(1322, 758 + i * 16, line, 8.4, T["muted"], maxw=224)
    p.txt(1322, 820, "发布人 owner · 写入审计与事件流", 8.0, T["faint"])
    p.txt(1322, 844, "查看应用详情 ↗", 8.6, T["ink2"])
    return p


# =====================================================================
# screen 3 · generation library
# =====================================================================

def screen_library() -> Painter:
    p = Painter(*CANVAS)
    p.rect(0, 0, 236, p.h, fill=T["surface2"], panel="sidebar")
    p.line(236, 0, 236, p.h, T["line"])
    p.rect(24, 22, 24, 24, fill=T["ink"], rx=7)
    p.txt(36, 39.4, "O", 13, "#ffffff", "serif", 500, "middle")
    p.txt(58, 34, "OntoPlatform", 13.2, T["ink"], "sans", 600)
    p.txt(58, 49, "业务世界控制台", 9.2, T["muted"])
    p.rect(14, 68, 208, 62, fill=T["sunken"], rx=8)
    p.txt(28, 86, "WORKSPACE", 7.8, T["faint"], "mono", 400, ls=1.2)
    p.txt(28, 104, "收费站全链路演练", 11.4, T["ink"], "sans", 600)
    p.txt(28, 120, "7c33935df1d1 · 全功能", 8.2, T["muted"], "mono")
    p.txt(24, 160, "业务世界", 7.8, T["faint"], "mono", 400, ls=1.2)
    y = 174
    for label in ["本体", "数据", "能力", "应用", "对象实例", "智能视图"]:
        p.txt(48, y + 17, label, 11.6, T["ink2"])
        y += 30
    p.line(20, 372, 216, 372, T["linesoft"])
    p.txt(24, 394, "运行", 7.8, T["faint"], "mono", 400, ls=1.2)
    for i, label in enumerate(["运行日志", "AI 助手", "审计"]):
        p.txt(24, 418 + i * 28, label, 11.0, T["ink2"])
    p.line(20, 936, 216, 936, T["linesoft"])
    p.txt(24, 958, "v0.3.0 · local", 8.2, T["faint"], "mono")

    p.rect(236, 0, p.w - 236, 58, fill=T["bg"])
    p.line(236, 58, p.w, 58, T["line"])
    p.txt(260, 36, "工作台 / 收费站全链路演练 / 智能视图 / 生成记录", 10.4, T["muted"])
    p.rect(1160, 14, 180, 30, fill=T["surface"], stroke=T["line"], rx=6)
    p.txt(1176, 33, "搜索或跳转", 9.4, T["faint"])
    p.txt(1322, 33, "⌘K", 8.6, T["faint"], "mono")
    p.rect(1356, 14, 122, 30, fill=T["surface"], stroke=T["line"], rx=6)
    p.circle(1370, 29, 3.4, fill=T["sage"])
    p.txt(1380, 33, "gpt-4o-mini", 9.2, T["ink2"], "mono")
    p.circle(1520, 29, 13, fill="#e9e4d8")
    p.txt(1520, 33, "L", 10.4, T["ink"], "sans", 600, "middle")

    p.line(262, 96, 276, 96, T["line2"])
    p.txt(282, 99.4, "AI ADAPTIVE UI · AUDIT", 8.6, T["muted"], "mono", 400, ls=1.4)
    p.txt(262, 136, "生成记录与版本审计", 29, T["ink"], "serif", 500, ls=-0.5)
    p.txt(262, 159, "每次生成、保存、发布都有版本与哈希；失败与降级同样留痕，可回滚。", 12.0, T["muted"])
    p.button(1428, 94, 84, 32, "导出 CSV", False, 9.6)
    p.button(1520, 94, 54, 32, "新建", True, 9.6)
    p.line(262, 176, 1574, 176, T["line"])

    # toolbar
    p.rect(262, 192, 340, 32, fill=T["surface"], stroke=T["line"], rx=6)
    p.circle(280, 208, 5, fill="none", stroke=T["muted"], sw=1.2)
    p.line(284, 212, 288, 216, T["muted"], 1.2)
    p.txt(298, 212.6, "搜索提示词、程序哈希", 9.4, T["faint"])
    p.txt(584, 212.6, "/", 8.8, T["faint"], "mono", 400, "end")
    chips = [("全部 12", 82, True), ("已发布 4", 80, False), ("草稿 5", 72, False),
             ("失败 2", 68, False), ("降级 1", 72, False)]
    x = 618
    for label, cw, active in chips:
        p.pill(x, 194, cw, 28, label, 9.0, T["ink"] if active else T["muted"],
               T["sunken"] if active else T["surface"], None if active else T["line"], 600 if active else 400)
        x += cw + 8
    p.rect(1458, 192, 116, 32, fill=T["surface"], stroke=T["line"], rx=6)
    p.rect(1459, 193, 56, 30, fill=T["sunken"], rx=5)
    p.txt(1487, 212, "列表", 9.0, T["ink"], "sans", 600, "middle")
    p.txt(1544, 212, "时间线", 9.0, T["muted"], "sans", 400, "middle")

    # table
    p.rect(262, 244, 1312, 368, fill=T["surface"], stroke=T["line"], rx=10, panel="table")
    p.rect(262, 244, 1312, 34, fill=T["surface2"])
    p.line(262, 278, 1574, 278, T["line"])
    heads = [(282, "时间", 80), (386, "提示词", 350), (774, "对象", 110), (900, "模型", 96),
             (1006, "耗时", 58), (1076, "状态", 96), (1190, "程序", 86), (1290, "操作", 240)]
    for hx, label, _ in heads:
        p.txt(hx, 266, label, 8.4, T["muted"], "mono")
    rows = [
        ("今天 10:41", "owner", "异常交易按站点分组，顶部加时间范围筛选", "v4 · 第 2 轮修改",
         "TollTransaction", "鲁C56789", "gpt-4o-mini", "4.6s", "待发布", "warn", "6f1c…9d", "查看 · 对比 · 发布", True),
        ("今天 10:32", "owner", "通行交易异常分析看板，右侧配地图", "v3 · 初始生成",
         "TollTransaction", "鲁C56789", "gpt-4o-mini", "4.2s", "已发布", "ok", "9a3f…2c", "查看 · 对比 · 回滚", False),
        ("今天 09:18", "editor", "站点日报与拥堵巡检", "v2 · 第 1 轮修改",
         "TollStation", "—", "gpt-4o-mini", "3.8s", "已发布", "ok", "3c7d…8e", "查看 · 对比", False),
        ("昨天 17:20", "editor", "收费站运行总览", "v1 · 初始生成",
         "TollStation", "—", "gpt-4o-mini", "2.9s", "草稿", "plain", "77ab…41", "查看 · 发布", False),
        ("昨天 16:02", "owner", "按车牌追踪完整通行链", "失败 · 重试 1 次",
         "Vehicle", "鲁C56789", "gpt-4o-mini", "—", "失败", "err", "—", "查看错误 · 重试", False),
    ]
    for i, row in enumerate(rows):
        ry = 278 + i * 66
        if row[-1]:
            p.rect(262, ry, 1312, 66, fill=T["surface2"])
            p.rect(262, ry, 2.5, 66, fill=T["ink"])
        p.line(262, ry + 66, 1574, ry + 66, T["linesoft"])
        p.txt(282, ry + 27, row[0], 9.4, T["ink"])
        p.txt(282, ry + 43, row[1], 8.0, T["muted"], "mono")
        p.txt(386, ry + 27, row[2], 10.0, T["ink"], maxw=350)
        p.txt(386, ry + 43, row[3], 8.2, T["muted"], maxw=340)
        p.txt(774, ry + 27, row[4], 8.6, T["ink2"], "mono")
        p.txt(774, ry + 43, row[5], 8.0, T["muted"], "mono")
        p.txt(900, ry + 27, row[6], 8.6, T["ink2"], "mono")
        p.txt(1006, ry + 27, row[7], 8.8, T["ink2"], "mono")
        p.tag(1076, ry + 18, 62, 20, row[8], row[9], 8.4)
        p.txt(1190, ry + 27, row[10], 8.4, T["muted"], "mono")
        p.txt(1290, ry + 27, row[11], 8.6, T["ink2"], maxw=230)

    # detail
    p.rect(262, 628, 790, 328, fill=T["surface"], stroke=T["line"], rx=10, panel="detail")
    p.txt(282, 658, "异常交易按站点分组", 13.0, T["ink"], "sans", 600)
    p.tag(700, 640, 64, 22, "待发布 v4", "warn", 8.2)
    p.txt(282, 676, "10:41 · owner · gpt-4o-mini · 4.6s · 2,140 tokens", 8.4, T["muted"], "mono")
    p.line(282, 690, 1032, 690, T["linesoft"])
    facts = [("PROGRAM HASH", "sha256:6f1c9d…", 282), ("ONTOLOGY VERSION", "v0.9.2", 662),
             ("ENGINE", "0.1.0 · deterministic", 282), ("TOKENS / COST", "2,140 / ¥0.04", 662),
             ("RETRIES", "0 · validation passed", 282), ("RUNTIME", "sandbox · 0 errors", 662)]
    for i, (label, value, fx) in enumerate(facts):
        fy = 712 + (i // 2) * 30
        p.txt(fx, fy, label, 7.2, T["faint"], "mono", 400, ls=0.8)
        p.txt(fx, fy + 13, value, 8.8, T["ink2"], "mono")
    p.line(282, 792, 1032, 792, T["linesoft"])
    p.txt(282, 814, "审计轨迹", 8.6, T["muted"], "mono", 400, ls=1.0)
    trail = [("10:41", "owner 生成 v4", "已生成"), ("10:43", "owner 保存草稿", "草稿"),
             ("—", "待发布到「收费稽核 · 演示」", "等待")]
    for i, (ts, text, state) in enumerate(trail):
        ty = 836 + i * 16
        p.circle(286, ty - 3, 2.4, fill=T["ink"] if i < 2 else T["faint"])
        p.txt(296, ty, ts, 8.2, T["muted"], "mono")
        p.txt(340, ty, text, 8.8, T["ink2"], maxw=560)
        p.txt(1010, ty, state, 8.2, T["muted"], "sans", 400, "end")
    p.button(282, 896, 84, 30, "预览", True, 9.4)
    p.button(374, 896, 96, 30, "与 v3 对比", False, 9.4)
    p.button(478, 896, 96, 30, "回滚到 v3", False, 9.4)
    p.button(582, 896, 104, 30, "导出 program", False, 9.4)

    # quality card
    p.rect(1070, 628, 504, 328, fill=T["surface"], stroke=T["line"], rx=10, panel="quality")
    p.txt(1090, 658, "本周质量与成本", 13.0, T["ink"], "sans", 600)
    p.txt(1090, 676, "workspace 7c33935df1d1", 8.4, T["muted"], "mono")
    p.line(1090, 690, 1554, 690, T["linesoft"])
    kpi = [("12", "次生成"), ("83%", "采纳率"), ("4.1s", "平均耗时"), ("1", "失败")]
    for i, (value, label) in enumerate(kpi):
        kx = 1090 + i * 116
        p.txt(kx, 722, value, 18, T["ink"], "serif", 500)
        p.txt(kx, 738, label, 7.8, T["muted"])
    p.rect(1090, 762, 464, 6, fill=T["line"], rx=3)
    p.rect(1090, 762, 288, 6, fill=T["sage"], rx=3)
    p.txt(1090, 782, "预算使用 62% · 3,420 / 5,500 tokens", 8.0, T["muted"], "mono")
    p.line(1090, 796, 1554, 796, T["linesoft"])
    events = [("10:43", "owner 保存草稿 v4"), ("10:41", "owner 生成 v4"),
              ("10:32", "owner 发布 v3 → 收费稽核"), ("09:18", "editor 生成 v2")]
    for i, (ts, text) in enumerate(events):
        ey = 818 + i * 18
        p.circle(1094, ey - 3, 2.2, fill=T["line2"])
        p.txt(1104, ey, ts, 8.0, T["muted"], "mono")
        p.txt(1150, ey, text, 8.6, T["ink2"], maxw=370)
    p.txt(1090, 930, "打开完整审计日志 →", 8.6, T["ink2"])
    return p


# =====================================================================
# screen 4 · pipeline architecture
# =====================================================================

def screen_architecture() -> Painter:
    p = Painter(*ARCH_CANVAS)
    p.line(60, 64, 74, 64, T["line2"])
    p.txt(80, 67.4, "SMART UI STUDIO · PIPELINE", 8.6, T["muted"], "mono", 400, ls=1.4)
    p.txt(60, 104, "LLM 提议，平台裁决", 32, T["ink"], "serif", 500, ls=-0.6)
    p.txt(60, 128, "模型只产出受 Schema 约束的蓝图提案；校验、编译、数据与权限始终由平台决定。", 12.2, T["muted"])

    # legend
    lx = 1160
    p.line(lx, 76, lx + 26, 76, T["ink"], 1.4)
    p.txt(lx + 34, 79, "主链路", 8.4, T["muted"])
    p.line(lx + 90, 76, lx + 116, 76, T["clay"], 1.4, dash="4 3")
    p.txt(lx + 124, 79, "重试 / 回退", 8.4, T["muted"])
    p.line(lx + 196, 76, lx + 222, 76, T["muted"], 1.2, dash="2 3")
    p.txt(lx + 230, 79, "审计写入", 8.4, T["muted"])

    nodes = [
        (52, "01", "描述与约束", ["业务语言 + 角色 / 输出", "设备 / 风格约束；不含", "任何数据实例值"]),
        (272, "02", "上下文包", ["L0 本体 + L1 能力 + L2", "模板；focus 与计数；", "actor 权限快照"]),
        (492, "03", "LLM 提议", ["Schema 约束的", "BlueprintIntent；禁止坐标、", "脚本与表现层字段"]),
        (712, "04", "平台裁决", ["Schema / 组件白名单 /", "数据范围 / 动作权限 /", "预算与时延"]),
        (932, "05", "确定性编译", ["复用 L3 规划 + L4 编译；", "同一 proposal 编译出", "同一程序与哈希"]),
        (1152, "06", "沙箱预览", ["Opaque origin + bridge；", "绑定真实 payload；", "角色切换不重调模型"]),
        (1372, "07", "资产化", ["草稿 / 版本 / 发布快照；", "program_hash 与", "审计事件绑定"]),
    ]
    ny, nh, nw = 180, 150, 196
    lane_labels = [(52, 468, "控制台 · 同源"), (492, 1348, "应用层 + 引擎"), (1372, 1568, "资产 · 治理")]
    for x0, x1, label in lane_labels:
        p.line(x0, 162, x1, 162, T["line"])
        p.txt(x0, 156, label, 7.8, T["faint"], "mono", 400, ls=1.0)

    for x, num, title, lines in nodes:
        p.rect(x, ny, nw, nh, fill=T["surface"], stroke=T["line"], rx=8, panel=f"node{num}")
        p.txt(x + 16, ny + 24, num, 8.2, T["faint"], "mono", 400, ls=1.2)
        p.txt(x + 16, ny + 50, title, 13.0, T["ink"], "sans", 600, maxw=nw - 24)
        for i, line in enumerate(lines):
            p.txt(x + 16, ny + 72 + i * 15, line, 8.4, T["muted"], maxw=nw - 26)
        p.line(x + 16, ny + 120, x + nw - 16, ny + 120, T["linesoft"])

    # arrows between nodes
    for i in range(len(nodes) - 1):
        x = nodes[i][0] + nw
        x2 = nodes[i + 1][0]
        mid = (x + x2) / 2
        p.line(x + 1, ny + 75, x2 - 6, ny + 75, T["ink"], 1.4)
        p.path(f"M{x2-6} {ny+75} l-5 -3.2 v6.4 Z", fill=T["ink"])

    # retry loop (node 4 -> node 3)
    p.path(f"M790 {ny+nh} V370 H590 V{ny+nh+2}", stroke=T["clay"], sw=1.3, dash="5 3")
    p.path("M590 332 l-3.4 5 h6.8 Z", fill=T["clay"])
    p.txt(700, 362, "校验失败 · 带诊断重试 ≤ 1 次", 8.2, T["clay"], maxw=240)
    # fallback (node 4 -> node 5)
    p.path(f"M830 {ny+nh} V404 H1030 V{ny+nh+2}", stroke=T["muted"], sw=1.3, dash="5 3")
    p.path("M1030 332 l-3.4 5 h6.8 Z", fill=T["muted"])
    p.txt(880, 396, "模型不可用 / 超时 / 校验仍失败 → 确定性回退", 8.2, T["muted"], maxw=300)

    # governance band
    p.rect(52, 452, 1516, 118, fill=T["surface2"], stroke=T["line"], rx=10, panel="governance")
    p.txt(72, 478, "治理闸门 · 每一步都留痕", 8.6, T["muted"], "mono", 400, ls=1.2)
    gates = [("Schema 校验", "蓝图 Schema · 视角词表 · 属性存在性"), ("权限剪裁", "actor 角色 ≤ 请求角色；动作二次校验"),
             ("预算与时延", "60s 超时 · token 上限 · 失败自动降级"), ("审计与版本", "prompt_hash · program_hash · 发布人")]
    for i, (title, desc) in enumerate(gates):
        gx = 72 + i * 376
        p.txt(gx, 510, title, 10.4, T["ink"], "sans", 600)
        p.txt(gx, 528, desc, 8.2, T["muted"], maxw=340)
        if i:
            p.line(gx - 16, 492, gx - 16, 548, T["linesoft"])

    # invariants
    invariants = ["I1 LLM 不写表现层", "I4 视角必须有本体依据", "实例数据不出发", "发布不改写生命周期"]
    for i, text in enumerate(invariants):
        ix = 52 + i * 380
        p.rect(ix, 596, 360, 34, fill=T["surface"], stroke=T["line"], rx=6)
        p.check(ix + 18, 613, T["greenink"], 6.0)
        p.txt(ix + 32, 617, text, 9.0, T["ink2"], maxw=316)
    p.txt(52, 664, "现有确定性链路保持可用：Studio 是增量入口，关闭开关后回到原形态。", 9.2, T["muted"])
    p.txt(52, 690, "失败语义：failed 不渲染半成品；fallback 明示降级原因；ready 才允许保存与发布。", 9.2, T["muted"])
    p.txt(52, 716, "模型调用与数据访问解耦：proposal 只含字段名与视角，payload 只在沙箱内按权限取数。", 9.2, T["muted"])
    return p


# =====================================================================

def main() -> int:
    screens = [
        ("01-studio.svg", screen_studio, CANVAS),
        ("02-iterate.svg", screen_iterate, CANVAS),
        ("03-library.svg", screen_library, CANVAS),
        ("04-architecture.svg", screen_architecture, ARCH_CANVAS),
    ]
    failed = False
    summary = {}
    for name, fn, _ in screens:
        p = fn()
        svg = p.finish()
        (OUT / name).write_text(svg, encoding="utf-8")
        status = "ok" if not p.errors else "FAIL"
        summary[name] = {"texts": p.texts, "errors": p.errors}
        print(f"{name}: {status} · {p.texts} texts · {len(p.errors)} errors")
        for err in p.errors:
            print("  -", err)
        failed = failed or bool(p.errors)
    (OUT / "audit.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
