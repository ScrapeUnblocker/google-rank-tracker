"""Command line: ``google-rank-tracker run | digest | preview | status | report``, plus
``test-destinations``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from scrapeunblocker import Client

from . import __version__, messages
from .collect import FATAL, FetchError, Serp, SerpClient, collect, fetch_serp
from .config import WEEKDAYS, Config, ConfigError, Keyword, load_config
from .process import (
    Change,
    Tracking,
    Verdict,
    WeeklyDigest,
    build_digest,
    digest_due,
    evaluate,
    has_history_before,
    iso_week,
    rank_in,
    ranks_of,
)
from .report import write_report
from .sinks import REGISTRY, DeliveryError, Message, Sink, build_sinks, new_http_client
from .state import StateError, Store

API_KEY_ENV = "SCRAPEUNBLOCKER_KEY"
REPORT_URL_ENV = "RANK_REPORT_URL"
SIGNUP_URL = (
    "https://scrapeunblocker.com/?utm_source=github&utm_medium=integration"
    "&utm_campaign=example-repos"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="google-rank-tracker",
        description=(
            "Track where your site ranks on Google for your keywords. Position changes, "
            "competitor overtakes and AI Overview citations go to Slack, Discord or a webhook, "
            "with a weekly digest, an SQLite history and a chart page. Google results come "
            "from the ScrapeUnblocker serpApi."
        ),
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-c", "--config", default="config.yml", help="config file (default: config.yml)"
    )
    common.add_argument(
        "--env-file",
        default=".env",
        help="read environment variables from this file when it exists (default: .env)",
    )
    common.add_argument("-v", "--verbose", action="store_true", help="log every API call")

    sub = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    run = sub.add_parser(
        "run",
        parents=[common],
        help="check keywords not yet checked today, deliver changes and a due digest",
    )
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="print exactly what would be delivered; deliver nothing and save nothing",
    )
    run.add_argument(
        "--force",
        action="store_true",
        help="check again even when a keyword was already checked today (replaces that check)",
    )
    run.add_argument(
        "--keyword",
        action="append",
        metavar="TEXT",
        help="only this keyword from config.yml; repeat for more (no digest is sent)",
    )
    digest = sub.add_parser(
        "digest", parents=[common], help="send the weekly digest now (no API calls)"
    )
    digest.add_argument(
        "--dry-run", action="store_true", help="print the digest; deliver and save nothing"
    )
    preview = sub.add_parser(
        "preview",
        parents=[common],
        help="search one keyword and show where every tracked site ranks (saves nothing)",
    )
    preview.add_argument("keyword", help="the search, as typed into Google")
    preview.add_argument("--country", help="two-letter country code (default: from config.yml)")
    preview.add_argument(
        "--top", type=int, default=20, help="how many results to list (default: 20)"
    )
    sub.add_parser(
        "status", parents=[common], help="latest positions from the history (no API calls)"
    )
    report = sub.add_parser(
        "report", parents=[common], help="write the HTML chart page (no API calls)"
    )
    report.add_argument("--out", default="site", help="output folder (default: site)")
    sub.add_parser(
        "test-destinations",
        parents=[common],
        help="send a test message to each destination (no API calls)",
    )
    return parser


def load_env_file(path: str | Path, environ: MutableMapping[str, str]) -> None:
    """Minimal ``.env`` reader: ``KEY=value`` lines; real environment variables win."""
    path = Path(path)
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in environ:
            environ[key] = value


def annotation(level: str, title: str, message: str) -> str:
    """A GitHub Actions workflow command (``::notice title=...::...``), safely escaped."""

    def data(text: str) -> str:
        return text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")

    prop = data(title).replace(":", "%3A").replace(",", "%2C")
    return f"::{level} title={prop}::{data(message)}"


# -- messages -----------------------------------------------------------------------------


def changes_message(config: Config, changes: Sequence[Change], report_url: str | None) -> Message:
    return Message(
        kind="changes",
        render=lambda style, limit: messages.render_changes(
            changes,
            style,
            site=config.site.spec,
            country=config.country,
            report_url=report_url,
            limit=limit,
        ),
        data={"site": config.site.spec, "changes": [c.to_dict() for c in changes]},
    )


def tracking_message(config: Config, items: Sequence[Tracking], report_url: str | None) -> Message:
    weekday = config.digest.weekday
    return Message(
        kind="tracking",
        render=lambda style, limit: messages.render_tracking(
            items,
            style,
            site=config.site.spec,
            country=config.country,
            competitors=[c.spec for c in config.competitors],
            digest_day=WEEKDAYS[weekday - 1].capitalize() if weekday else None,
            report_url=report_url,
            limit=limit,
        ),
        data={"site": config.site.spec, "keywords": [t.to_dict() for t in items]},
    )


def digest_message(digest: WeeklyDigest, report_url: str | None) -> Message:
    return Message(
        kind="digest",
        render=lambda style, limit: messages.render_digest(
            digest, style, report_url=report_url, limit=limit
        ),
        data=digest.to_dict(),
    )


def deliver(message: Message, sinks: Sequence[Sink], *, err: TextIO) -> tuple[bool, list[str]]:
    """Send to every sink. Returns ``(at least one took it, names of sinks that failed)``."""
    ok = False
    failed = []
    for sink in sinks:
        try:
            sink.send(message)
        except DeliveryError as exc:
            print(f"Delivery failed: {exc}", file=err)
            failed.append(sink.name)
        else:
            ok = True
    return ok, failed


def print_preview(message: Message, sinks: Sequence[Sink], out: TextIO) -> None:
    if not sinks:
        for text in message.render(messages.PLAIN, 100_000):
            print(f"\n{text}", file=out)
        return
    for sink in sinks:
        for text in sink.preview(message):
            print(f"\n--- {sink.name} ---\n{text}", file=out)


# -- run ----------------------------------------------------------------------------------


def judge(config: Config, store: Store, keywords: Sequence[Keyword]) -> list[Verdict]:
    """Each keyword's latest check against what was last delivered."""
    verdicts = []
    for keyword in keywords:
        check = store.latest_check(keyword)
        if check is not None:
            verdicts.append(evaluate(config, check, store.baseline(keyword)))
    return verdicts


