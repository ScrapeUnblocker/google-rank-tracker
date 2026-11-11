"""Build the chart page: one self-contained ``index.html`` with no external assets.

Positions now (with the 7-day change), then a line chart per keyword: your site and each
competitor over the last ``report.days`` days, position 1 at the top. Hovering (or the
arrow keys) shows every site's position for a day; each chart has a table view too. The
workflow publishes the page to GitHub Pages; locally, open ``site/index.html``.
"""

from __future__ import annotations

import html
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .messages import depth_text, display_url, fmt_time
from .process import WeeklyDigest, build_digest, ranks_of
from .state import Check, Store

PRODUCT_URL = (
    "https://scrapeunblocker.com/?utm_source=github&utm_medium=integration"
    "&utm_campaign=example-repos"
)
REPO_URL = "https://github.com/ScrapeUnblocker/google-rank-tracker"

# Chart geometry, in SVG user units.
WIDTH, HEIGHT = 720, 240
LEFT, RIGHT, TOP, BOTTOM = 40, 52, 14, 30


@dataclass
class Series:
    name: str
    slot: int  # 1-8, fixed by config order so a site keeps its colour on every chart
    you: bool
    values: list[int | None]


def esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def y_max(series: Sequence[Series]) -> int:
    worst = max((v for s in series for v in s.values if v is not None), default=10)
    return 10 if worst <= 10 else int(math.ceil(worst / 10.0) * 10)


def y_ticks(top: int) -> list[int]:
    step = 5 if top <= 20 else 10 if top <= 50 else 20
    return [1, *range(step, top + 1, step)]


def x_positions(days: Sequence[date]) -> list[float]:
    width = WIDTH - LEFT - RIGHT
    if len(days) == 1:
        return [LEFT + width / 2]
    span = (days[-1] - days[0]).days or 1
    return [LEFT + (d - days[0]).days / span * width for d in days]


def y_of(position: int, top: int) -> float:
    height = HEIGHT - TOP - BOTTOM
    return TOP + (position - 1) / max(top - 1, 1) * height


def chart_svg(days: Sequence[date], series: Sequence[Series], label: str) -> str:
    xs = x_positions(days)
    top = y_max(series)
    parts = [f'<svg viewBox="0 0 {WIDTH} {HEIGHT}" role="img" aria-label="{esc(label)}">']
    for tick in y_ticks(top):
        y = y_of(tick, top)
        parts.append(
            f'<line class="grid" x1="{LEFT}" x2="{WIDTH - RIGHT}" y1="{y:.1f}" y2="{y:.1f}"/>'
        )
        parts.append(
            f'<text class="tick" x="{LEFT - 8}" y="{y + 4:.1f}" text-anchor="end">{tick}</text>'
        )
    step = max(1, math.ceil(len(days) / 6))
    shown = list(range(0, len(days), step))
    if len(days) > 1 and shown[-1] != len(days) - 1:
        if xs[-1] - xs[shown[-1]] < 60:
            shown.pop()
        shown.append(len(days) - 1)
    for i in shown:
        anchor = "middle"
        if len(days) > 1 and i in (0, len(days) - 1):
            anchor = "start" if i == 0 else "end"
        parts.append(
            f'<text class="tick" x="{xs[i]:.1f}" y="{HEIGHT - 8}" text-anchor="{anchor}">'
            f"{days[i].day} {days[i]:%b}</text>"
        )
    # Competitors first, your site last so its line is drawn on top.
    for s in sorted(series, key=lambda s: s.you):
        segments: list[list[tuple[float, float]]] = [[]]
        for x, v in zip(xs, s.values, strict=True):
            if v is None:
                segments.append([])
            else:
                segments[-1].append((x, y_of(v, top)))
        for seg in segments:
            if len(seg) > 1:
                d = " ".join(
                    f"{'M' if i == 0 else 'L'}{x:.1f} {y:.1f}" for i, (x, y) in enumerate(seg)
                )
                parts.append(f'<path class="line s{s.slot}" d="{d}"/>')
            elif len(seg) == 1:
                x, y = seg[0]
                parts.append(f'<circle class="dot s{s.slot}" cx="{x:.1f}" cy="{y:.1f}" r="4"/>')
        latest = s.values[-1] if s.values else None
        if latest is not None and len(segments[-1]) > 1:
            # End dot on a line; a lone point already is a dot.
            y = y_of(latest, top)
            parts.append(f'<circle class="dot s{s.slot}" cx="{xs[-1]:.1f}" cy="{y:.1f}" r="4"/>')
        if s.you and latest is not None:
            y = y_of(latest, top) + 4
            parts.append(f'<text class="end" x="{xs[-1] + 9:.1f}" y="{y:.1f}">#{latest}</text>')
    parts.append(
        f'<line class="cross" x1="0" x2="0" y1="{TOP}" y2="{HEIGHT - BOTTOM}" visibility="hidden"/>'
    )
    parts.append("</svg>")
    return "".join(parts)


