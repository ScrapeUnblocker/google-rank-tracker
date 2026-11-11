from datetime import date

from google_rank_tracker import messages
from google_rank_tracker.config import Keyword
from google_rank_tracker.messages import DISCORD, PLAIN, SLACK
from google_rank_tracker.process import (
    AI_LOST,
    DOWN,
    LEFT_TOP,
    NEW_URL,
    RANKED,
    UP,
    Change,
    CompetitorSummary,
    DigestRow,
    NewInTop,
    Overtake,
    Tracking,
    WeeklyDigest,
)

KW = Keyword("python dataclasses", "us")
DAY = date(2026, 11, 11)


def change(**kwargs):
    base = dict(
        keyword=KW,
        label="python dataclasses",
        day=DAY,
        pages=2,
        old=1,
        new=10,
        old_url="https://docs.python.org/3/library/dataclasses.html",
        new_url="https://docs.python.org/3.7/library/dataclasses.html",
        reasons=[DOWN, NEW_URL, AI_LOST],
        overtakes=[],
        top=10,
    )
    base.update(kwargs)
    return Change(**base)


def test_change_message_plain():
    [text] = messages.render_changes([change()], PLAIN, site="python.org", country="us")
    assert text == (
        "Google rank changes for python.org \N{MIDDLE DOT} US \N{MIDDLE DOT} Wed 11 Nov 2026\n"
        "\n"
        "\N{BLACK DOWN-POINTING TRIANGLE} python dataclasses: #1 \N{RIGHTWARDS ARROW} #10 "
        "(\N{MINUS SIGN}9) \N{MIDDLE DOT} no longer cited in Google's AI Overview\n"
        "    new page https://docs.python.org/3.7/library/dataclasses.html "
        "(was docs.python.org/3/library/dataclasses.html)"
    )


def test_change_message_slack_and_discord_links_and_report():
    c = change(reasons=[UP], old=8, new=3, old_url=None, new_url="https://example.com/a?b=1&c=2")
    [slack] = messages.render_changes(
        [c], SLACK, site="example.com", country="us", report_url="https://me.github.io/r/"
    )
    assert slack.startswith("*Google rank changes for example.com*")
    assert (
        "\N{BLACK UP-POINTING TRIANGLE} *python dataclasses*: #8 \N{RIGHTWARDS ARROW} #3 (+5)"
        in slack
    )
    assert "<https://example.com/a?b=1&c=2|example.com/a?b=1&amp;c=2>" in slack
    assert slack.endswith("Chart: <https://me.github.io/r/|me.github.io/r>")
    [discord] = messages.render_changes([c], DISCORD, site="example.com", country="us")
    assert "**python dataclasses**" in discord
    assert "[example.com/a?b=1&c=2](<https://example.com/a?b=1&c=2>)" in discord


def test_change_lines_ranked_and_overtakes():
    c = change(
        reasons=[RANKED],
        old=None,
        new=7,
        old_url=None,
        new_url="https://python.org/x",
        overtakes=[Overtake("rival.com", 2, True), Overtake("other.org", None, False)],
    )
    head = messages.change_lines(c, PLAIN)[0]
    assert "now #7 (was not on the first 2 pages)" in head
    assert "rival.com moved above you (#2)" in head
    assert "you moved above other.org (not on the first 2 pages)" in head
    gone = change(reasons=[DOWN, LEFT_TOP], old=4, new=12, new_url="https://python.org/x")
    assert "out of the top 10" in messages.change_lines(gone, PLAIN)[0]


def test_user_text_is_escaped():
    c = change(label="a <b> & *c*", reasons=[UP], old=9, new=2)
    slack = messages.change_lines(c, SLACK)[0]
    assert "*a &lt;b&gt; &amp; *c**" in slack
    discord = messages.change_lines(c, DISCORD)[0]
    assert "**a <b\\> & \\*c\\***" in discord