def run_tracker(
    config: Config,
    client: SerpClient | None,
    sinks: Sequence[Sink],
    *,
    dry_run: bool,
    now: datetime,
    force: bool = False,
    only: Sequence[Keyword] | None = None,
    report_url: str | None = None,
    out: TextIO = sys.stdout,
    err: TextIO = sys.stderr,
    annotate: bool = False,
    summary_path: str | None = None,
    sleep: Callable[[float], None] | None = None,
) -> int:
    """One scheduled run: check, compare, deliver, save. Returns the exit code."""
    today = now.date()
    stamp = now.isoformat()
    keywords = list(only or config.keywords)
    exit_code = 0
    with Store.open(config.database, scratch=dry_run) as store:
        due = [k for k in keywords if force or not store.has_check(k, today)]
        if due:
            if client is None:
                raise ConfigError(f"set {API_KEY_ENV} to check keywords")
            print(
                f"Checking {len(due)} keyword(s) on Google, {messages.country_text(config.country)}"
                f", {messages.depth_text(config.pages)} (up to {len(due) * config.pages} API "
                "requests):",
                file=out,
            )
            width = max(len(config.label(k)) for k in due)

            def on_done(keyword: Keyword, serp: Serp | None, error: str | None) -> None:
                label = config.label(keyword)
                if serp is None:
                    print(f"  {label:<{width}}  failed: {error}", file=out)
                    if annotate:
                        print(annotation("warning", f"Can't check {label}", error or ""), file=out)
                    return
                store.save_check(serp, today, stamp)
                you = rank_in(config.site, serp.results, serp.citations, serp.ai_overview)
                where = messages.pos(you.position) if you.position else "not ranked"
                ai = (
                    ", AI Overview" + (" cites you" if you.ai_cited else "")
                    if serp.ai_overview
                    else ""
                )
                print(
                    f"  {label:<{width}}  {where:>10}  ({serp.depth} results, {serp.pages} "
                    f"page(s){ai})",
                    file=out,
                )

            kwargs: dict[str, Any] = {"sleep": sleep} if sleep else {}
            result = collect(
                client,
                due,
                pages=config.pages,
                full_urls=config.full_urls,
                on_done=on_done,
                **kwargs,
            )
            requests = result.requests
            if result.failures and not result.serps:
                exit_code = 1
            # Google can answer the same search differently a minute later. A keyword that
            # would alert is searched once more and the second answer is the one kept.
            checked = {s.keyword.key for s in result.serps}
            again = [
                v.keyword
                for v in judge(config, store, keywords)
                if isinstance(v.event, Change) and v.keyword.key in checked
            ]
            if config.alerts.recheck and again:
                print(
                    f"Searching {len(again)} changed keyword(s) again to confirm (up to "
                    f"{len(again) * config.pages} API requests):",
                    file=out,
                )
                requests += collect(
                    client,
                    again,
                    pages=config.pages,
                    full_urls=config.full_urls,
                    on_done=on_done,
                    **kwargs,
                ).requests
            print(f"API requests used: {requests}", file=out)
        else:
            print(
                f"All {len(keywords)} keyword(s) were already checked today ({today.isoformat()}, "
                "UTC): 0 API requests. Use --force to check again.",
                file=out,
            )

        verdicts = judge(config, store, keywords)
        for v in verdicts:
            if v.event is None:
                store.save_baseline(v.keyword, v.baseline)  # quiet updates, nothing to deliver
        pending: list[tuple[Message, list[Verdict]]] = []
        tracking = [v for v in verdicts if isinstance(v.event, Tracking)]
        changed = [v for v in verdicts if isinstance(v.event, Change)]
        if tracking:
            items = [v.event for v in tracking if isinstance(v.event, Tracking)]
            pending.append((tracking_message(config, items, report_url), tracking))
        if changed:
            items = [v.event for v in changed if isinstance(v.event, Change)]
            pending.append((changes_message(config, items, report_url), changed))
            if annotate:
                for change in items:
                    line = messages.change_lines(change, messages.PLAIN)[0]
                    print(annotation("notice", change.label, line), file=out)

        digest: WeeklyDigest | None = None
        week = iso_week(today)
        if not only and digest_due(config, store, today):
            if has_history_before(config, store, today):
                digest = build_digest(config, store, today)
            else:
                # First week: the "now tracking" message already gave every position.
                store.mark_digest(week, stamp)
        if digest is not None:
            pending.append((digest_message(digest, report_url), []))

        if summary_path:
            delivered = [v.event for v in changed if isinstance(v.event, Change)]
            current = build_digest(config, store, today)
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write(messages.summary_markdown(current, delivered))

        print(file=out)
        if not pending:
            print("Nothing new to deliver.", file=out)
            if dry_run:
                print("Dry run: nothing was sent or saved.", file=out)
            return exit_code
        names = ", ".join(m.kind for m, _ in pending)
        if dry_run:
            print(f"Dry run: would deliver {len(pending)} message(s) ({names}).", file=out)
            print("Nothing was sent or saved.", file=out)
            if not sinks:
                print("No destination secrets are set; the message(s) would be:", file=out)
            for message, _ in pending:
                print_preview(message, sinks, out)
            return exit_code
        if not sinks:
            print(
                f"{len(pending)} message(s) ({names}) are waiting: no destination secret is set. "
                "They are delivered on the first run after you add one.",
                file=out,
            )
            return exit_code
        failed_any: list[str] = []
        for message, owners in pending:
            ok, failed = deliver(message, sinks, err=err)
            failed_any += [f for f in failed if f not in failed_any]
            if not ok:
                print(f"Not delivered: {message.kind}. It is retried next run.", file=out)
                continue
            for v in owners:
                store.save_baseline(v.keyword, v.baseline)
            if message.kind == "digest":
                store.mark_digest(week, stamp)
            took = ", ".join(s.name for s in sinks if s.name not in failed)
            print(f"Delivered {message.kind} to {took}.", file=out)
        if failed_any:
            exit_code = 1
    return exit_code


