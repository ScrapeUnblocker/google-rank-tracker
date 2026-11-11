"""Load and check ``config.yml``, the one file a user edits.

Secrets never live here: the API key and webhook URLs come from environment variables.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml

DESTINATIONS = ("slack", "discord", "webhook")
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
MAX_PAGES = 10

_COUNTRY = re.compile(r"^[a-z]{2}$")
_HOST = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$")


class ConfigError(ValueError):
    """config.yml is missing, unreadable or has a value that cannot work."""


@dataclass(frozen=True)
class Site:
    """A site to find in the results: a host (subdomains count) and an optional path."""

    spec: str
    host: str
    path: str = ""

    @classmethod
    def parse(cls, raw: Any, where: str) -> Site:
        if not isinstance(raw, str) or not raw.strip():
            raise ConfigError(f"{where}: expected a domain such as example.com")
        text = raw.strip().lower()
        if "://" not in text:
            text = "https://" + text
        parts = urlsplit(text)
        host = (parts.hostname or "").removeprefix("www.")
        if not _HOST.match(host):
            raise ConfigError(f"{where}: {raw!r} is not a domain such as example.com")
        path = parts.path.rstrip("/")
        spec = host + path
        return cls(spec=spec, host=host, path=path)

    def matches(self, host: str, path: str = "") -> bool:
        """True when a result on ``host`` with URL ``path`` belongs to this site."""
        host = host.lower().removeprefix("www.")
        if host != self.host and not host.endswith("." + self.host):
            return False
        if not self.path:
            return True
        path = path or "/"
        return path == self.path or path.startswith(self.path + "/")


@dataclass(frozen=True)
class Keyword:
    text: str
    country: str  # "" lets the API pick the country

    @property
    def key(self) -> tuple[str, str]:
        return (self.text, self.country)


@dataclass(frozen=True)
class Alerts:
    min_change: int = 3
    top: int = 10
    competitors: bool = True
    ai_overview: bool = True
    url_change: bool = True
    recheck: bool = True


@dataclass(frozen=True)
class Digest:
    weekday: int | None = 1  # ISO weekday, 1 = Monday; None = no weekly digest
    new_in_top: int = 10


@dataclass(frozen=True)
class Delivery:
    destinations: tuple[str, ...] = DESTINATIONS
    baseline_message: bool = True


@dataclass(frozen=True)
class Report:
    days: int = 90
    title: str = ""


@dataclass(frozen=True)
class Config:
    site: Site
    keywords: tuple[Keyword, ...]
    competitors: tuple[Site, ...] = ()
    country: str = "us"
    pages: int = 2
    full_urls: bool = True
    alerts: Alerts = field(default_factory=Alerts)
    digest: Digest = field(default_factory=Digest)
    delivery: Delivery = field(default_factory=Delivery)
    report: Report = field(default_factory=Report)
    database: Path = Path("state/ranks.db")

    @property
    def sites(self) -> tuple[Site, ...]:
        """Your site first, then the competitors in config order."""
        return (self.site, *self.competitors)

    def label(self, keyword: Keyword) -> str:
        """How a keyword is shown: its text, plus the country when it is not the default."""
        if keyword.country != self.country:
            return f"{keyword.text} ({keyword.country.upper() or 'any country'})"
        return keyword.text

    def find_keywords(self, text: str) -> list[Keyword]:
        wanted = text.strip().casefold()
        return [k for k in self.keywords if k.text.casefold() == wanted]


def _bool(value: Any, where: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{where}: expected true or false, got {value!r}")
    return value


def _int(value: Any, where: str, low: int, high: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{where}: expected a whole number, got {value!r}")
    if value < low or (high is not None and value > high):
        span = f"{low}-{high}" if high is not None else f"{low} or more"
        raise ConfigError(f"{where}: must be {span}, got {value}")
    return value


def _country(value: Any, where: str) -> str:
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or not _COUNTRY.match(value.strip().lower()):
        raise ConfigError(f"{where}: expected a two-letter country code such as us, got {value!r}")
    return value.strip().lower()


def _mapping(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{key}: expected a section of settings")
    return value


def _unknown(section: dict[str, Any], allowed: set[str], where: str) -> None:
    extra = sorted(set(section) - allowed)
    if extra:
        raise ConfigError(f"{where}: unknown setting(s) {', '.join(extra)}")


def parse_config(raw: Any, base_dir: Path | None = None) -> Config:
    if not isinstance(raw, dict):
        raise ConfigError("config.yml must be a mapping of settings")
    _unknown(
        raw,
        {
            "site",
            "competitors",
            "keywords",
            "country",
            "pages",
            "full_urls",
            "alerts",
            "digest",
            "delivery",
            "report",
            "database",
        },
        "config.yml",
    )
    site = Site.parse(raw.get("site"), "site")
    competitors_raw = raw.get("competitors") or []
    if not isinstance(competitors_raw, list):
        raise ConfigError("competitors: expected a list of domains")
    competitors: list[Site] = []
    for i, item in enumerate(competitors_raw):
        comp = Site.parse(item, f"competitors[{i}]")
        if comp.spec == site.spec or comp in competitors:
            raise ConfigError(f"competitors[{i}]: {comp.spec} is listed twice")
        competitors.append(comp)
    if len(competitors) > 7:
        raise ConfigError("competitors: at most 7, so every line in the chart keeps its colour")

    country = _country(raw.get("country", "us"), "country")
    keywords_raw = raw.get("keywords")
    if not isinstance(keywords_raw, list) or not keywords_raw:
        raise ConfigError("keywords: expected a list of searches, for example - python tutorial")
    keywords: list[Keyword] = []
    for i, item in enumerate(keywords_raw):
        where = f"keywords[{i}]"
        if isinstance(item, dict):
            _unknown(item, {"keyword", "country"}, where)
            text, kw_country = item.get("keyword"), _country(item.get("country", country), where)
        else:
            text, kw_country = item, country
        if not isinstance(text, str) or not text.strip():
            raise ConfigError(f"{where}: expected the search text")
        keyword = Keyword(" ".join(text.split()), kw_country)
        if keyword in keywords:
            raise ConfigError(f"{where}: {keyword.text!r} is listed twice")
        keywords.append(keyword)

    alerts_raw = _mapping(raw, "alerts")
    _unknown(
        alerts_raw,
        {"min_change", "top", "competitors", "ai_overview", "url_change", "recheck"},
        "alerts",
    )
    alerts = Alerts(
        min_change=_int(alerts_raw.get("min_change", 3), "alerts.min_change", 1),
        top=_int(alerts_raw.get("top", 10), "alerts.top", 0, 100),
        competitors=_bool(alerts_raw.get("competitors", True), "alerts.competitors"),
        ai_overview=_bool(alerts_raw.get("ai_overview", True), "alerts.ai_overview"),
        url_change=_bool(alerts_raw.get("url_change", True), "alerts.url_change"),
        recheck=_bool(alerts_raw.get("recheck", True), "alerts.recheck"),
    )

    digest_raw = _mapping(raw, "digest")
    _unknown(digest_raw, {"weekday", "new_in_top"}, "digest")
    weekday_raw = digest_raw.get("weekday", "monday")
    if weekday_raw in (None, False, ""):
        weekday = None
    elif isinstance(weekday_raw, str) and weekday_raw.strip().lower() in WEEKDAYS:
        weekday = WEEKDAYS.index(weekday_raw.strip().lower()) + 1
    else:
        raise ConfigError(
            f"digest.weekday: expected a day such as monday, or null; got {weekday_raw!r}"
        )
    digest = Digest(
        weekday=weekday,
        new_in_top=_int(digest_raw.get("new_in_top", 10), "digest.new_in_top", 0, 100),
    )

    delivery_raw = _mapping(raw, "delivery")
    _unknown(delivery_raw, {"destinations", "baseline_message"}, "delivery")
    dests = delivery_raw.get("destinations", list(DESTINATIONS))
    if dests is None:
        dests = []
    if not isinstance(dests, list) or any(d not in DESTINATIONS for d in dests):
        raise ConfigError(f"delivery.destinations: expected a list of {', '.join(DESTINATIONS)}")
    delivery = Delivery(
        destinations=tuple(dict.fromkeys(dests)),
        baseline_message=_bool(
            delivery_raw.get("baseline_message", True), "delivery.baseline_message"
        ),
    )

    report_raw = _mapping(raw, "report")
    _unknown(report_raw, {"days", "title"}, "report")
    title = report_raw.get("title") or ""
    if not isinstance(title, str):
        raise ConfigError("report.title: expected text")
    report = Report(days=_int(report_raw.get("days", 90), "report.days", 7, 3650), title=title)

    database = raw.get("database") or "state/ranks.db"
    if not isinstance(database, str):
        raise ConfigError("database: expected a file path")
    db_path = Path(database)
    if base_dir is not None and not db_path.is_absolute():
        db_path = base_dir / db_path

    return Config(
        site=site,
        competitors=tuple(competitors),
        keywords=tuple(keywords),
        country=country,
        pages=_int(raw.get("pages", 2), "pages", 1, MAX_PAGES),
        full_urls=_bool(raw.get("full_urls", True), "full_urls"),
        alerts=alerts,
        digest=digest,
        delivery=delivery,
        report=report,
        database=db_path,
    )


def load_config(path: str | Path) -> Config:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(f"{path} not found (run from the repo folder or pass --config)") from None
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from None
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from None
    return parse_config(raw, base_dir=path.resolve().parent)
