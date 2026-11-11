"""The SQLite history: every check, every result, and what was last delivered.

One file (``state/ranks.db`` by default) that the workflow commits after each run. Tables:

* ``checks``: one row per keyword per day (UTC): when, how many pages, AI Overview shown.
* ``results``: every organic result of a check: position, host, URL, title.
* ``ai_citations``: the pages and sites Google's AI Overview cited.
* ``baseline``: per keyword and tracked site, what the last delivered message said. Alerts
  compare the latest check with this, so a second run with no change sends nothing and an
  undelivered alert is simply found again next run.
* ``digests``: the weeks a digest went out.

Positions are worked out from ``results`` when read, so a competitor added to the config
later gets its full history.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .collect import Citation, Result, Serp
from .config import Keyword

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY,
    keyword TEXT NOT NULL,
    country TEXT NOT NULL,          -- '' when the API picked the country
    day TEXT NOT NULL,              -- YYYY-MM-DD, UTC
    checked_at TEXT NOT NULL,       -- ISO 8601, UTC
    pages INTEGER NOT NULL,         -- Google result pages read (billed requests)
    results INTEGER NOT NULL,       -- organic results read
    ai_overview INTEGER NOT NULL,   -- 1 when Google showed an AI Overview
    UNIQUE (keyword, country, day)
);
CREATE TABLE IF NOT EXISTS results (
    check_id INTEGER NOT NULL REFERENCES checks (id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    host TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS results_check ON results (check_id);
CREATE TABLE IF NOT EXISTS ai_citations (
    check_id INTEGER NOT NULL REFERENCES checks (id) ON DELETE CASCADE,
    host TEXT NOT NULL,
    url TEXT NOT NULL               -- '' when only the site was named
);
CREATE INDEX IF NOT EXISTS ai_citations_check ON ai_citations (check_id);
CREATE TABLE IF NOT EXISTS baseline (
    keyword TEXT NOT NULL,
    country TEXT NOT NULL,
    site TEXT NOT NULL,
    position INTEGER,               -- NULL: not in the results read
    url TEXT,
    ai_cited INTEGER,               -- NULL: no AI Overview seen yet
    above_you INTEGER,              -- competitors: 1 above your site, 0 below, NULL unknown
    day TEXT NOT NULL,
    PRIMARY KEY (keyword, country, site)
);
CREATE TABLE IF NOT EXISTS digests (
    week TEXT PRIMARY KEY,          -- ISO week, e.g. 2026-W46
    sent_at TEXT NOT NULL
);
"""


class StateError(RuntimeError):
    """The database cannot be opened or is from a newer version of this tool."""


@dataclass(frozen=True)
class Check:
    id: int
    keyword: Keyword
    day: date
    checked_at: str
    pages: int
    depth: int
    ai_overview: bool
    results: tuple[Result, ...]
    citations: tuple[Citation, ...]


@dataclass(frozen=True)
class BaselineRow:
    position: int | None
    url: str | None
    ai_cited: bool | None
    above_you: bool | None
    day: str


def _opt_bool(value: int | None) -> bool | None:
    return None if value is None else bool(value)


def _opt_int(value: bool | None) -> int | None:
    return None if value is None else int(value)


