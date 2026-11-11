import io
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from conftest import FakeClient, FakeHttp, ranking, serp_json

from google_rank_tracker import cli

# Wednesday 11 Nov 2026, 06:00 UTC
T0 = datetime(2026, 11, 11, 6, 0, tzinfo=UTC)
ENV = {
    "SCRAPEUNBLOCKER_KEY": "test-key",
    "SLACK_WEBHOOK_URL": "https://slack.test/hook",
    "ALERT_WEBHOOK_URL": "https://webhook.test/in",
}


class Harness:
    def __init__(self, path, client, http=None, env=None):
        self.path = path
        self.client = client
        self.http = http or FakeHttp()
        self.env = dict(ENV if env is None else env)

    def __call__(self, *args, now=T0):
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(
            [*args, "-c", str(self.path), "--env-file", str(self.path.parent / "none.env")],
            environ=dict(self.env),
            client_factory=lambda env: self.client,
            http_factory=lambda: self.http,
            now=now,
            out=out,
            err=err,
        )
        self.out, self.err = out.getvalue(), err.getvalue()
        return code


def client_for(you, rival=None, other=None, ai_refs=None):
    return FakeClient(default=serp_json(ranking(you, rival=rival, other=other), ai_refs=ai_refs))


def test_parser():
    parser = cli.build_parser()
    args = parser.parse_args(["run", "--dry-run", "--force", "--keyword", "a", "--keyword", "b"])
    assert (args.command, args.dry_run, args.force, args.keyword) == ("run", True, True, ["a", "b"])
    assert parser.parse_args(["run"]).dry_run is False
    args = parser.parse_args(["preview", "running shoes", "--country", "gb", "--top", "5"])
    assert (args.keyword, args.country, args.top) == ("running shoes", "gb", 5)
    assert parser.parse_args(["report"]).out == "site"
    assert parser.parse_args(["digest", "--dry-run"]).dry_run is True
    with pytest.raises(SystemExit):
        parser.parse_args([])


def test_first_run_then_nothing_new_then_a_change(write_config):
    run = Harness(write_config(), client_for(4, rival=2))
    assert run("run") == 0
    assert len(run.client.calls) == 2  # two keywords, one call each
    assert "up to 4 API requests" in run.out
    slack = run.http.texts("slack")
    assert len(slack) == 1 and slack[0].startswith(
        "*Now tracking 2 Google keywords for example.com*"
    )
    assert "rival.com #2" in slack[0]
    [(_, webhook)] = [p for p in run.http.posts if "webhook" in p[0]]
    assert webhook["kind"] == "tracking" and webhook["keywords"][0]["position"] == 4

    # Same day: no API call, nothing delivered.
    run.http.posts.clear()
    assert run("run", now=T0 + timedelta(hours=3)) == 0
    assert len(run.client.calls) == 2
    assert run.http.posts == []
    assert "0 API requests" in run.out and "Nothing new to deliver." in run.out

    # Next day the site dropped from #4 to #12: one alert (confirmed by a second search).
    run.client.default = serp_json(ranking(12, rival=2))
    assert run("run", now=T0 + timedelta(days=1)) == 0
    assert len(run.client.calls) == 6  # 2 checks + 2 re-checks
    [text] = run.http.texts("slack")
    assert "#4 \N{RIGHTWARDS ARROW} #12" in text and "out of the top 10" in text
    [(_, webhook)] = [p for p in run.http.posts if "webhook" in p[0]]
    assert webhook["kind"] == "changes"
    assert webhook["changes"][0]["reasons"] == ["down", "left_top"]

    # And again the same day: already delivered.
    run.http.posts.clear()
    assert run("run", now=T0 + timedelta(days=1, hours=2)) == 0
    assert run.http.posts == []