def keyword_series(checks: Sequence[Check], sites: Sequence, you_spec: str) -> list[Series]:
    out = []
    for slot, site in enumerate(sites, start=1):
        values = [ranks_of(c, [site])[site.spec].position for c in checks]
        out.append(Series(name=site.spec, slot=slot, you=site.spec == you_spec, values=values))
    return out


def change_cell(row) -> str:
    if not row.compared:
        return '<span class="muted">new</span>'
    if row.position is not None and row.previous is None:
        return '<span class="good">\N{BLACK UP-POINTING TRIANGLE} in</span>'
    if row.position is None and row.previous is not None:
        return '<span class="bad">\N{BLACK DOWN-POINTING TRIANGLE} out</span>'
    delta = row.delta
    if not delta:
        return '<span class="muted">=</span>'
    if delta > 0:
        return f'<span class="good">\N{BLACK UP-POINTING TRIANGLE} {delta}</span>'
    return f'<span class="bad">\N{BLACK DOWN-POINTING TRIANGLE} {-delta}</span>'


def pos_cell(value: int | None) -> str:
    return f"{value}" if value is not None else '<span class="muted">\N{EN DASH}</span>'


def tiles(digest: WeeklyDigest, ai_shown: int, ai_cited: int) -> str:
    def tile(label: str, value: str, note: str) -> str:
        return (
            f'<div class="tile"><div class="tile-label">{esc(label)}</div>'
            f'<div class="tile-value">{esc(value)}</div>'
            f'<div class="tile-note">{esc(note)}</div></div>'
        )

    def was(top: int) -> str:
        if not digest.compared or digest.since is None:
            return "first check"
        return f"{digest.count(top, previous=True)} on {digest.since.day} {digest.since:%b}"

    total = len(digest.rows)
    items = [
        tile("Keywords in the top 3", str(digest.count(3)), was(3)),
        tile("Keywords in the top 10", str(digest.count(10)), was(10)),
        tile(f"On {depth_text(digest.pages)}", f"{digest.ranked} of {total}", "keywords"),
        tile(
            "Cited in AI Overviews",
            f"{ai_cited} of {ai_shown}" if ai_shown else "\N{EN DASH}",
            "keywords with an AI Overview" if ai_shown else "no AI Overview on the latest checks",
        ),
    ]
    return '<section class="tiles">' + "".join(items) + "</section>"


def summary_table(digest: WeeklyDigest) -> str:
    comps = [c.site for c in digest.competitors]
    head = "".join(f'<th class="num">{esc(c)}</th>' for c in comps)
    rows = []
    for r in sorted(digest.rows, key=lambda r: (r.position is None, r.position or 0, r.label)):
        page = (
            f'<a href="{esc(r.url)}">{esc(display_url(r.url, 64))}</a>'
            if r.url
            else '<span class="muted">not ranked</span>'
        )
        if r.ai_cited:
            page += ' <span class="pill">AI Overview</span>'
        cells = "".join(f'<td class="num">{pos_cell(r.competitors.get(c))}</td>' for c in comps)
        rows.append(
            f'<tr><td><a href="#{anchor(r.label)}">{esc(r.label)}</a></td>'
            f'<td class="num strong">{pos_cell(r.position)}</td>'
            f'<td class="num">{change_cell(r)}</td>{cells}<td>{page}</td></tr>'
        )
    since = (
        f"Change since {digest.since.day} {digest.since:%b}."
        if digest.since
        else "Change shows from the second week."
    )
    return (
        '<section class="card"><h2>Positions now</h2>'
        f'<p class="sub">{esc(since)} A dash: not on {esc(depth_text(digest.pages))}.</p>'
        '<div class="scroll"><table><thead><tr><th>Keyword</th><th class="num">You</th>'
        f'<th class="num">Change</th>{head}<th>Your ranking page</th></tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div></section>"
    )


