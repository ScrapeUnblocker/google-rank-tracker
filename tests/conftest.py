"""Shared fakes: a serpApi client, an HTTP client for the sinks, and config files.

No test calls the real API or posts anywhere.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pytest

from google_rank_tracker.config import Config, load_config

CONFIG = """
site: example.com
competitors: [rival.com, other.org]
keywords:
  - running shoes
  - trail shoes
country: us
pages: 2
full_urls: true
alerts:
  min_change: 3
  top: 10
digest:
  weekday: monday
delivery:
  destinations: [slack, discord, webhook]
database: state/ranks.db
"""


def serp_json(
    hosts: Sequence[str],
    *,
    ai_refs: Sequence[str] | None = None,
    ai_sources: Sequence[str] = (),
    pages: int = 2,
    paths: dict[str, str] | None = None,
) -> dict[str, Any]:
    """A serpApi-shaped response with one organic result per host, in order."""
    paths = paths or {}
    organic = [
        {
            "position": i,
            "title": f"Result {i} on {host}",
            "url": f"https://www.{host}{paths.get(host, '/page')}",
            "domain": host,
        }
        for i, host in enumerate(hosts, start=1)
    ]
    ai = None
    if ai_refs is not None:
        ai = {
            "text": "summary",
            "sources": list(ai_sources),
            "references": [{"url": f"https://{h}/ref", "domain": h} for h in ai_refs],
        }
    return {"organic": organic, "aiOverview": ai, "pagesFetched": pages, "keyword": "x"}


def ranking(you: int | None, *, rival: int | None = None, other: int | None = None) -> list[str]:
    """Twenty hosts with example.com / rival.com / other.org at the given positions."""
    hosts = [f"filler{i}.net" for i in range(1, 21)]
    for host, position in (("example.com", you), ("rival.com", rival), ("other.org", other)):
        if position is not None:
            hosts[position - 1] = host
    return hosts


class FakeClient:
    """Stands in for ``scrapeunblocker.Client``. ``answers`` maps keyword -> list of
    responses (dicts or exceptions), consumed in order; the last one repeats."""

    def __init__(self, answers: dict[str, list[Any]] | None = None, default: Any = None):
        self.answers = answers or {}
        self.default = default
        self.calls: list[dict[str, Any]] = []

    def serp(self, keyword: str, **kwargs: Any) -> Any:
        self.calls.append({"keyword": keyword, **kwargs})
        queue = self.answers.get(keyword)
        answer = (queue.pop(0) if len(queue) > 1 else queue[0]) if queue else self.default
        if isinstance(answer, BaseException):
            raise answer
        return answer


class FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "ok", headers: Any = None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


class FakeHttp:
    """Records every POST. ``fail`` maps a URL substring to a status code to answer with."""

    def __init__(self, fail: dict[str, int] | None = None):
        self.posts: list[tuple[str, Any]] = []
        self.fail = fail or {}

    def post(self, url: str, *, json: Any, headers: Any = None, timeout: float) -> FakeResponse:
        self.posts.append((url, json))
        for part, status in self.fail.items():
            if part in url:
                return FakeResponse(status, "boom")
        return FakeResponse()

    def close(self) -> None:
        pass

    def texts(self, part: str = "") -> list[str]:
        out = []
        for url, body in self.posts:
            if part in url:
                out.append(body.get("text") or body.get("content") or "")
        return out


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(time, "sleep", lambda _s: None)


@pytest.fixture
def write_config(tmp_path: Path) -> Callable[..., Path]:
    def write(text: str = CONFIG, extra: str = "") -> Path:
        path = tmp_path / "config.yml"
        path.write_text(text + extra, encoding="utf-8")
        return path

    return write


@pytest.fixture
def config(write_config: Callable[..., Path]) -> Config:
    return load_config(write_config())
