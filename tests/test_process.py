from dataclasses import replace
from datetime import date, timedelta

from conftest import ranking, serp_json

from google_rank_tracker.collect import parse_serp
from google_rank_tracker.config import Keyword, Site
from google_rank_tracker.process import (
    AI_CITED,
    AI_LOST,
    DOWN,
    ENTERED_TOP,
    LEFT_TOP,
    NEW_URL,
    RANKED,
    UNRANKED,
    UP,
    Change,
    Tracking,
    build_digest,
    digest_due,
    evaluate,
    has_history_before,
    iso_week,
    rank_in,
)
from google_rank_tracker.state import Check, Store

KW = Keyword("running shoes", "us")
D0 = date(2026, 11, 9)  # a Monday


def check(hosts, day=D0, keyword=KW, ai_refs=None, paths=None):
    serp = parse_serp(keyword, serp_json(hosts, ai_refs=ai_refs, paths=paths))
    return Check(
        id=1,
        keyword=keyword,
        day=day,
        checked_at=day.isoformat(),
        pages=serp.pages,
        depth=serp.depth,
        ai_overview=serp.ai_overview,
        results=tuple(serp.results),
        citations=tuple(serp.citations),
    )


def first(config, hosts, **kwargs):
    """Evaluate a first check and return its baseline rows (as if delivered)."""
    verdict = evaluate(config, check(hosts, **kwargs), {})
    return verdict.baseline


def test_rank_in_takes_the_best_position_and_counts_results():
    c = check(["a.net", "docs.example.com", "rival.com", "example.com"], ai_refs=["example.com"])
    rank = rank_in(Site.parse("example.com", "s"), c.results, c.citations, c.ai_overview)
    assert rank.position == 2 and rank.count == 2
    assert rank.url == "https://www.docs.example.com/page"
    assert rank.ai_cited is True
    none = rank_in(Site.parse("missing.com", "s"), c.results, c.citations, c.ai_overview)
    assert none.position is None and none.ai_cited is False
    no_ai = rank_in(Site.parse("example.com", "s"), c.results, c.citations, False)
    assert no_ai.ai_cited is None


def test_first_check_is_a_tracking_event(config):
    verdict = evaluate(config, check(ranking(4, rival=2), ai_refs=["example.com"]), {})
    assert isinstance(verdict.event, Tracking)
    assert verdict.event.position == 4
    assert verdict.event.ai_cited is True
    assert verdict.event.competitors == {"rival.com": 2, "other.org": None}
    assert verdict.baseline["example.com"].position == 4
    assert verdict.baseline["rival.com"].above_you is True
    assert verdict.baseline["other.org"].above_you is False

    quiet = replace(config, delivery=replace(config.delivery, baseline_message=False))
    verdict = evaluate(quiet, check(ranking(4)), {})
    assert verdict.event is None and verdict.baseline["example.com"].position == 4


def test_small_moves_wait_until_they_add_up(config):
    base = first(config, ranking(8))
    for you in (7, 6):
        verdict = evaluate(config, check(ranking(you)), base)
        assert verdict.event is None
        assert verdict.baseline["example.com"].position == 8  # still what you were told
        base = verdict.baseline
    verdict = evaluate(config, check(ranking(5)), base)
    assert isinstance(verdict.event, Change)
    assert verdict.event.reasons == [UP]
    assert (verdict.event.old, verdict.event.new, verdict.event.delta) == (8, 5, 3)
    assert verdict.baseline["example.com"].position == 5


def test_drop_and_top_ten_exit(config):
    base = first(config, ranking(9))
    verdict = evaluate(config, check(ranking(14)), base)
    assert verdict.event.reasons == [DOWN, LEFT_TOP]
    assert verdict.event.delta == -5
    assert not verdict.event.good


def test_top_ten_entry_by_one_place(config):
    base = first(config, ranking(11))
    verdict = evaluate(config, check(ranking(10)), base)
    assert verdict.event.reasons == [ENTERED_TOP]
    assert verdict.event.good


def test_ranked_and_unranked(config):
    base = first(config, ranking(None))
    verdict = evaluate(config, check(ranking(6)), base)
    assert verdict.event.reasons == [RANKED, ENTERED_TOP]
    verdict = evaluate(config, check(ranking(None)), verdict.baseline)
    assert verdict.event.reasons == [UNRANKED]
    assert verdict.event.new is None and verdict.event.old == 6


def test_new_ranking_page(config):
    base = first(config, ranking(3))
    moved = check(ranking(3), paths={"example.com": "/other-page/"})
    verdict = evaluate(config, moved, base)
    assert verdict.event.reasons == [NEW_URL]
    # same page with tracking parameters is not a change
    same = check(ranking(3), paths={"example.com": "/page?utm_source=x"})
    assert evaluate(config, same, base).event is None