def anchor(label: str) -> str:
    slug = "".join(ch if ch.isalnum() else "-" for ch in label.lower()).strip("-")
    return "kw-" + "-".join(filter(None, slug.split("-")))


def keyword_card(label: str, checks: Sequence[Check], series: Sequence[Series], pages: int) -> str:
    days = [c.day for c in checks]
    legend = "".join(
        f'<li><svg class="key" viewBox="0 0 16 4" aria-hidden="true"><line class="line s{s.slot}" '
        f'x1="1" x2="15" y1="2" y2="2"/></svg>{esc(s.name)}{" (you)" if s.you else ""}</li>'
        for s in series
    )
    data = {
        "days": [f"{d.day} {d:%b %Y}" for d in days],
        "x": [round(x, 1) for x in x_positions(days)],
        "series": [
            {"name": s.name + (" (you)" if s.you else ""), "slot": s.slot, "values": s.values}
            for s in series
        ],
    }
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    table_head = "".join(f'<th class="num">{esc(s.name)}</th>' for s in series)
    table_rows = "".join(
        f"<tr><td>{d.isoformat()}</td>"
        + "".join(f'<td class="num">{pos_cell(s.values[i])}</td>' for s in series)
        + "</tr>"
        for i, d in reversed(list(enumerate(days)))
    )
    aria = f"Google position for {label} over {len(days)} check(s); position 1 is at the top."
    return (
        f'<article class="card" id="{anchor(label)}"><h3>{esc(label)}</h3>'
        f'<ul class="legend">{legend}</ul>'
        f'<div class="chart" tabindex="0" aria-label="{esc(aria)}">{chart_svg(days, series, aria)}'
        f'<div class="tip" hidden></div><script type="application/json">{blob}</script></div>'
        f'<details><summary>Table</summary><div class="scroll"><table><thead><tr><th>Day</th>'
        f"{table_head}</tr></thead><tbody>{table_rows}</tbody></table></div></details>"
        "</article>"
    )


def build_report(config, store: Store, now: datetime) -> str:
    today = now.date()
    digest = build_digest(config, store, today)
    title = config.report.title or f"Google rankings: {config.site.spec}"
    sub = [
        f"{config.country.upper() or 'Any country'}",
        depth_text(config.pages),
        f"{len(config.keywords)} keywords",
        f"updated {fmt_time(now)}",
    ]
    body: list[str] = []
    if digest is None:
        body.append(
            '<section class="card"><p>Nothing checked yet. Run '
            "<code>google-rank-tracker run</code> first.</p></section>"
        )
    else:
        ai_shown = ai_cited = 0
        cards = []
        since = today - timedelta(days=config.report.days)
        for keyword in config.keywords:
            checks = store.checks(keyword, since)
            if not checks:
                continue
            latest = checks[-1]
            if latest.ai_overview:
                ai_shown += 1
                ai_cited += bool(ranks_of(latest, [config.site])[config.site.spec].ai_cited)
            series = keyword_series(checks, config.sites, config.site.spec)
            cards.append(keyword_card(config.label(keyword), checks, series, config.pages))
        body.append(tiles(digest, ai_shown, ai_cited))
        body.append(summary_table(digest))
        body.append(
            f'<h2 class="section">Position over the last {config.report.days} days</h2>'
            '<p class="sub">Lower is better. A gap: not on '
            f"{esc(depth_text(config.pages))} that day.</p>"
        )
        body.extend(cards)
    return PAGE.format(
        title=esc(title),
        sub=esc(" \N{MIDDLE DOT} ".join(sub)),
        body="\n".join(body),
        product=esc(PRODUCT_URL),
        repo=esc(REPO_URL),
        css=CSS,
        js=JS,
    )


