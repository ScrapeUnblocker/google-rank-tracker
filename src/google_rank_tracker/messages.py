"""Render alerts, the first "now tracking" message and the weekly digest as chat text.

Three styles share one renderer: Slack mrkdwn, Discord Markdown and plain text (webhook,
``--dry-run``, logs). Long messages are split at line boundaries; a split table keeps its
code fence on both sides.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urlsplit

from .process import (
    AI_CITED,
    AI_LOST,
    ENTERED_TOP,
    LEFT_TOP,
    NEW_URL,
    RANKED,
    UNRANKED,
    Change,
    Tracking,
    WeeklyDigest,
)

ARROW = "\N{RIGHTWARDS ARROW}"
UP_MARK = "\N{BLACK UP-POINTING TRIANGLE}"
DOWN_MARK = "\N{BLACK DOWN-POINTING TRIANGLE}"
BULLET = "\N{BULLET}"
DOT = "\N{MIDDLE DOT}"
ELLIPSIS = "\N{HORIZONTAL ELLIPSIS}"
MINUS = "\N{MINUS SIGN}"
FENCE = "```"


@dataclass(frozen=True)
class Style:
    name: str
    bold_mark: str  # "" for plain text
    link_format: str  # "{text}" / "<{url}|{text}>" / "[{text}](<{url}>)"

    def escape(self, text: str) -> str:
        if self.name == "slack":
            return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if self.name == "discord":
            for ch in "\\*_~`|>":
                text = text.replace(ch, "\\" + ch)
        return text

    def bold(self, text: str) -> str:
        text = self.escape(text)
        return f"{self.bold_mark}{text}{self.bold_mark}" if self.bold_mark else text

    def link(self, url: str, text: str | None = None) -> str:
        shown = self.escape(text if text is not None else display_url(url))
        if self.name == "plain":
            return url if text is None else f"{text} ({url})"
        return self.link_format.format(url=url.replace(">", "%3E"), text=shown)


PLAIN = Style("plain", "", "{text}")
SLACK = Style("slack", "*", "<{url}|{text}>")
DISCORD = Style("discord", "**", "[{text}](<{url}>)")


def display_url(url: str | None, limit: int = 60) -> str:
    """``https://www.example.com/a/b/`` -> ``example.com/a/b``, shortened to ``limit``."""
    if not url:
        return ""
    parts = urlsplit(url)
    text = (parts.hostname or "").removeprefix("www.") + parts.path.rstrip("/")
    if parts.query and len(text) < 30:
        text += "?" + parts.query
    return text if len(text) <= limit else text[: limit - 1] + ELLIPSIS


def fmt_day(day: date) -> str:
    return f"{day:%a} {day.day} {day:%b %Y}"


def fmt_time(value: str | datetime | None) -> str:
    if value is None:
        return "-"
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return f"{value.day} {value:%b %Y %H:%M} UTC"


def pos(value: int | None) -> str:
    return f"#{value}" if value is not None else "-"


def signed(delta: int) -> str:
    if delta > 0:
        return f"+{delta}"
    if delta < 0:
        return f"{MINUS}{-delta}"
    return "="


def depth_text(pages: int) -> str:
    return "the first page" if pages == 1 else f"the first {pages} pages"


def country_text(country: str) -> str:
    return country.upper() if country else "any country"


def change_lines(change: Change, style: Style) -> list[str]:
    """One alert: a headline and, when it helps, the ranking page on the next line."""
    mark = UP_MARK if change.good else DOWN_MARK
    reasons = set(change.reasons)
    depth = depth_text(change.pages)
    if RANKED in reasons:
        move = f"now {pos(change.new)} (was not on {depth})"
    elif UNRANKED in reasons:
        move = f"no longer on {depth} (was {pos(change.old)})"
    elif change.delta:
        move = f"{pos(change.old)} {ARROW} {pos(change.new)} ({signed(change.delta)})"
    else:
        move = pos(change.new)
    extras = []
    if ENTERED_TOP in reasons:
        extras.append(f"into the top {change.top}")
    if LEFT_TOP in reasons:
        extras.append(f"out of the top {change.top}")
    if AI_CITED in reasons:
        extras.append("cited in Google's AI Overview")
    if AI_LOST in reasons:
        extras.append("no longer cited in Google's AI Overview")
    for o in change.overtakes:
        where = pos(o.position) if o.position is not None else f"not on {depth}"
        if o.above_you:
            extras.append(f"{style.escape(o.competitor)} moved above you ({where})")
        else:
            extras.append(f"you moved above {style.escape(o.competitor)} ({where})")
    head = f"{mark} {style.bold(change.label)}: {move}"
    if extras:
        head += f" {DOT} " + f" {DOT} ".join(extras)
    lines = [head]
    if NEW_URL in reasons and change.new_url:
        lines.append(
            f"    new page {style.link(change.new_url)} (was {display_url(change.old_url)})"
        )
    elif change.new_url and change.new is not None:
        lines.append(f"    {style.link(change.new_url)}")
    return lines