def test_recheck_drops_a_change_google_does_not_repeat(write_config):
    run = Harness(write_config(), client_for(4))
    run("run")
    run.http.posts.clear()
    run.client.answers = {
        "running shoes": [serp_json(ranking(15)), serp_json(ranking(4))],
        "trail shoes": [serp_json(ranking(4))],
    }
    assert run("run", now=T0 + timedelta(days=1)) == 0
    assert [c["keyword"] for c in run.client.calls[2:]] == [
        "running shoes",
        "trail shoes",
        "running shoes",
    ]
    assert "Searching 1 changed keyword(s) again" in run.out
    assert run.http.posts == []


def test_recheck_off(write_config):
    run = Harness(write_config(extra="\n"), client_for(4))
    path = run.path
    path.write_text(path.read_text().replace("  top: 10\n", "  top: 10\n  recheck: false\n"))
    run("run")
    run.client.default = serp_json(ranking(15))
    run("run", now=T0 + timedelta(days=1))
    assert len(run.client.calls) == 4
    assert len(run.http.texts("slack")) == 2


def test_dry_run_sends_and_saves_nothing(write_config, tmp_path):
    run = Harness(write_config(), client_for(4))
    assert run("run", "--dry-run") == 0
    assert run.http.posts == []
    assert not (tmp_path / "state" / "ranks.db").exists()
    assert "Dry run: would deliver 1 message(s) (tracking)." in run.out
    assert "--- slack ---" in run.out and "*Now tracking 2 Google keywords" in run.out

    run("run")
    db = (tmp_path / "state" / "ranks.db").read_bytes()
    run.http.posts.clear()
    run.client.default = serp_json(ranking(9))
    assert run("run", "--dry-run", now=T0 + timedelta(days=1)) == 0
    assert run.http.posts == []
    assert "#4 \N{RIGHTWARDS ARROW} #9" in run.out
    assert (tmp_path / "state" / "ranks.db").read_bytes() == db


def test_failed_delivery_is_retried_next_run_without_new_api_calls(write_config):
    http = FakeHttp(fail={"slack": 500, "webhook": 500})
    run = Harness(write_config(), client_for(4), http=http)
    assert run("run") == 1
    assert "Delivery failed: slack: HTTP 500" in run.err
    assert "retried next run" in run.out

    http.fail = {}
    http.posts.clear()
    assert run("run", now=T0 + timedelta(hours=1)) == 0
    assert len(run.client.calls) == 2  # no new search, same day
    assert http.texts("slack")[0].startswith("*Now tracking")


def test_one_destination_failing_still_counts_as_delivered(write_config):
    http = FakeHttp(fail={"webhook": 500})
    run = Harness(write_config(), client_for(4), http=http)
    assert run("run") == 1  # a destination failed: the workflow shows it
    http.posts.clear()
    http.fail = {}
    run("run", now=T0 + timedelta(hours=1))
    assert http.posts == []  # Slack got it, so it is not sent twice


def test_no_destination_keeps_messages_waiting(write_config):
    run = Harness(write_config(), client_for(4), env={"SCRAPEUNBLOCKER_KEY": "k"})
    assert run("run") == 0
    assert "are waiting: no destination secret is set" in run.out
    run.env["DISCORD_WEBHOOK_URL"] = "https://discord.test/x"
    run("run", now=T0 + timedelta(hours=1))
    [text] = run.http.texts("discord")
    assert text.startswith("**Now tracking 2 Google keywords")


def test_weekly_digest(write_config):
    run = Harness(write_config(), client_for(6, rival=3))
    run("run")  # Wednesday: first week, the "now tracking" message stands in for a digest
    run.http.posts.clear()
    run("run", now=T0 + timedelta(days=1))  # Thursday, nothing changed
    assert run.http.posts == []

    run.client.default = serp_json(ranking(5, rival=3, other=8))
    monday = T0 + timedelta(days=5)
    assert run("run", now=monday) == 0
    slack = run.http.texts("slack")
    assert len(slack) == 1 and slack[0].startswith("*Weekly Google rankings for example.com*")
    assert "Change since Wed 11 Nov 2026." in slack[0]
    assert "other.org (competitor): #8 for running shoes" in slack[0]
    [(_, webhook)] = [p for p in run.http.posts if "webhook" in p[0]]
    assert webhook["kind"] == "digest" and webhook["keywords"][0]["change"] == 1

    run.http.posts.clear()
    run("run", now=monday + timedelta(hours=2))
    assert run.http.posts == []


