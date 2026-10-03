#!/usr/bin/env python3
"""Check the colour tokens in style.css against the required contrast pairs.

The token values are parsed from the stylesheet itself (the light `:root` block, then the dark
`:root[data-theme="dark"]` block layered over it), never copied, so changing a token re-runs the audit.

    python scripts/check_contrast.py           # table of every pair; exit 1 if any fails
    python scripts/check_contrast.py --failing # only the failures

Text must reach 4.5:1 (WCAG 1.4.3). Control boundaries, the focus ring and chart-critical marks must reach
3:1 against what they edge (WCAG 1.4.11).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

CSS_PATH = Path(__file__).resolve().parents[1] / "src" / "slopfi" / "web" / "static" / "style.css"

TEXT_MIN, EDGE_MIN = 4.5, 3.0
SURFACES = ["page", "sidebar", "surface", "surface-raised", "surface-tint"]
TINTS = ["accent-tint", "warning-tint", "positive-tint", "negative-tint"]

# Text. Rows and notices can be tinted (selected = accent, uncategorised = warning, review steps and deltas =
# positive/negative), so the body inks are checked on every background; each semantic ink on the surfaces plus the
# tint it is set on (badges, notices, toasts); button text on the accent fill.
TEXT = (
    [(ink, bg) for ink in ("ink", "ink-2", "ink-3") for bg in SURFACES + TINTS]
    + [("accent", bg) for bg in SURFACES + ["accent-tint", "warning-tint"]]
    + [("positive", bg) for bg in SURFACES + ["positive-tint"]]
    + [("negative", bg) for bg in SURFACES + ["negative-tint"]]
    + [("warning", bg) for bg in SURFACES + ["warning-tint"]]
    + [("accent-ink", "accent")]
)
# Edges: field, checkbox and slider-track borders wherever a control can sit; the focus ring; filled controls
# (primary button, checked box, slider thumb); single-series chart marks; the meter and bullet fill on their track,
# the meter's 1px edge and the target tick; sparkline strokes.
EDGES = (
    [("border-strong", bg) for bg in SURFACES + TINTS]
    + [("focus", bg) for bg in SURFACES + ["accent-tint", "warning-tint"]]
    + [("accent", bg) for bg in SURFACES]
    + [("chart-1", "surface"), ("chart-1", "surface-raised"), ("ramp-2", "surface"), ("ramp-2", "ramp-track"),
       ("ink", "ramp-track"), ("ink-2", "surface"), ("ink-3", "surface"), ("negative", "surface")]
)


def _block(css: str, selector: str) -> dict[str, str]:
    """The `--name: #rrggbb` declarations of the first rule whose selector is exactly `selector`."""
    m = re.search(re.escape(selector) + r"\s*\{([^{}]*)\}", css)
    if not m:
        raise SystemExit(f"no {selector} block in {CSS_PATH.name}")
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})\b", m.group(1)))


def schemes(css: str | None = None) -> dict[str, dict[str, str]]:
    css = css if css is not None else CSS_PATH.read_text()
    light = _block(css, ":root")
    dark_media = re.search(r'@media \(prefers-color-scheme: dark\) \{ :root:not\(\[data-theme="light"\]\) \{([^{}]*)\}',
                           css)
    dark = _block(css, ':root[data-theme="dark"]')
    if not dark_media or dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})\b", dark_media.group(1))) != dark:
        raise SystemExit("the dark media-query block and the [data-theme=dark] block disagree")
    return {"light": light, "dark": {**light, **dark}}


def _luminance(hex_: str) -> float:
    rgb = [int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float:
    hi, lo = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _tok(scheme: dict[str, str], name: str) -> str:
    value = scheme.get(f"color-{name}") or scheme.get(name)
    if value is None:
        raise SystemExit(f"token --color-{name} / --{name} not found")
    return value


def check(css: str | None = None) -> list[dict]:
    """Every pair in both schemes: {scheme, kind, fg, bg, ratio, min, ok}."""
    rows = []
    for scheme, tokens in schemes(css).items():
        for kind, pairs, minimum in (("text", TEXT, TEXT_MIN), ("edge", EDGES, EDGE_MIN)):
            for fg, bg in pairs:
                ratio = contrast(_tok(tokens, fg), _tok(tokens, bg))
                rows.append({"scheme": scheme, "kind": kind, "fg": fg, "bg": bg, "ratio": ratio, "min": minimum,
                             "ok": ratio >= minimum})
    return rows


def main(argv: list[str]) -> int:
    rows = check()
    failing = [r for r in rows if not r["ok"]]
    shown = failing if "--failing" in argv else rows
    for r in shown:
        mark = "ok  " if r["ok"] else "FAIL"
        print(f"{mark} {r['scheme']:<5} {r['kind']:<4} {r['fg']:>14} on {r['bg']:<15} {r['ratio']:5.2f}:1 (min {r['min']})")
    for scheme in ("light", "dark"):
        mine = [r for r in rows if r["scheme"] == scheme]
        low_text = min((r for r in mine if r["kind"] == "text"), key=lambda r: r["ratio"])
        low_edge = min((r for r in mine if r["kind"] == "edge"), key=lambda r: r["ratio"])
        print(f"{scheme}: {sum(r['ok'] for r in mine)}/{len(mine)} pass; lowest text {low_text['fg']} on "
              f"{low_text['bg']} {low_text['ratio']:.2f}:1, lowest edge {low_edge['fg']} on {low_edge['bg']} "
              f"{low_edge['ratio']:.2f}:1")
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