def render_changes(
    changes: Sequence[Change],
    style: Style,
    *,
    site: str,
    country: str,
    report_url: str | None = None,
    limit: int = 3900,
) -> list[str]:
    day = max(c.day for c in changes)
    title = f"Google rank changes for {site}"
    head = f"{style.bold(title)} {DOT} {country_text(country)} {DOT} {fmt_day(day)}"
    body: list[str] = []
    for change in sorted(changes, key=lambda c: (not c.good, c.label)):
        body.extend(change_lines(change, style))
    footer = [f"Chart: {style.link(report_url)}"] if report_url else []
    return split_message([head, ""], body, footer, limit=limit)


def render_tracking(
    items: Sequence[Tracking],
    style: Style,
    *,
    site: str,
    country: str,
    competitors: Sequence[str],
    digest_day: str | None,
    report_url: str | None = None,
    limit: int = 3900,
) -> list[str]:
    day = max(t.day for t in items)
    depth = depth_text(items[0].pages)
    title = f"Now tracking {len(items)} Google keyword{'s' if len(items) != 1 else ''} for {site}"
    head = f"{style.bold(title)} {DOT} {country_text(country)} {DOT} {fmt_day(day)}"
    body = []
    for t in sorted(items, key=lambda t: (t.position is None, t.position or 0, t.label)):
        if t.position is None:
            line = f"{BULLET} {style.bold(t.label)}: not on {depth}"
        else:
            line = f"{BULLET} {style.bold(t.label)}: {pos(t.position)} {style.link(t.url or '')}"
        if t.ai_cited:
            line += f" {DOT} cited in Google's AI Overview"
        body.append(line)
        ranked = sorted((p, s) for s, p in t.competitors.items() if p is not None)
        if ranked:
            body.append("    " + ", ".join(f"{style.escape(s)} {pos(p)}" for p, s in ranked))
    footer = [""]
    if competitors:
        footer.append(f"Compared with: {style.escape(', '.join(competitors))}")
    when = f" The weekly digest comes on {digest_day}s." if digest_day else ""
    footer.append(f"A message arrives here when a position changes.{when}")
    if report_url:
        footer.append(f"Chart: {style.link(report_url)}")
    return split_message([head, ""], body, footer, limit=limit)


def digest_table(digest: WeeklyDigest) -> list[str]:
    width = min(max(len(r.label) for r in digest.rows), 32)
    header = f"{'Keyword':<{width}}   Now  Change  Page"
    lines = [header]
    for r in sorted(digest.rows, key=lambda r: (r.position is None, r.position or 0, r.label)):
        label = r.label if len(r.label) <= width else r.label[: width - 1] + ELLIPSIS
        if not r.compared:
            change = "new"
        elif r.position is not None and r.previous is None:
            change = "in"
        elif r.position is None and r.previous is not None:
            change = "out"
        elif r.delta is None:
            change = ""
        else:
            change = signed(r.delta)
        page = display_url(r.url, 48)
        if r.ai_cited:
            page += " (AI)"
        lines.append(f"{label:<{width}}  {pos(r.position):>4}  {change:>6}  {page}".rstrip())
    return lines


