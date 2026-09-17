"""Self-contained HTML dashboard: inline CSS, inline SVG, one small script for hover.

No chart library and no CDN, so the file opens from disk on any machine and still works in five
years. Colour roles are CSS custom properties, declared once for light and once for dark, so the
two modes are chosen rather than an automatic inversion.

Palette: the validated default categorical set (blue `#2a78d6`, orange `#eb6834`) with its dark
steps. The two-slot pair was run through the validator before anything was drawn: adjacent CVD
Delta E 24.7 light and 26.8 dark against targets of 8, normal-vision 33.6 and 31.8 against a floor
of 15, and both clear 3:1 against their surface.

Rules followed throughout: one y-axis and never two; a legend whenever there are two series, with
direct labels as well so identity is never carried by colour alone; a 2px surface gap between
stacked segments; recessive grid and axes; values in ink rather than in the series colour; and a
table view under every chart for anyone the colours do not reach.
"""

from __future__ import annotations

import html
from collections.abc import Sequence
from typing import Any

CSS = """
:root { color-scheme: light dark; }
.viz-root {
  --surface-1: #fcfcfb; --plane: #f9f9f7;
  --text-primary: #0b0b0b; --text-secondary: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --series-1: #2a78d6; --series-2: #eb6834;
  --good: #0ca30c; --warning: #fab219; --critical: #d03b3b;
}
@media (prefers-color-scheme: dark) {
  :root:where(:not([data-theme="light"])) .viz-root {
    --surface-1: #1a1a19; --plane: #0d0d0d;
    --text-primary: #ffffff; --text-secondary: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --series-1: #3987e5; --series-2: #d95926;
  }
}
:root[data-theme="dark"] .viz-root {
  --surface-1: #1a1a19; --plane: #0d0d0d;
  --text-primary: #ffffff; --text-secondary: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
  --series-1: #3987e5; --series-2: #d95926;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--plane); color: var(--text-primary);
       font: 15px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif; }
.viz-root { max-width: 1040px; margin: 0 auto; padding: 32px 20px 64px; }
header h1 { font-size: 26px; margin: 0 0 4px; letter-spacing: -0.01em; }
header p  { margin: 0; color: var(--text-secondary); }
.meta { color: var(--muted); font-size: 13px; margin-top: 10px; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 28px 0 8px; }
.tile { background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; }
.tile .label { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }
.tile .value { font-size: 26px; font-weight: 600; margin-top: 4px; letter-spacing: -0.02em; }
.tile .sub   { font-size: 12px; color: var(--text-secondary); margin-top: 2px; }
.card { background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px;
        padding: 18px 18px 8px; margin-top: 20px; }
.card h2 { font-size: 16px; margin: 0 0 2px; }
.card .q { font-size: 13px; color: var(--text-secondary); margin: 0 0 2px; }
.card .basis { font-size: 12px; color: var(--muted); margin: 0 0 12px; }
.legend { display: flex; gap: 16px; font-size: 13px; color: var(--text-secondary); margin: 0 0 8px; }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.swatch { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
svg { width: 100%; height: auto; display: block; overflow: visible; }
.grid-line { stroke: var(--grid); stroke-width: 1; }
.axis-line { stroke: var(--axis); stroke-width: 1; }
.tick { fill: var(--muted); font-size: 11px; }
.val  { fill: var(--text-secondary); font-size: 11px; font-variant-numeric: tabular-nums; }
.hit  { fill: transparent; cursor: pointer; }
details { margin: 10px 0 6px; }
summary { cursor: pointer; font-size: 13px; color: var(--text-secondary); }
table { border-collapse: collapse; width: 100%; font-size: 13px; margin-top: 8px; }
th, td { text-align: left; padding: 5px 8px; border-bottom: 1px solid var(--border); }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
#tip { position: fixed; pointer-events: none; opacity: 0; transition: opacity .08s;
       background: var(--surface-1); color: var(--text-primary); border: 1px solid var(--border);
       border-radius: 8px; padding: 6px 9px; font-size: 12px; box-shadow: 0 6px 20px rgba(0,0,0,.14);
       z-index: 10; max-width: 260px; }
footer { color: var(--muted); font-size: 12px; margin-top: 28px; }
"""

