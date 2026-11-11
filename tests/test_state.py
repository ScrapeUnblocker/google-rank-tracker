import sqlite3
from datetime import date

import pytest
from conftest import serp_json

from google_rank_tracker.collect import parse_serp
from google_rank_tracker.config import Keyword
from google_rank_tracker.state import BaselineRow, StateError, Store

KW = Keyword("running shoes", "us")
DAY = date(2026, 11, 10)


def serp(hosts, **kwargs):
    return parse_serp(KW, serp_json(hosts, **kwargs))


def test_check_round_trip(tmp_path):
    path = tmp_path / "state" / "ranks.db"
    with Store.open(path) as store:
        store.save_check(serp(["example.com", "rival.com"], ai_refs=["rival.com"]), DAY, "t1")
        assert store.has_check(KW, DAY)
        assert not store.has_check(KW, date(2026, 11, 11))
        assert not store.has_check(Keyword("running shoes", "gb"), DAY)
    with Store.open(path) as store:
        check = store.latest_check(KW)
    assert check is not None
    assert check.day == DAY and check.checked_at == "t1"
    assert check.pages == 2 and check.depth == 2 and check.ai_overview
    assert [r.host for r in check.results] == ["example.com", "rival.com"]
    assert [c.host for c in check.citations] == ["rival.com"]


def test_same_day_check_replaces_the_first(tmp_path):
    with Store.open(tmp_path / "r.db") as store:
        store.save_check(serp(["example.com"]), DAY, "t1")
        store.save_check(serp(["rival.com", "example.com"]), DAY, "t2")
        checks = store.checks(KW)
        assert len(checks) == 1
        assert checks[0].checked_at == "t2"
        assert [r.host for r in checks[0].results] == ["rival.com", "example.com"]
        rows = store.conn.execute("SELECT COUNT(*) FROM results").fetchone()[0]
        assert rows == 2


def test_checks_since_and_first_day(tmp_path):
    with Store.open(tmp_path / "r.db") as store:
        for day in (date(2026, 11, 1), date(2026, 11, 5), date(2026, 11, 9)):
            store.save_check(serp(["example.com"]), day, day.isoformat())
        assert [c.day.day for c in store.checks(KW, date(2026, 11, 5))] == [5, 9]
        assert store.latest_check(KW).day == date(2026, 11, 9)
        assert store.first_day(KW) == date(2026, 11, 1)
        assert store.first_day(Keyword("other", "us")) is None
        assert store.stats() == (3, "2026-11-01", "2026-11-09")


def test_baseline_upsert(tmp_path):
    with Store.open(tmp_path / "r.db") as store:
        store.save_baseline(KW, {"example.com": BaselineRow(4, "u1", None, None, "d1")})
        store.save_baseline(
            KW,
            {
                "example.com": BaselineRow(2, "u2", True, None, "d2"),
                "rival.com": BaselineRow(None, None, None, False, "d2"),
            },
        )
        base = store.baseline(KW)
    assert base["example.com"] == BaselineRow(2, "u2", True, None, "d2")
    assert base["rival.com"] == BaselineRow(None, None, None, False, "d2")


def test_digest_marks(tmp_path):
    with Store.open(tmp_path / "r.db") as store:
        assert not store.digest_sent("2026-W46")
        store.mark_digest("2026-W46", "now")
        assert store.digest_sent("2026-W46")


def test_scratch_store_never_touches_the_file(tmp_path):
    path = tmp_path / "r.db"
    with Store.open(path, scratch=True) as store:
        store.save_check(serp(["example.com"]), DAY, "t")
    assert not path.exists()

    with Store.open(path) as store:
        store.save_check(serp(["example.com"]), DAY, "t")
    before = path.read_bytes()
    with Store.open(path, scratch=True) as store:
        assert store.has_check(KW, DAY)
        store.save_check(serp(["rival.com"]), date(2026, 11, 11), "t2")
        store.mark_digest("2026-W46", "now")
    assert path.read_bytes() == before


def test_newer_schema_is_refused(tmp_path):
    path = tmp_path / "r.db"
    with Store.open(path):
        pass
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE meta SET value = '99' WHERE key = 'schema'")
    conn.close()
    with pytest.raises(StateError, match="newer"):
        Store.open(path)


def test_not_a_database(tmp_path):
    path = tmp_path / "r.db"
    path.write_bytes(b"this is not sqlite" * 100)
    with pytest.raises(StateError):
        Store.open(path)
