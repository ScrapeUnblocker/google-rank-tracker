import httpx
import pytest
from conftest import FakeHttp, FakeResponse

from google_rank_tracker.config import Delivery
from google_rank_tracker.sinks import (
    DeliveryError,
    DiscordSink,
    Message,
    SlackSink,
    WebhookSink,
    build_sinks,
    post,
)

SECRET = "https://hooks.example.test/services/T000/B000/secret-token"


def message(kind="changes"):
    return Message(
        kind=kind,
        render=lambda style, limit: [f"{style.name} text"],
        data={"site": "example.com", "changes": [{"keyword": "x"}]},
    )


def test_slack_payload():
    http = FakeHttp()
    SlackSink(SECRET, http).send(message())
    assert http.posts == [
        (SECRET, {"text": "slack text", "unfurl_links": False, "unfurl_media": False})
    ]


def test_discord_payload_suppresses_embeds_and_mentions():
    http = FakeHttp()
    DiscordSink(SECRET, http).send(message())
    [(_, body)] = http.posts
    assert body == {"content": "discord text", "allowed_mentions": {"parse": []}, "flags": 4}


def test_webhook_payload_has_text_and_fields():
    http = FakeHttp()
    WebhookSink(SECRET, http).send(message("digest"))
    [(_, body)] = http.posts
    assert body == {
        "source": "google-rank-tracker",
        "kind": "digest",
        "text": "plain text",
        "site": "example.com",
        "changes": [{"keyword": "x"}],
    }


def test_every_part_of_a_long_message_is_posted():
    http = FakeHttp()
    long = Message("digest", lambda style, limit: ["part 1", "part 2"])
    SlackSink(SECRET, http).send(long)
    assert [b["text"] for _, b in http.posts] == ["part 1", "part 2"]


def test_errors_never_show_the_webhook_url():
    http = FakeHttp(fail={"hooks": 404})
    with pytest.raises(DeliveryError) as info:
        SlackSink(SECRET, http).send(message())
    assert "HTTP 404" in str(info.value)
    assert "secret-token" not in str(info.value)

    class Broken:
        def post(self, url, **kwargs):
            raise httpx.ConnectError(f"cannot reach {url}")

    with pytest.raises(DeliveryError) as info:
        WebhookSink(SECRET, Broken()).send(message())
    assert "secret-token" not in str(info.value)


def test_rate_limit_is_retried_once():
    answers = [FakeResponse(429, headers={"Retry-After": "1"}), FakeResponse(200)]

    class Http:
        calls = 0

        def post(self, url, **kwargs):
            Http.calls += 1
            return answers.pop(0)

    slept = []
    post(Http(), SECRET, {"a": 1}, sink="slack", sleep=slept.append)
    assert Http.calls == 2 and slept == [1.0]


def test_test_message():
    http = FakeHttp()
    assert SlackSink(SECRET, http).test() == "test message sent"
    assert "connected" in http.posts[0][1]["text"]


def test_build_sinks_skips_destinations_without_a_secret():
    delivery = Delivery(destinations=("slack", "discord", "webhook"))
    env = {"SLACK_WEBHOOK_URL": " https://s ", "DISCORD_WEBHOOK_URL": "", "ALERT_WEBHOOK_URL": "x"}
    sinks, skipped = build_sinks(delivery, env, FakeHttp())
    assert [s.name for s in sinks] == ["slack", "webhook"]
    assert sinks[0].webhook_url == "https://s"
    assert skipped == ["discord"]
