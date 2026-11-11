"""Turn stored checks into positions, alerts and the weekly digest.

Positions: a site's position for a keyword is the best (lowest) organic position of any
result on that site. Alerts compare the latest check with the baseline, which holds what
the last delivered message said; a position that drifts by one place a day still alerts
once the total reaches ``alerts.min_change``.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any
from urllib.parse import urlsplit

from .collect import Citation, Result
from .config import Config, Keyword, Site
from .state import BaselineRow, Check, Store

UP = "up"
DOWN = "down"
ENTERED_TOP = "entered_top"
LEFT_TOP = "left_top"
RANKED = "ranked"
UNRANKED = "unranked"
NEW_URL = "new_url"
AI_CITED = "ai_cited"
AI_LOST = "ai_lost"

GOOD = {UP, ENTERED_TOP, RANKED, AI_CITED}
BAD = {DOWN, LEFT_TOP, UNRANKED, AI_LOST}


@dataclass(frozen=True)
class Rank:
    position: int | None  # None: not in the results read
    url: str | None
    count: int  # results from this site in the results read
    ai_cited: bool | None  # None: Google showed no AI Overview


def page_key(url: str | None) -> str:
    """Compare ranking pages by host and path; tracking parameters and fragments vary."""
    if not url:
        return ""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower().removeprefix("www.")
    return host + (parts.path.rstrip("/") or "/")


def rank_in(
    site: Site,
    results: Sequence[Result],
    citations: Sequence[Citation] = (),
    ai_overview: bool = False,
) -> Rank:
    hits = [r for r in results if site.matches(r.host, r.path)]
    best = min(hits, key=lambda r: r.position) if hits else None
    cited: bool | None = None
    if ai_overview:
        cited = any(site.matches(c.host, urlsplit(c.url).path if c.url else "") for c in citations)
    return Rank(
        position=best.position if best else None,
        url=best.url if best else None,
        count=len(hits),
        ai_cited=cited,
    )


def ranks_of(check: Check, sites: Sequence[Site]) -> dict[str, Rank]:
    return {s.spec: rank_in(s, check.results, check.citations, check.ai_overview) for s in sites}


@dataclass
class Overtake:
    competitor: str
    position: int | None  # the competitor's position now
    above_you: bool  # True: the competitor moved above you


@dataclass
class Change:
    """Your site's position for one keyword changed enough to tell you."""

    keyword: Keyword
    label: str
    day: date
    pages: int
    old: int | None
    new: int | None
    old_url: str | None
    new_url: str | None
    reasons: list[str] = field(default_factory=list)
    overtakes: list[Overtake] = field(default_factory=list)
    top: int = 10

    kind = "change"

    @property
    def delta(self) -> int | None:
        """Places gained (positive) or lost (negative)."""
        if self.old is None or self.new is None:
            return None
        return self.old - self.new

    @property
    def good(self) -> bool:
        if self.reasons:
            first = next((r for r in self.reasons if r in GOOD | BAD), None)
            if first is not None:
                return first in GOOD
        return all(not o.above_you for o in self.overtakes)

    def to_dict(self) -> dict[str, Any]:
        return {
            "keyword": self.keyword.text,
            "country": self.keyword.country,
            "day": self.day.isoformat(),
            "old_position": self.old,
            "new_position": self.new,
            "change": self.delta,
            "old_url": self.old_url,
            "new_url": self.new_url,
            "reasons": list(self.reasons),
            "competitors": [asdict(o) for o in self.overtakes],
        }


@dataclass
class Tracking:
    """The first check of a keyword: where you stand when tracking starts."""

    keyword: Keyword
    label: str
    day: date
    pages: int
    position: int | None
    url: str | None
    ai_cited: bool | None
    competitors: dict[str, int | None]

    kind = "tracking"

    def to_dict(self) -> dict[str, Any]:
        return {
            "keyword": self.keyword.text,
            "country": self.keyword.country,
            "day": self.day.isoformat(),
            "position": self.position,
            "url": self.url,
            "ai_cited": self.ai_cited,
            "competitors": dict(self.competitors),
        }


Event = Change | Tracking


@dataclass
class Verdict:
    keyword: Keyword
    check: Check
    event: Event | None
    # Baseline rows to store: right away when there is no event, after delivery otherwise.
    baseline: dict[str, BaselineRow]


