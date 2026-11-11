"""Read Google results through the ScrapeUnblocker serpApi.

One call per keyword: ``client.serp(keyword, proxy_country=..., pages_to_check=...,
full_urls=...)`` from the official Python SDK, which calls ``POST /serpApi``. Each result
page Google renders is one billed request, so a keyword with ``pages: 2`` costs 2.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit

from scrapeunblocker import (
    AuthenticationError,
    BlockedError,
    BrowserTimeoutError,
    PaymentRequiredError,
    RateLimitError,
    ScrapeTimeoutError,
    ScrapeUnblockerError,
    ServerError,
    UpstreamOutageError,
)
from scrapeunblocker import ConnectionError as SUConnectionError

from .config import Keyword

log = logging.getLogger(__name__)

# Errors that end the whole run: retrying or moving on to the next keyword cannot help.
FATAL = (AuthenticationError, PaymentRequiredError)
# Errors worth another try after a pause. BlockedError is Google's CAPTCHA; the API
# already retried with other connections, a pause and one more call often gets through.
RETRYABLE = (
    BlockedError,
    ServerError,
    UpstreamOutageError,
    RateLimitError,
    BrowserTimeoutError,
    ScrapeTimeoutError,
    SUConnectionError,
)
RETRY_DELAYS = (10.0, 30.0)


class SerpClient(Protocol):
    """The part of ``scrapeunblocker.Client`` this package uses."""

    def serp(
        self,
        keyword: str,
        *,
        proxy_country: str | None = None,
        pages_to_check: int = 1,
        full_urls: bool = False,
    ) -> Any: ...


class FetchError(RuntimeError):
    """A keyword could not be checked this run. The run goes on with the others."""


@dataclass(frozen=True)
class Result:
    """One organic result. ``position`` counts organic results only (no ads or boxes)."""

    position: int
    url: str
    host: str
    title: str = ""

    @property
    def path(self) -> str:
        return urlsplit(self.url).path or "/"


@dataclass(frozen=True)
class Citation:
    """A page (or, when only the host is given, a site) cited by Google's AI Overview."""

    host: str
    url: str = ""


@dataclass
class Serp:
    keyword: Keyword
    results: list[Result]
    pages: int  # result pages Google rendered (= billed requests)
    ai_overview: bool = False
    citations: list[Citation] = field(default_factory=list)

    @property
    def depth(self) -> int:
        """How many results were read; a site below this counts as not ranked."""
        return len(self.results)


@dataclass
class CollectResult:
    serps: list[Serp] = field(default_factory=list)
    failures: dict[tuple[str, str], str] = field(default_factory=dict)  # keyword key -> reason

    @property
    def requests(self) -> int:
        return sum(s.pages for s in self.serps)


def host_of(url: str | None, fallback: str | None = None) -> str:
    """The result's host without ``www.``. Google redirect links fall back to ``domain``."""
    host = (urlsplit(url or "").hostname or "").lower()
    if not host or (host.endswith("google.com") and "/url" in (url or "")):
        host = (fallback or "").lower()
    return host.removeprefix("www.")


def parse_serp(keyword: Keyword, data: Any) -> Serp:
    """Turn the serpApi JSON into a :class:`Serp`. Raises :class:`FetchError` on junk."""
    if not isinstance(data, dict):
        raise FetchError("the API answered with something other than a JSON object")
    results: list[Result] = []
    for i, item in enumerate(data.get("organic") or []):
        if not isinstance(item, dict) or not item.get("url"):
            continue
        try:
            position = int(item.get("position") or 0)
        except (TypeError, ValueError):
            position = 0
        position = position if position > 0 else i + 1
        host = host_of(item["url"], item.get("domain"))
        if not host:
            continue
        title = " ".join(str(item.get("title") or "").split())
        results.append(Result(position=position, url=str(item["url"]), host=host, title=title))
    if not results:
        raise FetchError("Google returned no organic results")
    results.sort(key=lambda r: r.position)

    citations: list[Citation] = []
    ai = data.get("aiOverview")
    if isinstance(ai, dict):
        seen: set[str] = set()
        for ref in ai.get("references") or []:
            if isinstance(ref, dict) and ref.get("url"):
                host = host_of(ref["url"], ref.get("domain"))
                if host:
                    citations.append(Citation(host=host, url=str(ref["url"])))
                    seen.add(host)
        for source in ai.get("sources") or []:
            host = host_of(f"https://{source}") if isinstance(source, str) else ""
            if host and host not in seen:
                citations.append(Citation(host=host))
                seen.add(host)
    try:
        pages = int(data.get("pagesFetched") or 1)
    except (TypeError, ValueError):
        pages = 1
    return Serp(
        keyword=keyword,
        results=results,
        pages=max(pages, 1),
        ai_overview=isinstance(ai, dict),
        citations=citations,
    )


def fetch_serp(
    client: SerpClient,
    keyword: Keyword,
    *,
    pages: int,
    full_urls: bool,
    sleep: Callable[[float], None] | None = None,
) -> Serp:
    """Search Google for one keyword, retrying transient errors."""
    sleep = sleep or time.sleep
    attempts = len(RETRY_DELAYS) + 1
    for attempt in range(1, attempts + 1):
        try:
            log.info("serpApi %r country=%s (attempt %d)", keyword.text, keyword.country, attempt)
            data = client.serp(
                keyword.text,
                proxy_country=keyword.country or None,
                pages_to_check=pages,
                full_urls=full_urls,
            )
        except FATAL:
            raise
        except RETRYABLE as exc:
            if attempt == attempts:
                raise FetchError(f"{type(exc).__name__}: {exc}") from None
            delay = RETRY_DELAYS[attempt - 1]
            log.info("%s for %r, retrying in %.0fs", type(exc).__name__, keyword.text, delay)
            sleep(delay)
            continue
        except ScrapeUnblockerError as exc:
            raise FetchError(f"{type(exc).__name__}: {exc}") from None
        return parse_serp(keyword, data)
    raise AssertionError("unreachable")


def collect(
    client: SerpClient,
    keywords: Sequence[Keyword],
    *,
    pages: int,
    full_urls: bool,
    sleep: Callable[[float], None] | None = None,
    on_done: Callable[[Keyword, Serp | None, str | None], None] | None = None,
) -> CollectResult:
    """Check every keyword once. A failed keyword is recorded and skipped."""
    result = CollectResult()
    for keyword in keywords:
        try:
            serp = fetch_serp(client, keyword, pages=pages, full_urls=full_urls, sleep=sleep)
        except FetchError as exc:
            log.warning("%r: %s", keyword.text, exc)
            result.failures[keyword.key] = str(exc)
            if on_done:
                on_done(keyword, None, str(exc))
            continue
        result.serps.append(serp)
        if on_done:
            on_done(keyword, serp, None)
    return result
