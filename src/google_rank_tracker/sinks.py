"""Destinations behind one small interface.

A sink has a ``name`` and three methods: ``send(message)`` delivers one :class:`Message`,
``preview(message)`` returns what ``send`` would post (for ``--dry-run``) and ``test()``
posts a short hello (for ``test-destinations``). ``send`` and ``test`` raise
:class:`DeliveryError` on failure. Secrets come from environment variables, never from
``config.yml``. To add a destination, write a class like the ones below and register it
in ``REGISTRY``.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from . import messages
from .config import Delivery
from .messages import Style

USER_AGENT = "google-rank-tracker (+https://github.com/ScrapeUnblocker/google-rank-tracker)"
CONNECTED = (
    "google-rank-tracker is connected. Google rank changes for your keywords will arrive here."
)


class DeliveryError(RuntimeError):
    """A destination did not accept the message."""


@dataclass
class Message:
    """One thing to deliver: rank changes, the "now tracking" list, or the weekly digest."""

    kind: str  # "changes", "tracking" or "digest"
    render: Callable[[Style, int], list[str]]  # the text in a style, split to a length limit
    data: dict[str, Any] = field(default_factory=dict)  # structured fields for webhooks


class Sink(Protocol):
    name: str

    def send(self, message: Message) -> None: ...

    def preview(self, message: Message) -> list[str]: ...

    def test(self) -> str: ...


class Http(Protocol):
    def post(self, url: str, *, json: Any, headers: Any = None, timeout: float) -> Any: ...


def post(
    http: Http,
    url: str,
    payload: Any,
    *,
    sink: str,
    sleep: Callable[[float], None] | None = None,
) -> Any:
    """POST once, retrying once on 429/502/503. Errors never include the URL, because
    webhook URLs are secrets."""
    for attempt in (1, 2):
        try:
            resp = http.post(url, json=payload, timeout=20.0)
        except httpx.HTTPError as exc:
            raise DeliveryError(f"{sink}: request failed ({type(exc).__name__})") from None
        if resp.status_code in (429, 502, 503) and attempt == 1:
            (sleep or time.sleep)(_retry_after(resp))
            continue
        if resp.status_code >= 300:
            body = (resp.text or "")[:200].replace("\n", " ")
            raise DeliveryError(f"{sink}: HTTP {resp.status_code} {body}".rstrip())
        return resp
    raise AssertionError("unreachable")


def _retry_after(resp: Any) -> float:
    try:
        return min(float(resp.headers.get("Retry-After", "2")), 10.0)
    except (TypeError, ValueError, AttributeError):
        return 2.0


class ChatSink:
    """A chat webhook that shows formatted text."""

    name = "chat"
    style: Style = messages.PLAIN
    limit = 3900

    def __init__(self, webhook_url: str, http: Http) -> None:
        self.webhook_url = webhook_url
        self.http = http

    def preview(self, message: Message) -> list[str]:
        return message.render(self.style, self.limit)

    def send(self, message: Message) -> None:
        for text in self.preview(message):
            post(self.http, self.webhook_url, self.payload(text), sink=self.name)

    def test(self) -> str:
        post(self.http, self.webhook_url, self.payload(CONNECTED), sink=self.name)
        return "test message sent"

    def payload(self, text: str) -> dict[str, Any]:
        raise NotImplementedError


class SlackSink(ChatSink):
    """Slack incoming webhook (mrkdwn). Link previews are off."""

    name = "slack"
    style = messages.SLACK
    limit = 3900

    def payload(self, text: str) -> dict[str, Any]:
        return {"text": text, "unfurl_links": False, "unfurl_media": False}


class DiscordSink(ChatSink):
    """Discord channel webhook. Link previews are suppressed, mentions disabled."""

    name = "discord"
    style = messages.DISCORD
    limit = 1900

    def payload(self, text: str) -> dict[str, Any]:
        return {"content": text, "allowed_mentions": {"parse": []}, "flags": 4}


class WebhookSink:
    """Any URL that accepts a JSON POST: Zapier, Make, n8n, your own endpoint.

    One POST per message: ``source``, ``kind``, the plain-text message as ``text`` and every
    field as JSON (``changes``, ``keywords`` or the digest's ``keywords`` and
    ``competitors``).
    """

    name = "webhook"

    def __init__(self, url: str, http: Http) -> None:
        self.webhook_url = url
        self.http = http

    def payload(self, message: Message) -> dict[str, Any]:
        text = "\n\n".join(message.render(messages.PLAIN, 100_000))
        return {"source": "google-rank-tracker", "kind": message.kind, "text": text, **message.data}

    def preview(self, message: Message) -> list[str]:
        return [self.payload(message)["text"]]

    def send(self, message: Message) -> None:
        post(self.http, self.webhook_url, self.payload(message), sink=self.name)

    def test(self) -> str:
        payload = {"source": "google-rank-tracker", "kind": "test", "text": CONNECTED}
        post(self.http, self.webhook_url, payload, sink=self.name)
        return "test message sent"


@dataclass(frozen=True)
class SinkSpec:
    env: str
    build: Callable[[str, Http], Sink]


REGISTRY: dict[str, SinkSpec] = {
    "slack": SinkSpec("SLACK_WEBHOOK_URL", SlackSink),
    "discord": SinkSpec("DISCORD_WEBHOOK_URL", DiscordSink),
    "webhook": SinkSpec("ALERT_WEBHOOK_URL", WebhookSink),
}


def build_sinks(
    delivery: Delivery, env: Mapping[str, str], http: Http
) -> tuple[list[Sink], list[str]]:
    """Build every listed destination whose secret is set, in config order.

    Returns ``(sinks, skipped)`` where ``skipped`` names destinations with no secret.
    """
    sinks: list[Sink] = []
    skipped: list[str] = []
    for name in delivery.destinations:
        spec = REGISTRY[name]
        value = (env.get(spec.env) or "").strip()
        if value:
            sinks.append(spec.build(value, http))
        else:
            skipped.append(name)
    return sinks, skipped


def new_http_client() -> httpx.Client:
    return httpx.Client(headers={"User-Agent": USER_AGENT}, follow_redirects=False)
