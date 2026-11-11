from datetime import UTC, date, datetime, timedelta

from conftest import ranking, serp_json

from google_rank_tracker.collect import parse_serp
from google_rank_tracker.config import Keyword
from google_rank_tracker.report import Series, anchor, build_report, chart_svg, y_ticks
from google_rank_tracker.state import Store

NOW = datetime(2026, 11, 11, 6, 30, tzinfo=UTC)


def test_y_ticks():
    assert y_ticks(10) == [1, 5, 10]
    assert y_ticks(30) == [1, 10, 20, 30]
    assert y_ticks(100) == [1, 20, 40, 60, 80, 100]


def test_chart_breaks_the_line_where_a_site_was_not_ranked():
    days = [date(2026, 11, d) for d in (1, 2, 3, 4, 5)]
    you = Series("example.com", 1, True, [3, 2, None, 4, 5])
    rival = Series("rival.com", 2, False, [None, None, 7, None, None])
    svg = chart_svg(days, [you, rival], "label")
    assert svg.count('<path class="line s1"') == 2
    assert '<path class="line s2"' not in svg  # a lone point is a dot
    assert svg.count('class="dot s2"') == 1
    assert '<text class="end"' in svg and ">#5</text>" in svg


def test_report_page(config, tmp_path):
    kw = Keyword("running shoes", "us")
    with Store.open(tmp_path / "r.db") as store:
        for i, you in enumerate((9, 7, 4)):
            day = NOW.date() - timedelta(days=8 - i * 4)
            serp = parse_serp(kw, serp_json(ranking(you, rival=2), ai_refs=["example.com"]))
            store.save_check(serp, day, day.isoformat())
        page = build_report(config, store, NOW)
    assert "<title>Google rankings: example.com</title>" in page
    assert "US \N{MIDDLE DOT} the first 2 pages \N{MIDDLE DOT} 2 keywords" in page
    assert f'id="{anchor("running shoes")}"' in page
    assert '<li><svg class="key"' in page and "example.com (you)" in page
    assert "AI Overview</span>" in page
    assert page.count("<article") == 1  # trail shoes has no checks yet
    assert "\N{BLACK UP-POINTING TRIANGLE} 5" in page  # 9 -> 4 since 3 Nov
    assert "utm_source=github" in page


def test_report_escapes_user_text(config, tmp_path):
    kw = Keyword("</script><b>x", "us")
    from dataclasses import replace

    cfg = replace(config, keywords=(kw,))
    with Store.open(tmp_path / "r.db") as store:
        store.save_check(parse_serp(kw, serp_json(ranking(3))), NOW.date(), "t")
        page = build_report(cfg, store, NOW)
    assert "</script><b>" not in page
    assert "&lt;/script&gt;&lt;b&gt;x" in page


def test_report_without_history(config, tmp_path):
    with Store.open(tmp_path / "r.db") as store:
        page = build_report(config, store, NOW)
    assert "Nothing checked yet" in page