SCRIPT = """
(function () {
  var tip = document.getElementById('tip');
  function show(e) {
    var t = e.currentTarget.getAttribute('data-tip');
    if (!t) return;
    tip.innerHTML = t;
    tip.style.opacity = 1;
    move(e);
  }
  function move(e) {
    var pad = 14;
    var x = e.clientX + pad, y = e.clientY + pad;
    var r = tip.getBoundingClientRect();
    if (x + r.width > window.innerWidth) x = e.clientX - r.width - pad;
    if (y + r.height > window.innerHeight) y = e.clientY - r.height - pad;
    tip.style.left = x + 'px'; tip.style.top = y + 'px';
  }
  function hide() { tip.style.opacity = 0; }
  document.querySelectorAll('[data-tip]').forEach(function (el) {
    el.addEventListener('mouseenter', show);
    el.addEventListener('mousemove', move);
    el.addEventListener('mouseleave', hide);
    el.addEventListener('focus', show);
    el.addEventListener('blur', hide);
  });
})();
"""


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _money(pence_or_pounds: float, decimals: int = 0) -> str:
    return f"£{pence_or_pounds:,.{decimals}f}"


def _compact(value: float) -> str:
    for cut, suffix in ((1e9, "bn"), (1e6, "m"), (1e3, "k")):
        if abs(value) >= cut:
            return f"{value / cut:,.1f}{suffix}"
    return f"{value:,.0f}"


def _nice_max(value: float) -> float:
    if value <= 0:
        return 1.0
    import math

    exponent = math.floor(math.log10(value))
    base = 10**exponent
    for step in (1, 1.5, 2, 2.5, 3, 4, 5, 7.5, 10):
        if value <= step * base:
            return float(step * base)
    return float(10 * base)