def test_ai_overview_citations(config):
    base = first(config, ranking(3))  # no AI Overview yet: unknown
    not_cited = evaluate(config, check(ranking(3), ai_refs=["rival.com"]), base)
    assert not_cited.event is None and not_cited.baseline["example.com"].ai_cited is False
    cited = evaluate(config, check(ranking(3), ai_refs=["example.com"]), not_cited.baseline)
    assert cited.event.reasons == [AI_CITED]
    # no AI Overview shown this time: not a loss
    hidden = evaluate(config, check(ranking(3)), cited.baseline)
    assert hidden.event is None and hidden.baseline["example.com"].ai_cited is True
    lost = evaluate(config, check(ranking(3), ai_refs=["rival.com"]), cited.baseline)
    assert lost.event.reasons == [AI_LOST]


def test_competitor_overtakes(config):
    base = first(config, ranking(3, rival=5, other=1))
    verdict = evaluate(config, check(ranking(4, rival=2, other=6)), base)
    assert verdict.event.reasons == []
    moves = {(o.competitor, o.position, o.above_you) for o in verdict.event.overtakes}
    assert moves == {("rival.com", 2, True), ("other.org", 6, False)}
    assert verdict.baseline["rival.com"].above_you is True

    off = replace(config, alerts=replace(config.alerts, competitors=False))
    assert evaluate(off, check(ranking(4, rival=2, other=6)), base).event is None


def test_competitor_relation_kept_while_you_are_unranked(config):
    base = first(config, ranking(3, rival=5))
    verdict = evaluate(config, check(ranking(None, rival=5)), base)
    assert verdict.event.reasons == [UNRANKED]
    assert verdict.event.overtakes == []
    assert verdict.baseline["rival.com"].above_you is False


def test_digest_compares_with_a_week_earlier(config, tmp_path):
    other_kw = Keyword("trail shoes", "us")
    with Store.open(tmp_path / "r.db") as store:
        store.save_check(_serp(ranking(9, rival=3)), D0 - timedelta(days=8), "a")
        store.save_check(_serp(ranking(8, rival=3)), D0 - timedelta(days=7), "b")
        store.save_check(_serp(ranking(6, rival=7)), D0 - timedelta(days=2), "c")
        store.save_check(_serp(ranking(4, rival=7, other=2)), D0, "d")
        store.save_check(_serp(ranking(None, rival=1), other_kw), D0, "e")
        digest = build_digest(config, store, D0)
    assert digest.day == D0
    assert digest.since == D0 - timedelta(days=7)
    rows = {r.keyword.text: r for r in digest.rows}
    assert (rows["running shoes"].position, rows["running shoes"].previous) == (4, 8)
    assert rows["running shoes"].delta == 4
    assert rows["trail shoes"].compared is False and rows["trail shoes"].delta is None
    assert digest.count(10) == 1 and digest.count(10, previous=True) == 1
    assert digest.ranked == 1
    # other.org reached the top 10 only in the latest check of the week
    new = [(n.host, n.position, n.competitor) for n in digest.new_in_top]
    assert ("other.org", 2, True) in new
    assert all(n.host != "rival.com" for n in digest.new_in_top)
    comps = {c.site: c for c in digest.competitors}
    assert comps["rival.com"].above_you == 1  # above you on trail shoes, where you don't rank
    assert comps["rival.com"].best == 1 and comps["rival.com"].best_label == "trail shoes"
    assert comps["other.org"].above_you == 1


def test_digest_with_nothing_checked(config, tmp_path):
    with Store.open(tmp_path / "r.db") as store:
        assert build_digest(config, store, D0) is None


def test_digest_due_once_per_week(config, tmp_path):
    with Store.open(tmp_path / "r.db") as store:
        sunday = D0 - timedelta(days=1)
        assert digest_due(config, store, D0)  # Monday
        assert digest_due(config, store, D0 + timedelta(days=2))  # missed Monday: Wednesday
        store.mark_digest(iso_week(D0), "x")
        assert not digest_due(config, store, D0 + timedelta(days=2))
        friday = replace(config, digest=replace(config.digest, weekday=5))
        assert not digest_due(friday, store, sunday - timedelta(days=3))  # Thursday
        off = replace(config, digest=replace(config.digest, weekday=None))
        assert not digest_due(off, store, D0)
        assert not has_history_before(config, store, D0)
        store.save_check(_serp(ranking(3)), D0, "x")
        assert not has_history_before(config, store, D0)
        assert has_history_before(config, store, D0 + timedelta(days=1))


def test_iso_week():
    assert iso_week(date(2026, 11, 9)) == "2026-W46"
    assert iso_week(date(2027, 1, 1)) == "2026-W53"


def _serp(hosts, keyword=KW):
    return parse_serp(keyword, serp_json(hosts))
