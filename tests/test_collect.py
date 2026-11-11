import pytest
from conftest import FakeClient, serp_json
from scrapeunblocker import AuthenticationError, BlockedError, ServerError

from google_rank_tracker.collect import FetchError, collect, fetch_serp, host_of, parse_serp
from google_rank_tracker.config import Keyword

KW = Keyword("running shoes", "us")


def test_parse_serp_reads_results_and_ai_overview():
    data = serp_json(
        ["example.com", "rival.com", "docs.example.com"],
        ai_refs=["rival.com", "docs.example.com"],
        ai_sources=["rival.com", "blog.example.com"],
        pages=2,
    )
    serp = parse_serp(KW, data)
    assert [(r.position, r.host) for r in serp.results] == [
        (1, "example.com"),
        (2, "rival.com"),
        (3, "docs.example.com"),
    ]
    assert serp.results[0].url == "https://www.example.com/page"
    assert serp.results[0].title == "Result 1 on example.com"
    assert serp.pages == 2 and serp.depth == 3
    assert serp.ai_overview is True
    # references first, then sources not already cited, no duplicates
    assert [(c.host, c.url) for c in serp.citations] == [
        ("rival.com", "https://rival.com/ref"),
        ("docs.example.com", "https://docs.example.com/ref"),
        ("blog.example.com", ""),
    ]


def test_parse_serp_skips_junk_and_falls_back_to_domain():
    data = {
        "organic": [
            {"position": 1, "url": None, "domain": "nothing.com"},
            {"position": 2, "url": "https://www.google.com/url?q=x", "domain": "Shop.example.com"},
            "not a dict",
            {"position": "?", "url": "https://rival.com/a", "domain": "rival.com"},
        ],
        "aiOverview": None,
    }
    serp = parse_serp(KW, data)
    assert [(r.position, r.host) for r in serp.results] == [
        (2, "shop.example.com"),
        (4, "rival.com"),
    ]
    assert serp.ai_overview is False and serp.citations == []
    assert serp.pages == 1


def test_parse_serp_without_results_is_an_error():
    with pytest.raises(FetchError, match="no organic results"):
        parse_serp(KW, {"organic": []})
    with pytest.raises(FetchError):
        parse_serp(KW, ["not", "a", "dict"])


def test_host_of():
    assert host_of("https://WWW.Example.com/x") == "example.com"
    assert host_of("https://www.google.com/url?q=1", "site.org") == "site.org"
    assert host_of("", None) == ""


def test_fetch_serp_passes_options_and_retries_a_captcha():
    client = FakeClient(
        {"running shoes": [BlockedError("captcha", status_code=403), serp_json(["example.com"])]}
    )
    slept = []
    serp = fetch_serp(client, KW, pages=3, full_urls=True, sleep=slept.append)
    assert serp.results[0].host == "example.com"
    assert slept == [10.0]
    assert client.calls[0] == {
        "keyword": "running shoes",
        "proxy_country": "us",
        "pages_to_check": 3,
        "full_urls": True,
    }


def test_fetch_serp_gives_up_after_three_attempts():
    client = FakeClient({"running shoes": [ServerError("down", status_code=502)]})
    with pytest.raises(FetchError, match="ServerError"):
        fetch_serp(client, KW, pages=1, full_urls=False, sleep=lambda s: None)
    assert len(client.calls) == 3


def test_fetch_serp_lets_auth_errors_end_the_run():
    client = FakeClient({"running shoes": [AuthenticationError("bad key", status_code=401)]})
    with pytest.raises(AuthenticationError):
        fetch_serp(client, KW, pages=1, full_urls=False)
    assert len(client.calls) == 1


def test_no_country_lets_the_api_pick():
    client = FakeClient(default=serp_json(["example.com"]))
    fetch_serp(client, Keyword("x", ""), pages=1, full_urls=False)
    assert client.calls[0]["proxy_country"] is None


def test_collect_records_failures_and_goes_on():
    client = FakeClient(
        {"a": [serp_json(["example.com"], pages=2)], "b": [{"organic": []}]},
        default=serp_json(["rival.com"], pages=1),
    )
    seen = []
    result = collect(
        client,
        [Keyword("a", "us"), Keyword("b", "us"), Keyword("c", "us")],
        pages=2,
        full_urls=False,
        on_done=lambda k, s, e: seen.append((k.text, s is not None, e)),
    )
    assert [s.keyword.text for s in result.serps] == ["a", "c"]
    assert result.failures == {("b", "us"): "Google returned no organic results"}
    assert result.requests == 3
    assert seen == [
        ("a", True, None),
        ("b", False, "Google returned no organic results"),
        ("c", True, None),
    ]