def evaluate(config: Config, check: Check, base: dict[str, BaselineRow]) -> Verdict:
    """Compare a keyword's latest check with what was last delivered."""
    you = config.site
    alerts = config.alerts
    ranks = ranks_of(check, config.sites)
    cur = ranks[you.spec]
    day = check.day.isoformat()
    label = config.label(check.keyword)

    def above(comp: Site) -> bool | None:
        if cur.position is None:
            return None
        cpos = ranks[comp.spec].position
        return cpos is not None and cpos < cur.position

    if you.spec not in base:
        rows = {you.spec: BaselineRow(cur.position, cur.url, cur.ai_cited, None, day)}
        for comp in config.competitors:
            rows[comp.spec] = BaselineRow(ranks[comp.spec].position, None, None, above(comp), day)
        event = None
        if config.delivery.baseline_message:
            event = Tracking(
                keyword=check.keyword,
                label=label,
                day=check.day,
                pages=config.pages,
                position=cur.position,
                url=cur.url,
                ai_cited=cur.ai_cited,
                competitors={c.spec: ranks[c.spec].position for c in config.competitors},
            )
        return Verdict(check.keyword, check, event, rows)

    old = base[you.spec]
    reasons: list[str] = []
    if old.position is not None and cur.position is not None:
        delta = old.position - cur.position
        if abs(delta) >= alerts.min_change:
            reasons.append(UP if delta > 0 else DOWN)
        if alerts.top and old.position > alerts.top >= cur.position:
            reasons.append(ENTERED_TOP)
        elif alerts.top and cur.position > alerts.top >= old.position:
            reasons.append(LEFT_TOP)
        if alerts.url_change and old.url and cur.url and page_key(old.url) != page_key(cur.url):
            reasons.append(NEW_URL)
    elif old.position is None and cur.position is not None:
        reasons.append(RANKED)
        if alerts.top and cur.position <= alerts.top:
            reasons.append(ENTERED_TOP)
    elif old.position is not None and cur.position is None:
        reasons.append(UNRANKED)
    if alerts.ai_overview and check.ai_overview and cur.ai_cited != old.ai_cited:
        if cur.ai_cited:
            reasons.append(AI_CITED)
        elif old.ai_cited:
            reasons.append(AI_LOST)

    overtakes: list[Overtake] = []
    if alerts.competitors and cur.position is not None:
        for comp in config.competitors:
            prev = base.get(comp.spec)
            now = above(comp)
            if prev is not None and prev.above_you is not None and now != prev.above_you:
                overtakes.append(Overtake(comp.spec, ranks[comp.spec].position, bool(now)))

    has_event = bool(reasons or overtakes)
    rows = {
        you.spec: BaselineRow(
            position=cur.position if has_event else old.position,
            url=cur.url if has_event or not alerts.url_change else old.url,
            ai_cited=cur.ai_cited if check.ai_overview else old.ai_cited,
            above_you=None,
            day=day if has_event else old.day,
        )
    }
    for comp in config.competitors:
        prev = base.get(comp.spec)
        now = above(comp)
        rows[comp.spec] = BaselineRow(
            position=ranks[comp.spec].position,
            url=None,
            ai_cited=None,
            above_you=now if now is not None else (prev.above_you if prev else None),
            day=day,
        )
    event = None
    if has_event:
        event = Change(
            keyword=check.keyword,
            label=label,
            day=check.day,
            pages=config.pages,
            old=old.position,
            new=cur.position,
            old_url=old.url,
            new_url=cur.url,
            reasons=reasons,
            overtakes=overtakes,
            top=alerts.top,
        )
    return Verdict(check.keyword, check, event, rows)


def evaluate_all(config: Config, store: Store) -> list[Verdict]:
    verdicts = []
    for keyword in config.keywords:
        check = store.latest_check(keyword)
        if check is not None:
            verdicts.append(evaluate(config, check, store.baseline(keyword)))
    return verdicts


# -- weekly digest ------------------------------------------------------------------------