def write_report(config, store: Store, out_dir: str | Path, now: datetime) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "index.html"
    path.write_text(build_report(config, store, now), encoding="utf-8", newline="\n")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    return path


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<main class="viz-root">
<header><h1>{title}</h1><p class="sub">{sub}</p></header>
{body}
<footer>Google results via the <a href="{product}">ScrapeUnblocker</a> serpApi.
Built with <a href="{repo}">google-rank-tracker</a>.</footer>
</main>
<script>{js}</script>
</body>
</html>
"""

CSS = """
.viz-root {
  color-scheme: light;
  --page: #f9f9f7; --surface-1: #fcfcfb; --text-primary: #0b0b0b; --text-secondary: #52514e;
  --muted: #898781; --grid: #e1e0d9; --axis: #c3c2b7; --border: rgba(11,11,11,0.10);
  --good: #006300; --bad: #d03b3b;
  --series-1: #2a78d6; --series-2: #eb6834; --series-3: #1baf7a; --series-4: #eda100;
  --series-5: #e87ba4; --series-6: #008300; --series-7: #4a3aa7; --series-8: #e34948;
}
@media (prefers-color-scheme: dark) {
  .viz-root {
    color-scheme: dark;
    --page: #0d0d0d; --surface-1: #1a1a19; --text-primary: #ffffff; --text-secondary: #c3c2b7;
    --muted: #898781; --grid: #2c2c2a; --axis: #383835; --border: rgba(255,255,255,0.10);
    --good: #0ca30c; --bad: #e66767;
    --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70; --series-4: #c98500;
    --series-5: #d55181; --series-6: #008300; --series-7: #9085e9; --series-8: #e66767;
  }
  body { background: #0d0d0d; }
}
* { box-sizing: border-box; }
body { margin: 0; background: #f9f9f7; }
.viz-root {
  font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; color: var(--text-primary);
  background: var(--page); max-width: 1040px; margin: 0 auto; padding: 28px 20px 40px;
}
h1 { font-size: 26px; margin: 0 0 4px; font-weight: 600; }
h2 { font-size: 18px; margin: 0 0 4px; font-weight: 600; }
h2.section { margin: 28px 0 4px; }
h3 { font-size: 16px; margin: 0 0 8px; font-weight: 600; }
.sub { color: var(--text-secondary); margin: 0 0 14px; }
a { color: inherit; text-decoration-color: var(--axis); text-underline-offset: 2px; }
a:hover { text-decoration-color: currentColor; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 12px;
  margin: 18px 0; }
.tile, .card { background: var(--surface-1); border: 1px solid var(--border); border-radius: 10px; }
.tile { padding: 14px 16px; }
.tile-label { color: var(--text-secondary); font-size: 13px; }
.tile-value { font-size: 28px; font-weight: 600; margin: 2px 0; }
.tile-note { color: var(--muted); font-size: 13px; }
.card { padding: 16px 18px; margin: 12px 0; }
.scroll { overflow-x: auto; }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--grid);
  white-space: nowrap; }
th { color: var(--text-secondary); font-weight: 500; font-size: 13px; }
.num { text-align: right; font-variant-numeric: tabular-nums; }
.strong { font-weight: 600; }
.muted { color: var(--muted); }
.good { color: var(--good); }
.bad { color: var(--bad); }
.pill { font-size: 12px; color: var(--text-secondary); border: 1px solid var(--border);
  border-radius: 99px; padding: 1px 7px; margin-left: 6px; }
.legend { list-style: none; display: flex; flex-wrap: wrap; gap: 4px 16px; padding: 0;
  margin: 0 0 6px; color: var(--text-secondary); font-size: 13px; }