def send_digest(
    config: Config,
    sinks: Sequence[Sink],
    *,
    dry_run: bool,
    now: datetime,
    report_url: str | None,
    out: TextIO,
    err: TextIO,
) -> int:
    with Store.open(config.database, scratch=dry_run) as store:
        digest = build_digest(config, store, now.date())
        if digest is None:
            print("Nothing checked yet: run `google-rank-tracker run` first.", file=err)
            return 1
        message = digest_message(digest, report_url)
        if dry_run or not sinks:
            print_preview(message, sinks, out)
            return 0
        ok, failed = deliver(message, sinks, err=err)
        if ok:
            store.mark_digest(iso_week(now.date()), now.isoformat())
            took = ", ".join(s.name for s in sinks if s.name not in failed)
            print(f"Delivered the digest to {took}.", file=out)
        return 1 if failed else 0


def preview_keyword(
    config: Config, client: SerpClient, text: str, country: str | None, top: int, out: TextIO
) -> int:
    keyword = Keyword(" ".join(text.split()), (country or config.country).lower())
    try:
        serp = fetch_serp(client, keyword, pages=config.pages, full_urls=config.full_urls)
    except FetchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    tags: dict[int, str] = {}
    for site in config.sites:
        for r in serp.results:
            if site.matches(r.host, r.path):
                tags.setdefault(r.position, site.spec + (" (you)" if site is config.site else ""))
    print(
        f"Google, {messages.country_text(keyword.country)}, {text!r}: {serp.depth} results on "
        f"{serp.pages} page(s){', AI Overview shown' if serp.ai_overview else ''}",
        file=out,
    )
    for r in serp.results[:top]:
        tag = f"  <- {tags[r.position]}" if r.position in tags else ""
        print(f"  {r.position:>3}  {messages.display_url(r.url, 70)}{tag}", file=out)
    print(file=out)
    for site in config.sites:
        rank = rank_in(site, serp.results, serp.citations, serp.ai_overview)
        where = messages.pos(rank.position) if rank.position else "not ranked"
        extra = f", {rank.count} results" if rank.count > 1 else ""
        cited = ", cited in the AI Overview" if rank.ai_cited else ""
        print(f"  {site.spec:<28} {where}{extra}{cited}", file=out)
    print(f"\nAPI requests used: {serp.pages}. Nothing was saved.", file=out)
    return 0