def test_tracking_message_lists_positions_and_competitors():
    items = [
        Tracking(
            KW,
            "python dataclasses",
            DAY,
            2,
            1,
            "https://docs.python.org/3/x",
            True,
            {"realpython.com": 4, "w3schools.com": 8, "gone.com": None},
        ),
        Tracking(Keyword("b", "us"), "b", DAY, 2, None, None, None, {}),
    ]
    [text] = messages.render_tracking(
        items,
        PLAIN,
        site="python.org",
        country="us",
        competitors=["realpython.com"],
        digest_day="Monday",
    )
    lines = text.splitlines()
    assert lines[0].startswith("Now tracking 2 Google keywords for python.org")
    assert lines[2] == (
        "\N{BULLET} python dataclasses: #1 https://docs.python.org/3/x \N{MIDDLE DOT} "
        "cited in Google's AI Overview"
    )
    assert lines[3] == "    realpython.com #4, w3schools.com #8"
    assert lines[4] == "\N{BULLET} b: not on the first 2 pages"
    assert lines[-1].endswith("The weekly digest comes on Mondays.")


def digest(rows=None):
    rows = rows or [
        DigestRow(
            KW,
            "python dataclasses",
            1,
            "https://docs.python.org/3/x",
            3,
            True,
            True,
            {"rival.com": 4},
        ),
        DigestRow(Keyword("b", "us"), "b", None, None, 9, True, None, {"rival.com": 2}),
        DigestRow(
            Keyword("c", "us"),
            "c",
            5,
            "https://python.org/c",
            None,
            False,
            None,
            {"rival.com": None},
        ),
    ]
    return WeeklyDigest(
        site="python.org",
        country="us",
        pages=2,
        day=DAY,
        since=date(2026, 11, 4),
        rows=rows,
        new_in_top=[NewInTop("rival.com", "b", 2, True)],
        new_in_top_n=10,
        competitors=[CompetitorSummary("rival.com", 1, 2, 2, "b")],
    )


def test_digest_message():
    [text] = messages.render_digest(digest(), SLACK)
    lines = text.splitlines()
    assert lines[0] == (
        "*Weekly Google rankings for python.org* \N{MIDDLE DOT} US \N{MIDDLE DOT} Wed 11 Nov 2026"
    )
    assert lines[1] == (
        "Top 3: 1 (was 1) \N{MIDDLE DOT} Top 10: 2 (was 2) \N{MIDDLE DOT} "
        "On the first 2 pages: 2 of 3 keywords"
    )
    assert "Change since Wed 4 Nov 2026." in lines
    table = text.split("```")[1].strip("\n").splitlines()
    assert table[0].split() == ["Keyword", "Now", "Change", "Page"]
    assert table[1].split() == ["python", "dataclasses", "#1", "+2", "docs.python.org/3/x", "(AI)"]
    assert table[2].split() == ["c", "#5", "new", "python.org/c"]
    assert table[3].split() == ["b", "-", "out"]
    assert "\N{BULLET} rival.com: above you on 1 of 3, best #2 (b)" in text
    assert "\N{BULLET} rival.com (competitor): #2 for b" in text


def test_long_digest_is_split_with_fences_and_header_in_each_part():
    rows = [
        DigestRow(
            Keyword(f"keyword number {i}", "us"),
            f"keyword number {i}",
            i,
            f"https://python.org/{i}",
            i,
            True,
            None,
            {},
        )
        for i in range(1, 60)
    ]
    parts = messages.render_digest(digest(rows), DISCORD, limit=1900)
    assert len(parts) > 1
    for part in parts:
        assert len(part) <= 1900
        assert part.count("```") == 2
        assert "Keyword" in part.split("```")[1]
    assert parts[1].startswith("**Weekly Google rankings for python.org** \N{MIDDLE DOT} US")
    assert f"(2/{len(parts)})" in parts[1].splitlines()[0]
    body = "".join(p.split("```")[1] for p in parts)
    assert all(f"keyword number {i} " in body for i in range(1, 60))


def test_plain_digest_has_no_fence():
    [text] = messages.render_digest(digest(), PLAIN)
    assert "```" not in text


def test_summary_markdown():
    md = messages.summary_markdown(digest(), [change()])
    assert "| Keyword | You | rival.com | Page |" in md
    assert "| python dataclasses | #1 | #4 | docs.python.org/3/x (AI Overview) |" in md
    assert "### Changes delivered" in md
    assert messages.summary_markdown(None, []).endswith("Nothing checked yet.\n")


def test_display_url():
    assert messages.display_url("https://www.example.com/a/b/") == "example.com/a/b"
    long = "https://example.com/" + "x" * 100
    assert len(messages.display_url(long, 40)) == 40
    assert messages.display_url(None) == ""