.legend li { display: flex; align-items: center; gap: 6px; }
.key { width: 16px; height: 4px; overflow: visible; }
.chart { position: relative; outline: none; }
.chart:focus-visible { box-shadow: 0 0 0 2px var(--series-1); border-radius: 6px; }
.chart svg { display: block; width: 100%; height: auto; }
.grid { stroke: var(--grid); stroke-width: 1; vector-effect: non-scaling-stroke; }
.tick { fill: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.end { fill: var(--text-primary); font-size: 12px; font-weight: 600; }
.line { fill: none; stroke-width: 2; stroke-linejoin: round; stroke-linecap: round;
  vector-effect: non-scaling-stroke; }
.dot { stroke: var(--surface-1); stroke-width: 2; vector-effect: non-scaling-stroke; }
.cross { stroke: var(--axis); stroke-width: 1; vector-effect: non-scaling-stroke; }
.s1 { stroke: var(--series-1); } .dot.s1 { fill: var(--series-1); }
.s2 { stroke: var(--series-2); } .dot.s2 { fill: var(--series-2); }
.s3 { stroke: var(--series-3); } .dot.s3 { fill: var(--series-3); }
.s4 { stroke: var(--series-4); } .dot.s4 { fill: var(--series-4); }
.s5 { stroke: var(--series-5); } .dot.s5 { fill: var(--series-5); }
.s6 { stroke: var(--series-6); } .dot.s6 { fill: var(--series-6); }
.s7 { stroke: var(--series-7); } .dot.s7 { fill: var(--series-7); }
.s8 { stroke: var(--series-8); } .dot.s8 { fill: var(--series-8); }
.dot { stroke: var(--surface-1); }
.tip { position: absolute; top: 4px; pointer-events: none; background: var(--surface-1);
  border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px; font-size: 13px;
  box-shadow: 0 2px 10px rgba(0,0,0,0.12); min-width: 170px; }
.tip-day { color: var(--text-secondary); margin-bottom: 4px; }
.tip-row { display: flex; align-items: center; gap: 8px; }
.tip-row b { min-width: 30px; text-align: right; font-variant-numeric: tabular-nums; }
.tip-row span { color: var(--text-secondary); }
details { margin-top: 8px; }
summary { cursor: pointer; color: var(--text-secondary); font-size: 13px; }
footer { margin-top: 28px; color: var(--muted); font-size: 13px; }
code { font-size: 13px; }
"""

JS = """
(function () {
  var NS = "http://www.w3.org/2000/svg";
  document.querySelectorAll(".chart").forEach(function (chart) {
    var data = JSON.parse(chart.querySelector("script").textContent);
    var svg = chart.querySelector("svg");
    var cross = svg.querySelector(".cross");
    var tip = chart.querySelector(".tip");
    var width = svg.viewBox.baseVal.width;
    var current = data.days.length - 1;

    function show(i) {
      current = i;
      var x = data.x[i];
      cross.setAttribute("x1", x);
      cross.setAttribute("x2", x);
      cross.setAttribute("visibility", "visible");
      tip.textContent = "";
      var day = document.createElement("div");
      day.className = "tip-day";
      day.textContent = data.days[i];
      tip.appendChild(day);
      data.series.forEach(function (s) {
        var row = document.createElement("div");
        row.className = "tip-row";
        var key = document.createElementNS(NS, "svg");
        key.setAttribute("class", "key");
        key.setAttribute("viewBox", "0 0 16 4");
        var line = document.createElementNS(NS, "line");
        line.setAttribute("class", "line s" + s.slot);
        line.setAttribute("x1", "1"); line.setAttribute("x2", "15");
        line.setAttribute("y1", "2"); line.setAttribute("y2", "2");
        key.appendChild(line);
        var value = document.createElement("b");
        value.textContent = s.values[i] == null ? "\\u2013" : "#" + s.values[i];
        var name = document.createElement("span");
        name.textContent = s.name;
        row.appendChild(key); row.appendChild(value); row.appendChild(name);
        tip.appendChild(row);
      });
      tip.hidden = false;
      var px = x / width * chart.clientWidth;
      var left = px + 14;
      if (left + tip.offsetWidth > chart.clientWidth) left = px - tip.offsetWidth - 14;
      tip.style.left = Math.max(0, left) + "px";
    }
    function hide() { cross.setAttribute("visibility", "hidden"); tip.hidden = true; }
    function nearest(clientX) {
      var box = svg.getBoundingClientRect();
      var x = (clientX - box.left) / box.width * width;
      var best = 0;
      data.x.forEach(function (v, i) {
        if (Math.abs(v - x) < Math.abs(data.x[best] - x)) best = i;
      });
      return best;
    }
    chart.addEventListener("pointermove", function (e) { show(nearest(e.clientX)); });
    chart.addEventListener("pointerleave", hide);
    chart.addEventListener("focus", function () { show(current); });
    chart.addEventListener("blur", hide);
    chart.addEventListener("keydown", function (e) {
      if (e.key === "ArrowLeft") { show(Math.max(0, current - 1)); e.preventDefault(); }
      if (e.key === "ArrowRight") {
        show(Math.min(data.days.length - 1, current + 1));
        e.preventDefault();
      }
      if (e.key === "Escape") hide();
    });
  });
})();
"""