def test_digest_command(write_config):
    run = Harness(write_config(), client_for(6))
    assert run("digest") == 1
    assert "Nothing checked yet" in run.err
    run("run")
    run.http.posts.clear()
    assert run("digest", "--dry-run") == 0
    assert run.http.posts == [] and "Weekly Google rankings" in run.out
    assert run("digest") == 0
    assert len(run.http.texts("slack")) == 1


def test_keyword_filter_and_unknown_keyword(write_config):
    run = Harness(write_config(), client_for(6))
    assert run("run", "--keyword", "Trail Shoes") == 0
    assert [c["keyword"] for c in run.client.calls] == ["trail shoes"]
    assert run("run", "--keyword", "nope") == 2
    assert "not in the keywords list" in run.err


def test_force_checks_again(write_config):
    run = Harness(write_config(), client_for(6))
    run("run")
    run("run", "--force", now=T0 + timedelta(hours=1))
    assert len(run.client.calls) == 4
    with sqlite3.connect(run.path.parent / "state" / "ranks.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM checks").fetchone()[0] == 2
    conn.close()


def test_missing_api_key(write_config):
    run = Harness(write_config(), client_for(6), env={})
    assert run("run") == 2
    assert "set SCRAPEUNBLOCKER_KEY" in run.err


def test_bad_config_exit_code(write_config):
    run = Harness(write_config("site: example.com\nkeywords: []\n"), client_for(6))
    assert run("run") == 2
    assert "keywords" in run.err


def test_preview(write_config):
    client = FakeClient(default=serp_json(ranking(3, rival=1), ai_refs=["example.com"]))
    run = Harness(write_config(), client)
    assert run("preview", "running shoes", "--top", "5") == 0
    assert "  1  rival.com/page  <- rival.com" in run.out
    assert "  3  example.com/page  <- example.com (you)" in run.out
    assert "cited in the AI Overview" in run.out
    assert not (run.path.parent / "state" / "ranks.db").exists()


def test_status_and_report(write_config, tmp_path):
    run = Harness(write_config(), client_for(6, rival=3))
    assert run("status") == 0 and "No history yet" in run.out
    run("run")
    assert run("status") == 0
    assert "running shoes" in run.out and "#6" in run.out and "#3" in run.out
    assert run("report", "--out", str(tmp_path / "site")) == 0
    page = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    assert "<title>Google rankings: example.com</title>" in page
    assert (tmp_path / "site" / ".nojekyll").exists()


def test_test_destinations(write_config):
    run = Harness(write_config(), client_for(6))
    assert run("test-destinations") == 0
    assert "slack    ok" in run.out and "discord  skipped (DISCORD_WEBHOOK_URL not set)" in run.out
    assert len(run.http.posts) == 2
    run.env = {}
    assert run("test-destinations") == 2


def test_github_actions_annotations_and_summary(write_config, tmp_path):
    summary = tmp_path / "summary.md"
    env = {**ENV, "GITHUB_ACTIONS": "true", "GITHUB_STEP_SUMMARY": str(summary)}
    run = Harness(write_config(), client_for(4), env=env)
    run("run")
    run.client.default = serp_json(ranking(12))
    run("run", now=T0 + timedelta(days=1))
    assert "::notice title=running shoes::" in run.out
    text = summary.read_text(encoding="utf-8")
    assert "| running shoes | #12 |" in text
    assert "### Changes delivered" in text


def test_report_url_is_linked(write_config):
    run = Harness(
        write_config(), client_for(4), env={**ENV, "RANK_REPORT_URL": "https://me.github.io/r/"}
    )
    run("run")
    assert run.http.texts("slack")[0].endswith("Chart: <https://me.github.io/r/|me.github.io/r>")