def show_status(config: Config, out: TextIO) -> int:
    if not config.database.is_file():
        print(f"No history yet ({config.database}). Run `google-rank-tracker run`.", file=out)
        return 0
    with Store.open(config.database) as store:
        count, first, last = store.stats()
        print(f"History: {config.database}, {count} checks, {first} to {last}", file=out)
        width = max(len(config.label(k)) for k in config.keywords)
        names = "".join(f"  {s.spec:>16}" for s in config.competitors)
        print(f"\n  {'Keyword':<{width}}  {'Day':<10}  {'You':>5}{names}", file=out)
        for keyword in config.keywords:
            check = store.latest_check(keyword)
            if check is None:
                print(f"  {config.label(keyword):<{width}}  not checked yet", file=out)
                continue
            ranks = ranks_of(check, config.sites)
            cells = "".join(
                f"  {messages.pos(ranks[s.spec].position):>16}" for s in config.competitors
            )
            you = messages.pos(ranks[config.site.spec].position)
            print(
                f"  {config.label(keyword):<{width}}  {check.day.isoformat()}  {you:>5}{cells}",
                file=out,
            )
    return 0


def test_destinations(sinks: Sequence[Sink], skipped: Sequence[str], out: TextIO) -> int:
    for name in skipped:
        print(f"  {name:<8} skipped ({REGISTRY[name].env} not set)", file=out)
    failed = 0
    for sink in sinks:
        try:
            result = sink.test()
        except DeliveryError as exc:
            failed += 1
            print(f"  {sink.name:<8} FAILED {exc}", file=out)
        else:
            print(f"  {sink.name:<8} ok, {result}", file=out)
    return 1 if failed else 0