class Store:
    """A small wrapper around one SQLite connection.

    ``Store.open(path)`` works on the file. ``Store.open(path, scratch=True)`` loads a copy
    into memory, so ``--dry-run`` runs the exact same code and nothing reaches the disk.
    """

    def __init__(self, conn: sqlite3.Connection, path: Path) -> None:
        self.conn = conn
        self.path = path
        conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    @classmethod
    def open(cls, path: str | Path, *, scratch: bool = False) -> Store:
        path = Path(path)
        try:
            if scratch:
                conn = sqlite3.connect(":memory:")
                if path.is_file():
                    with closing(sqlite3.connect(path)) as disk:
                        disk.backup(conn)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(path)
                # Plain rollback journal: no -wal/-shm side files next to the committed file.
                conn.execute("PRAGMA journal_mode = DELETE")
        except (sqlite3.Error, OSError) as exc:
            raise StateError(f"cannot open {path}: {exc}") from None
        return cls(conn, path)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _migrate(self) -> None:
        try:
            with self.conn:
                self.conn.executescript(SCHEMA)
                row = self.conn.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
                if row is None:
                    self.conn.execute(
                        "INSERT INTO meta (key, value) VALUES ('schema', ?)", (str(SCHEMA_VERSION),)
                    )
                elif int(row[0]) > SCHEMA_VERSION:
                    raise StateError(
                        f"{self.path} was written by a newer google-rank-tracker; upgrade first"
                    )
        except sqlite3.DatabaseError as exc:
            raise StateError(f"{self.path} is not a google-rank-tracker database: {exc}") from None

    # -- checks -------------------------------------------------------------------------

    def has_check(self, keyword: Keyword, day: date) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM checks WHERE keyword = ? AND country = ? AND day = ?",
            (*keyword.key, day.isoformat()),
        ).fetchone()
        return row is not None

    def save_check(self, serp: Serp, day: date, checked_at: str) -> int:
        """Store one check. A second check on the same day replaces the first (``--force``)."""
        with self.conn:
            self.conn.execute(
                "DELETE FROM checks WHERE keyword = ? AND country = ? AND day = ?",
                (*serp.keyword.key, day.isoformat()),
            )
            cur = self.conn.execute(
                "INSERT INTO checks (keyword, country, day, checked_at, pages, results,"
                " ai_overview) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    *serp.keyword.key,
                    day.isoformat(),
                    checked_at,
                    serp.pages,
                    serp.depth,
                    int(serp.ai_overview),
                ),
            )
            check_id = int(cur.lastrowid or 0)
            self.conn.executemany(
                "INSERT INTO results (check_id, position, host, url, title) VALUES (?, ?, ?, ?, ?)",
                [(check_id, r.position, r.host, r.url, r.title) for r in serp.results],
            )
            self.conn.executemany(
                "INSERT INTO ai_citations (check_id, host, url) VALUES (?, ?, ?)",
                [(check_id, c.host, c.url) for c in serp.citations],
            )
        return check_id

    def _load(self, rows: Iterable[sqlite3.Row | tuple]) -> list[Check]:
        rows = list(rows)
        if not rows:
            return []
        ids = [row[0] for row in rows]
        marks = ",".join("?" * len(ids))
        results: dict[int, list[Result]] = {i: [] for i in ids}
        for check_id, position, host, url, title in self.conn.execute(
            f"SELECT check_id, position, host, url, title FROM results WHERE check_id IN ({marks})"
            " ORDER BY check_id, position",
            ids,
        ):
            results[check_id].append(Result(position=position, url=url, host=host, title=title))
        citations: dict[int, list[Citation]] = {i: [] for i in ids}
        for check_id, host, url in self.conn.execute(
            f"SELECT check_id, host, url FROM ai_citations WHERE check_id IN ({marks})"
            " ORDER BY rowid",
            ids,
        ):
            citations[check_id].append(Citation(host=host, url=url))
        return [
            Check(
                id=cid,
                keyword=Keyword(keyword, country),
                day=date.fromisoformat(day),
                checked_at=checked_at,
                pages=pages,
                depth=depth,
                ai_overview=bool(ai),
                results=tuple(results[cid]),
                citations=tuple(citations[cid]),
            )
            for cid, keyword, country, day, checked_at, pages, depth, ai in rows
        ]

    _COLUMNS = "id, keyword, country, day, checked_at, pages, results, ai_overview"

    def latest_check(self, keyword: Keyword) -> Check | None:
        rows = self.conn.execute(
            f"SELECT {self._COLUMNS} FROM checks WHERE keyword = ? AND country = ?"
            " ORDER BY day DESC LIMIT 1",
            keyword.key,
        )
        found = self._load(rows)
        return found[0] if found else None

    def checks(self, keyword: Keyword, since: date | None = None) -> list[Check]:
        """Every check of ``keyword`` (from ``since`` on), oldest first."""
        rows = self.conn.execute(
            f"SELECT {self._COLUMNS} FROM checks WHERE keyword = ? AND country = ? AND day >= ?"
            " ORDER BY day",
            (*keyword.key, since.isoformat() if since else ""),
        )
        return self._load(rows)

    def first_day(self, keyword: Keyword) -> date | None:
        row = self.conn.execute(
            "SELECT MIN(day) FROM checks WHERE keyword = ? AND country = ?", keyword.key
        ).fetchone()
        return date.fromisoformat(row[0]) if row and row[0] else None

    # -- baseline -----------------------------------------------------------------------

    def baseline(self, keyword: Keyword) -> dict[str, BaselineRow]:
        rows = self.conn.execute(
            "SELECT site, position, url, ai_cited, above_you, day FROM baseline"
            " WHERE keyword = ? AND country = ?",
            keyword.key,
        )
        return {
            site: BaselineRow(position, url, _opt_bool(ai), _opt_bool(above), day)
            for site, position, url, ai, above, day in rows
        }

    def save_baseline(self, keyword: Keyword, rows: dict[str, BaselineRow]) -> None:
        with self.conn:
            self.conn.executemany(
                "INSERT INTO baseline (keyword, country, site, position, url, ai_cited, above_you,"
                " day) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (keyword, country, site) DO"
                " UPDATE SET position = excluded.position, url = excluded.url, ai_cited ="
                " excluded.ai_cited, above_you = excluded.above_you, day = excluded.day",
                [
                    (
                        *keyword.key,
                        site,
                        row.position,
                        row.url,
                        _opt_int(row.ai_cited),
                        _opt_int(row.above_you),
                        row.day,
                    )
                    for site, row in rows.items()
                ],
            )

    # -- digests ------------------------------------------------------------------------

    def digest_sent(self, week: str) -> bool:
        row = self.conn.execute("SELECT 1 FROM digests WHERE week = ?", (week,)).fetchone()
        return row is not None

    def mark_digest(self, week: str, sent_at: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO digests (week, sent_at) VALUES (?, ?)", (week, sent_at)
            )

    def stats(self) -> tuple[int, str | None, str | None]:
        """``(checks, first day, last day)`` over all keywords."""
        row = self.conn.execute("SELECT COUNT(*), MIN(day), MAX(day) FROM checks").fetchone()
        return int(row[0]), row[1], row[2]