def iso_week(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"


@dataclass
class DigestRow:
    keyword: Keyword
    label: str
    position: int | None
    url: str | None
    previous: int | None
    compared: bool  # False: no earlier check to compare with
    ai_cited: bool | None
    competitors: dict[str, int | None]

    @property
    def delta(self) -> int | None:
        if not self.compared or self.position is None or self.previous is None:
            return None
        return self.previous - self.position


@dataclass
class NewInTop:
    host: str
    label: str
    position: int
    competitor: bool


@dataclass
class CompetitorSummary:
    site: str
    above_you: int
    ranked: int
    best: int | None
    best_label: str | None


@dataclass
class WeeklyDigest:
    site: str
    country: str
    pages: int
    day: date
    since: date | None
    rows: list[DigestRow]
    new_in_top: list[NewInTop]
    new_in_top_n: int
    competitors: list[CompetitorSummary]

    kind = "digest"

    def count(self, top: int, previous: bool = False) -> int:
        if previous:
            return sum(
                1 for r in self.rows if r.compared and r.previous is not None and r.previous <= top
            )
        return sum(1 for r in self.rows if r.position is not None and r.position <= top)

    @property
    def compared(self) -> bool:
        return any(r.compared for r in self.rows)

    @property
    def ranked(self) -> int:
        return sum(1 for r in self.rows if r.position is not None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "site": self.site,
            "day": self.day.isoformat(),
            "since": self.since.isoformat() if self.since else None,
            "keywords": [
                {
                    "keyword": r.keyword.text,
                    "country": r.keyword.country,
                    "position": r.position,
                    "previous": r.previous if r.compared else None,
                    "change": r.delta,
                    "url": r.url,
                    "ai_cited": r.ai_cited,
                    "competitors": dict(r.competitors),
                }
                for r in self.rows
            ],
            "new_in_top": [asdict(n) for n in self.new_in_top],
            "competitors": [asdict(c) for c in self.competitors],
        }


def build_digest(config: Config, store: Store, today: date, days: int = 7) -> WeeklyDigest | None:
    """Positions now against about ``days`` earlier. None when nothing was checked yet."""
    rows: list[DigestRow] = []
    new_in_top: list[NewInTop] = []
    compared_days: list[date] = []
    latest_day: date | None = None
    top_n = config.digest.new_in_top
    for keyword in config.keywords:
        history = [
            c for c in store.checks(keyword, today - timedelta(days=days * 2)) if c.day <= today
        ]
        if not history:
            continue
        latest = history[-1]
        latest_day = max(latest_day or latest.day, latest.day)
        target = latest.day - timedelta(days=days)
        earlier = [c for c in history[:-1] if c.day <= target]
        if earlier:
            base = earlier[-1]
        else:
            later = [c for c in history[:-1] if c.day > target]
            base = later[0] if later else None
        ranks = ranks_of(latest, config.sites)
        you = ranks[config.site.spec]
        prev = ranks_of(base, [config.site])[config.site.spec] if base else None
        if base is not None:
            compared_days.append(base.day)
        rows.append(
            DigestRow(
                keyword=keyword,
                label=config.label(keyword),
                position=you.position,
                url=you.url,
                previous=prev.position if prev else None,
                compared=base is not None,
                ai_cited=you.ai_cited,
                competitors={c.spec: ranks[c.spec].position for c in config.competitors},
            )
        )
        window = [c for c in history[:-1] if c.day >= latest.day - timedelta(days=days)]
        if top_n and window:
            seen = {r.host for c in window for r in c.results if r.position <= top_n}
            listed: set[str] = set()
            for r in latest.results:
                if r.position > top_n or r.host in seen or r.host in listed:
                    continue
                if config.site.matches(r.host, r.path):
                    continue
                listed.add(r.host)
                new_in_top.append(
                    NewInTop(
                        host=r.host,
                        label=config.label(keyword),
                        position=r.position,
                        competitor=any(c.matches(r.host, r.path) for c in config.competitors),
                    )
                )
    if not rows or latest_day is None:
        return None

    summaries = []
    for comp in config.competitors:
        positions = [(r.competitors[comp.spec], r) for r in rows if r.competitors[comp.spec]]
        above = sum(
            1
            for pos, r in positions
            if pos is not None and (r.position is None or pos < r.position)
        )
        best = min(positions, key=lambda p: p[0] or 0) if positions else None
        summaries.append(
            CompetitorSummary(
                site=comp.spec,
                above_you=above,
                ranked=len(positions),
                best=best[0] if best else None,
                best_label=best[1].label if best else None,
            )
        )
    since = Counter(compared_days).most_common(1)[0][0] if compared_days else None
    new_in_top.sort(key=lambda n: (n.position, n.host))
    return WeeklyDigest(
        site=config.site.spec,
        country=config.country,
        pages=config.pages,
        day=latest_day,
        since=since,
        rows=rows,
        new_in_top=new_in_top,
        new_in_top_n=top_n,
        competitors=summaries,
    )


def digest_due(config: Config, store: Store, today: date) -> bool:
    """The first run on or after ``digest.weekday`` each ISO week sends the digest."""
    weekday = config.digest.weekday
    if weekday is None or today.isoweekday() < weekday:
        return False
    return not store.digest_sent(iso_week(today))


def has_history_before(config: Config, store: Store, today: date) -> bool:
    """True when some keyword was checked before today (so a digest has something to say)."""
    for keyword in config.keywords:
        first = store.first_day(keyword)
        if first is not None and first < today:
            return True
    return False