def _make_client(environ: Mapping[str, str]) -> Client:
    # Ten result pages with full URLs can take a few minutes.
    return Client(api_key=environ[API_KEY_ENV].strip(), timeout=300.0)


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: MutableMapping[str, str] | None = None,
    client_factory: Callable[[Mapping[str, str]], SerpClient] = _make_client,
    http_factory: Callable[[], Any] = new_http_client,
    now: datetime | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    environ = os.environ if environ is None else environ
    out = out or sys.stdout
    err = err or sys.stderr
    if hasattr(out, "reconfigure"):
        # Result titles and URLs can hold any character; never crash on a narrow console.
        out.reconfigure(errors="replace")
    now = now or datetime.now(UTC).replace(microsecond=0)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    # httpx logs full request URLs at INFO, and webhook URLs are secrets.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    load_env_file(args.env_file, environ)
    report_url = (environ.get(REPORT_URL_ENV) or "").strip() or None
    has_key = bool((environ.get(API_KEY_ENV) or "").strip())
    try:
        config = load_config(args.config)
        if args.command == "status":
            return show_status(config, out)
        if args.command == "report":
            with Store.open(config.database, scratch=True) as store:
                path = write_report(config, store, args.out, now)
            print(f"Wrote {path}", file=out)
            return 0
        if args.command in ("run", "preview") and not has_key:
            print(
                f"error: set {API_KEY_ENV} to your ScrapeUnblocker API key "
                f"(get one at {SIGNUP_URL})",
                file=err,
            )
            return 2
        if args.command == "preview":
            return preview_keyword(
                config, client_factory(environ), args.keyword, args.country, args.top, out
            )

        http = http_factory()
        try:
            sinks, skipped = build_sinks(config.delivery, environ, http)
            if args.command == "test-destinations":
                if not sinks:
                    wanted = ", ".join(REGISTRY[n].env for n in config.delivery.destinations)
                    print(f"error: no destination is set up. Set one of: {wanted}", file=err)
                    return 2
                return test_destinations(sinks, skipped, out)
            for name in skipped:
                print(f"({name} skipped: {REGISTRY[name].env} not set)", file=out)
            if args.command == "digest":
                return send_digest(
                    config,
                    sinks,
                    dry_run=args.dry_run,
                    now=now,
                    report_url=report_url,
                    out=out,
                    err=err,
                )
            only = None
            if args.keyword:
                only = []
                for text in args.keyword:
                    found = config.find_keywords(text)
                    if not found:
                        raise ConfigError(f"--keyword {text!r} is not in the keywords list")
                    only += [k for k in found if k not in only]
            return run_tracker(
                config,
                client_factory(environ),
                sinks,
                dry_run=args.dry_run,
                now=now,
                force=args.force,
                only=only,
                report_url=report_url,
                out=out,
                err=err,
                annotate=environ.get("GITHUB_ACTIONS") == "true",
                summary_path=environ.get("GITHUB_STEP_SUMMARY") or None,
            )
        finally:
            close = getattr(http, "close", None)
            if callable(close):
                close()
    except (ConfigError, StateError) as exc:
        print(f"error: {exc}", file=err)
        return 2
    except FATAL as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=err)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