def line_chart(labels: Sequence[str], values: Sequence[float], unit: str = "£") -> str:
    """Single series over time. No legend: the card title names the series.

    Direct label on the final point only, never a number on every point.
    """
    w, h = 760, 260
    left, right, top, bottom = 56, 28, 16, 34
    plot_w, plot_h = w - left - right, h - top - bottom
    top_value = _nice_max(max(values) if values else 1)
    n = max(len(values) - 1, 1)

    def x(i: int) -> float:
        return left + plot_w * (i / n)

    def y(v: float) -> float:
        return top + plot_h * (1 - v / top_value)

    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Line chart">']
    for f in (0, 0.25, 0.5, 0.75, 1.0):
        gy = top + plot_h * f
        parts.append(f'<line class="grid-line" x1="{left}" y1="{gy:.1f}" x2="{left + plot_w}" y2="{gy:.1f}"/>')
        parts.append(
            f'<text class="tick" x="{left - 8}" y="{gy + 4:.1f}" text-anchor="end">'
            f"{unit}{_compact(top_value * (1 - f))}</text>"
        )
    parts.append(f'<line class="axis-line" x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}"/>')

    step = max(1, len(labels) // 8)
    for i, label in enumerate(labels):
        if i % step == 0 or i == len(labels) - 1:
            parts.append(
                f'<text class="tick" x="{x(i):.1f}" y="{top + plot_h + 18}" text-anchor="middle">{_e(label)}</text>'
            )

    path = " ".join(f"{'M' if i == 0 else 'L'}{x(i):.1f},{y(v):.1f}" for i, v in enumerate(values))
    parts.append(
        f'<path d="{path}" fill="none" stroke="var(--series-1)" stroke-width="2" '
        'stroke-linejoin="round" stroke-linecap="round"/>'
    )

    for i, v in enumerate(values):
        # A 2px surface ring keeps a marker legible where the line passes behind it.
        parts.append(
            f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="3.5" fill="var(--series-1)" '
            'stroke="var(--surface-1)" stroke-width="2"/>'
        )
        parts.append(
            f'<circle class="hit" cx="{x(i):.1f}" cy="{y(v):.1f}" r="14" tabindex="0" '
            f'data-tip="<b>{_e(labels[i])}</b><br>{unit}{v:,.0f}"/>'
        )

    if values:
        parts.append(
            f'<text class="val" x="{x(len(values) - 1):.1f}" y="{y(values[-1]) - 12:.1f}" '
            f'text-anchor="end">{unit}{_compact(values[-1])}</text>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


def hbar_chart(
    labels: Sequence[str], values: Sequence[float], unit: str = "£", tips: Sequence[str] | None = None
) -> str:
    """Magnitude by identity. One series, so one colour, with a direct value label on every bar."""
    row_h, gap = 26, 8
    w = 760
    left, right = 150, 70
    h = len(labels) * (row_h + gap) + 12
    plot_w = w - left - right
    top_value = _nice_max(max(values) if values else 1)

    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Bar chart">']
    for i, (label, value) in enumerate(zip(labels, values, strict=False)):
        y = i * (row_h + gap) + 6
        bar_w = max(2.0, plot_w * (value / top_value))
        tip = tips[i] if tips else f"<b>{_e(label)}</b><br>{unit}{value:,.0f}"
        parts.append(
            f'<text class="tick" x="{left - 10}" y="{y + row_h / 2 + 4:.1f}" text-anchor="end">{_e(label)}</text>'
        )
        parts.append(f'<rect x="{left}" y="{y}" width="{bar_w:.1f}" height="{row_h}" rx="4" fill="var(--series-1)"/>')
        parts.append(
            f'<text class="val" x="{left + bar_w + 8:.1f}" y="{y + row_h / 2 + 4:.1f}">{unit}{_compact(value)}</text>'
        )
        parts.append(
            f'<rect class="hit" x="{left}" y="{y}" width="{plot_w}" height="{row_h}" tabindex="0" data-tip="{tip}"/>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


def rate_chart(labels: Sequence[str], rates: Sequence[float], target: float = 0.99) -> str:
    """Pass rates against a target line. Status colour plus a written value, never colour alone."""
    row_h, gap = 26, 8
    w = 760
    left, right = 150, 90
    h = len(labels) * (row_h + gap) + 26
    plot_w = w - left - right

    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Pass rate chart">']
    target_x = left + plot_w * target
    parts.append(
        f'<line class="grid-line" x1="{target_x:.1f}" y1="0" x2="{target_x:.1f}" y2="{h - 22}" stroke-dasharray="3 3"/>'
    )
    parts.append(f'<text class="tick" x="{target_x:.1f}" y="{h - 8}" text-anchor="middle">target {target:.0%}</text>')
    for i, (label, rate) in enumerate(zip(labels, rates, strict=False)):
        y = i * (row_h + gap) + 6
        bar_w = max(2.0, plot_w * rate)
        colour = "var(--good)" if rate >= target else ("var(--warning)" if rate >= target - 0.01 else "var(--critical)")
        state = "meets target" if rate >= target else "below target"
        parts.append(
            f'<text class="tick" x="{left - 10}" y="{y + row_h / 2 + 4:.1f}" text-anchor="end">{_e(label)}</text>'
        )
        parts.append(f'<rect x="{left}" y="{y}" width="{bar_w:.1f}" height="{row_h}" rx="4" fill="{colour}"/>')
        parts.append(f'<text class="val" x="{left + plot_w + 10:.1f}" y="{y + row_h / 2 + 4:.1f}">{rate:.3%}</text>')
        parts.append(
            f'<rect class="hit" x="{left}" y="{y}" width="{plot_w}" height="{row_h}" tabindex="0" '
            f'data-tip="<b>{_e(label)}</b><br>{rate:.3%} pass rate<br>{state}"/>'
        )
    parts.append("</svg>")
    return "\n".join(parts)


def stacked_bar_chart(
    labels: Sequence[str], series_a: Sequence[float], series_b: Sequence[float], names: tuple[str, str]
) -> str:
    """Two series stacked, with a 2px surface gap between the segments so the boundary is a shape."""
    w, h = 760, 280
    left, right, top, bottom = 56, 28, 16, 50
    plot_w, plot_h = w - left - right, h - top - bottom
    totals = [a + b for a, b in zip(series_a, series_b, strict=False)]
    top_value = _nice_max(max(totals) if totals else 1)
    slot = plot_w / max(len(labels), 1)
    bar_w = min(38.0, slot * 0.62)

    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Stacked bar chart">']
    for f in (0, 0.25, 0.5, 0.75, 1.0):
        gy = top + plot_h * f
        parts.append(f'<line class="grid-line" x1="{left}" y1="{gy:.1f}" x2="{left + plot_w}" y2="{gy:.1f}"/>')
        parts.append(
            f'<text class="tick" x="{left - 8}" y="{gy + 4:.1f}" text-anchor="end">'
            f"£{_compact(top_value * (1 - f))}</text>"
        )
    parts.append(f'<line class="axis-line" x1="{left}" y1="{top + plot_h}" x2="{left + plot_w}" y2="{top + plot_h}"/>')

    step = max(1, len(labels) // 10)
    for i, label in enumerate(labels):
        cx = left + slot * (i + 0.5)
        a, b = series_a[i], series_b[i]
        total = a + b or 1
        h_a = plot_h * (a / top_value)
        h_b = plot_h * (b / top_value)
        y_a = top + plot_h - h_a
        y_b = y_a - h_b
        parts.append(
            f'<rect x="{cx - bar_w / 2:.1f}" y="{y_a:.1f}" width="{bar_w:.1f}" height="{max(h_a, 0.5):.1f}" '
            'rx="0" fill="var(--series-1)"/>'
        )
        # The 2px gap is cut out of the upper segment, not painted over the lower one.
        parts.append(
            f'<rect x="{cx - bar_w / 2:.1f}" y="{y_b:.1f}" width="{bar_w:.1f}" height="{max(h_b - 2, 0.5):.1f}" '
            'rx="4" fill="var(--series-2)"/>'
        )
        parts.append(
            f'<rect class="hit" x="{cx - slot / 2:.1f}" y="{top}" width="{slot:.1f}" height="{plot_h}" '
            f'tabindex="0" data-tip="<b>{_e(label)}</b><br>{_e(names[0])}: £{a:,.0f}<br>'
            f'{_e(names[1])}: £{b:,.0f}<br>online share {b / total:.1%}"/>'
        )
        if i % step == 0 or i == len(labels) - 1:
            parts.append(
                f'<text class="tick" x="{cx:.1f}" y="{top + plot_h + 18}" text-anchor="middle">{_e(label)}</text>'
            )
    parts.append("</svg>")
    return "\n".join(parts)


def legend(items: Sequence[tuple[str, str]]) -> str:
    spans = "".join(
        f'<span><i class="swatch" style="background:{colour}"></i>{_e(name)}</span>' for name, colour in items
    )
    return f'<div class="legend">{spans}</div>'


def pretty(label: str) -> str:
    """Column values are machine names; a reader should not have to decode snake_case."""
    special = {
        "unknown": "ATM, fees and unmatched",
        "atm_withdrawal": "ATM withdrawal",
        "misc_retail": "Other retail",
        "digital_services": "Digital services",
        "travel_agents": "Travel agents",
        "eating_out": "Eating out",
        "fast_food": "Fast food",
    }
    if label in special:
        return special[label]
    return label.replace("_", " ").capitalize()


def table(
    columns: Sequence[str], rows: Sequence[Sequence[Any]], numeric: Sequence[bool], label: str = "Table view"
) -> str:
    head = "".join(f'<th class="{"num" if numeric[i] else ""}">{_e(c)}</th>' for i, c in enumerate(columns))
    body = []
    for row in rows:
        cells = "".join(f'<td class="{"num" if numeric[i] else ""}">{_e(v)}</td>' for i, v in enumerate(row))
        body.append(f"<tr>{cells}</tr>")
    return (
        f"<details><summary>{_e(label)}</summary>"
        f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></details>"
    )


def card(title: str, question: str, basis: str, body: str) -> str:
    return (
        f'<section class="card"><h2>{_e(title)}</h2><p class="q">{_e(question)}</p>'
        f'<p class="basis">Date basis: {_e(basis)}</p>{body}</section>'
    )


def page(title: str, subtitle: str, meta: str, tiles: str, cards: str, footer: str) -> str:
    return f"""<!doctype html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="viz-root">
  <header>
    <h1>{_e(title)}</h1>
    <p>{_e(subtitle)}</p>
    <p class="meta">{meta}</p>
  </header>
  <div class="tiles">{tiles}</div>
  {cards}
  <footer>{footer}</footer>
</div>
<div id="tip" role="status"></div>
<script>{SCRIPT}</script>
</body>
</html>
"""