def render_digest(
    digest: WeeklyDigest,
    style: Style,
    *,
    report_url: str | None = None,
    limit: int = 3900,
) -> list[str]:
    title = f"Weekly Google rankings for {digest.site}"
    head = [f"{style.bold(title)} {DOT} {country_text(digest.country)} {DOT} {fmt_day(digest.day)}"]
    total = len(digest.rows)
    depth = depth_text(digest.pages)

    def count(top: int) -> str:
        now = digest.count(top)
        if digest.compared:
            return f"Top {top}: {now} (was {digest.count(top, previous=True)})"
        return f"Top {top}: {now}"

    head.append(
        f"{count(3)} {DOT} {count(10)} {DOT} On {depth}: {digest.ranked} of {total} keywords"
    )
    if digest.since:
        head.append(f"Change since {fmt_day(digest.since)}.")
    if any(r.ai_cited for r in digest.rows):
        head.append("(AI): your page is cited in Google's AI Overview.")
    table = digest_table(digest)
    footer: list[str] = []
    if digest.competitors:
        footer.append("")
        footer.append(style.bold("Competitors"))
        for c in digest.competitors:
            if not c.ranked:
                footer.append(f"{BULLET} {style.escape(c.site)}: not on {depth} for any keyword")
                continue
            footer.append(
                f"{BULLET} {style.escape(c.site)}: above you on {c.above_you} of {total}, "
                f"best {pos(c.best)} ({style.escape(c.best_label or '')})"
            )
    if digest.new_in_top:
        footer.append("")
        footer.append(style.bold(f"New in the top {digest.new_in_top_n} this week"))
        for n in digest.new_in_top[:10]:
            tag = " (competitor)" if n.competitor else ""
            where = f"{pos(n.position)} for {style.escape(n.label)}"
            footer.append(f"{BULLET} {style.escape(n.host)}{tag}: {where}")
    if report_url:
        footer.append("")
        footer.append(f"Chart: {style.link(report_url)}")
    return split_message([*head, ""], table, footer, limit=limit, code=style.name != "plain")


def split_message(
    head: Sequence[str],
    body: Sequence[str],
    footer: Sequence[str],
    *,
    limit: int,
    code: bool = False,
) -> list[str]:
    """Join head + body + footer, splitting the body over several messages when needed.

    With ``code`` the body is a table inside a code fence, re-opened in every part.
    """
    open_, close = ([FENCE], [FENCE]) if code else ([], [])
    head, body, footer = list(head), list(body), list(footer)
    keep = body[:1] if code else []  # a table header is repeated in every part
    parts: list[list[str]] = []
    current: list[str] = []
    budget = limit - len("\n".join(head + open_ + close + footer)) - 2
    budget = max(budget, 200)
    size = 0
    for line in body:
        if current and size + len(line) + 1 > budget:
            parts.append(current)
            current = list(keep) if code else []
            size = sum(len(x) + 1 for x in current)
        current.append(line)
        size += len(line) + 1
    parts.append(current)
    messages = []
    for i, part in enumerate(parts):
        lines = list(head) if i == 0 else [f"{head[0]} ({i + 1}/{len(parts)})", ""]
        if part:
            lines += open_ + part + close
        if i == len(parts) - 1:
            lines += footer
        text = "\n".join(lines).strip("\n")
        messages.append(text if len(text) <= limit else text[: limit - 1] + ELLIPSIS)
    return messages


def summary_markdown(digest: WeeklyDigest | None, changes: Sequence[Change]) -> str:
    """The run's job summary on the GitHub Actions run page."""
    lines = ["## Google rankings", ""]
    if digest is None:
        return "\n".join([*lines, "Nothing checked yet."]) + "\n"
    lines.append(f"{digest.site}, {country_text(digest.country)}, checked {fmt_day(digest.day)}.")
    lines.append("")
    comps = [c.site for c in digest.competitors]
    lines.append("| Keyword | You | " + " | ".join(comps) + (" |" if comps else "") + " Page |")
    lines.append("|---|---:|" + "---:|" * len(comps) + "---|")
    for r in digest.rows:
        cells = [r.label.replace("|", "\\|"), pos(r.position)]
        cells += [pos(r.competitors.get(c)) for c in comps]
        cells.append(display_url(r.url, 70) + (" (AI Overview)" if r.ai_cited else ""))
        lines.append("| " + " | ".join(cells) + " |")
    if changes:
        lines += ["", "### Changes delivered", ""]
        for change in changes:
            lines.append("- " + change_lines(change, PLAIN)[0])
    return "\n".join(lines) + "\n"
