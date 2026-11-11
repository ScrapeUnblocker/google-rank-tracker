# Google rank tracker

For site owners, SEOs and content teams: where your site ranks on Google for your keywords, checked every day. When a position really moves, you enter or leave the top 10, a competitor passes you, or Google's AI Overview starts or stops citing you, a message lands in Slack, Discord or any webhook. A weekly digest follows every Monday, and a chart page shows the full history.

[![CI](https://github.com/ScrapeUnblocker/google-rank-tracker/actions/workflows/ci.yml/badge.svg)](https://github.com/ScrapeUnblocker/google-rank-tracker/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![Powered by ScrapeUnblocker](https://img.shields.io/badge/powered%20by-ScrapeUnblocker-6c47ff.svg)](https://scrapeunblocker.com/?utm_source=github&utm_medium=integration&utm_campaign=example-repos)

It runs on a GitHub Actions schedule in your own copy of this repo, so there's nothing to host. Google results come from the [ScrapeUnblocker](https://scrapeunblocker.com/?utm_source=github&utm_medium=integration&utm_campaign=example-repos) serpApi. Every result is kept in an SQLite file that the workflow commits back to the repo. You only hear about it when something changed.

## What actually arrives

These are real messages from a test run that tracked two keywords for python.org, with realpython.com, w3schools.com and geeksforgeeks.org as competitors. The first check was stamped a day earlier so the digest had something to compare with. This is the plain-text form the webhook gets. In Slack and Discord the keyword is bold and the page is a link.

**The first run**: where you stand on each keyword.

```text
Now tracking 2 Google keywords for python.org · US · Wed 11 Nov 2026

• python dataclasses: #1 https://docs.python.org/3/library/dataclasses.html · cited in Google's AI Overview
    realpython.com #3, geeksforgeeks.org #4, w3schools.com #6
• python virtual environment: #1 https://docs.python.org/3/library/venv.html
    realpython.com #2, w3schools.com #3

Compared with: realpython.com, w3schools.com, geeksforgeeks.org
A message arrives here when a position changes. The weekly digest comes on Mondays.
```

**A change**: sent only when something moved.

```text
Google rank changes for python.org · US · Wed 11 Nov 2026

▼ python dataclasses: #1 → #10 (−9) · no longer cited in Google's AI Overview
    new page https://docs.python.org/3.7/library/dataclasses.html (was docs.python.org/3/library/dataclasses.html)
```

That one is real, and it is also why `alerts.recheck` exists. For a minute Google served a completely different results page for this search. Searched again, python.org was back at #1. With `recheck` on (the default), a keyword that would alert is searched a second time, and only a change Google repeats gets sent.

**The weekly digest**, on the first run on or after Monday:

```text
Weekly Google rankings for python.org · US · Wed 11 Nov 2026
Top 3: 2 (was 2) · Top 10: 2 (was 2) · On the first 2 pages: 2 of 2 keywords
Change since Tue 10 Nov 2026.
(AI): your page is cited in Google's AI Overview.

Keyword                      Now  Change  Page
python dataclasses            #1       =  docs.python.org/3/library/dataclasses.html (AI)
python virtual environment    #1       =  docs.python.org/3/library/venv.html

Competitors
• realpython.com: above you on 0 of 2, best #2 (python virtual environment)
• w3schools.com: above you on 0 of 2, best #3 (python virtual environment)
• geeksforgeeks.org: above you on 0 of 2, best #4 (python dataclasses)

New in the top 10 this week
• datacamp.com: #8 for python dataclasses
• dataquest.io: #9 for python dataclasses
```

The webhook also gets every field as JSON:

```json
{
  "source": "google-rank-tracker",
  "kind": "changes",
  "text": "Google rank changes for python.org · US · Wed 11 Nov 2026\n\n▼ python dataclasses: ...",
  "site": "python.org",
  "changes": [
    {
      "keyword": "python dataclasses",
      "country": "us",
      "day": "2026-11-11",
      "old_position": 1,
      "new_position": 10,
      "change": -9,
      "old_url": "https://docs.python.org/3/library/dataclasses.html",
      "new_url": "https://docs.python.org/3.7/library/dataclasses.html",
      "reasons": ["down", "new_url", "ai_lost"],
      "competitors": []
    }
  ]
}
```

`kind` is `tracking`, `changes` or `digest`. The `reasons` are `up`, `down`, `entered_top`, `left_top`, `ranked`, `unranked`, `new_url`, `ai_cited` and `ai_lost`. `competitors` lists the overtakes.

There are two more places to look:

- **The chart page** (GitHub Pages, optional): positions now, with the change since a week earlier, and a line chart per keyword with your site and each competitor over the last 90 days. Hover a day to see every position. Each chart has a table view, and the page works in light and dark mode.
- **The workflow run summary**: every run writes a table of today's positions to the run page in the Actions tab.

## Setup in three steps

1. **Use this template.** Click **Use this template** > **Create a new repository**. A private repository works.
2. **Add secrets.** In your new repo go to **Settings > Secrets and variables > Actions** and add:
   - `SCRAPEUNBLOCKER_KEY`: your API key ([get one here](https://scrapeunblocker.com/?utm_source=github&utm_medium=integration&utm_campaign=example-repos)).
   - At least one destination: `SLACK_WEBHOOK_URL` ([Slack incoming webhook](https://api.slack.com/messaging/webhooks)), `DISCORD_WEBHOOK_URL` (channel settings > Integrations > Webhooks) or `ALERT_WEBHOOK_URL` (any URL that takes a JSON POST: Zapier, Make, n8n, your own endpoint).
3. **Edit `config.yml`.** Put in your site, your competitors and your keywords. It ships tracking python.org, so it works out of the box.

Then open **Actions > Track rankings > Run workflow** to run it now instead of waiting for the next day. The first run posts where you stand. After that you only hear about changes, plus the weekly digest.

**Optional: the chart page.** Go to **Settings > Pages** and set **Source** to **GitHub Actions**. From the next run on, the workflow publishes the chart to `https://<you>.github.io/<repo>/` and every message links to it. A Pages site is public. Publishing one from a private repository needs a paid GitHub plan, and only GitHub Enterprise Cloud can make the site itself private. Leave Pages off if your rankings should stay private; `google-rank-tracker report` builds the same page on your machine.

Until `SCRAPEUNBLOCKER_KEY` is set, the scheduled workflow prints a notice and exits green without checking anything.

### Run it on your own machine instead

```bash
git clone https://github.com/<you>/<your-copy>.git && cd <your-copy>
python -m pip install .
cp .env.example .env            # then fill in the key and a webhook URL
google-rank-tracker run --dry-run  # searches Google, prints what would be sent, saves nothing
google-rank-tracker run
```

For a daily cron job (06:23 every day):

```cron
23 6 * * * cd /path/to/your-copy && /path/to/venv/bin/google-rank-tracker run >> tracker.log 2>&1
```

Or with Docker, no local Python needed:

```bash
docker run --rm -v "$PWD":/app -w /app --env-file .env python:3.13-slim \
  sh -c "pip install -q . && google-rank-tracker run"
```

The `.env` file is read automatically, and real environment variables win.

## How it works

1. **Collect.** One serpApi call per keyword, from the country you set, reading `pages` Google result pages (about 10 results each). The call waits for every result's real URL (`full_urls`), so the ranking page is exact. A keyword is searched at most once per UTC day. Transient errors, including a Google CAPTCHA, are retried after 10 and 30 seconds. A keyword that still fails is skipped and tried again on the next run.
2. **Process.** Your position is the best organic position of any page on your domain or its subdomains (docs.python.org counts for python.org). Ads, the map pack and other boxes don't count. The latest check is compared with what the last message told you, not with yesterday, so a slow slide of one place a day still alerts once it adds up to `min_change`. A message goes out when:
   - your position moved by `min_change` or more,
   - you entered or left the top `top`, even by one place,
   - you started or stopped ranking on the pages read,
   - a different page of your site now ranks,
   - a competitor moved above you, or you moved above one,
   - the AI Overview started or stopped citing you. When Google shows no AI Overview, that isn't counted as a loss.

   With `recheck` on, a keyword that would alert is searched once more, and only the second answer counts.
3. **Deliver.** One message per run with every change, to each destination whose secret is set. On the first run on or after the digest day, the weekly digest follows: positions now against a week earlier, top-3 and top-10 counts, competitors, and sites new to the top 10.
4. **State.** Everything lives in `state/ranks.db` (SQLite), which the workflow commits after each run that checked something. It holds every check, every result with its URL and title, the AI Overview citations, what was last delivered and which weeks got a digest. A second run with no change sends nothing. If no destination accepted a message, nothing is marked as delivered and the next run sends it. A run later the same day sends it without searching again. Positions are worked out from the stored results when they're read, so a competitor you add later shows its full history.

The history is plain SQLite, so you can query it directly:

```bash
sqlite3 -header -column state/ranks.db "
  SELECT c.day, c.keyword, MIN(r.position) AS position, r.url
  FROM checks c JOIN results r ON r.check_id = c.id
  WHERE r.host = 'python.org' OR r.host LIKE '%.python.org'
  GROUP BY c.id ORDER BY c.keyword, c.day"
```

### Commands

| Command | What it does | API calls |
|---|---|---|
| `google-rank-tracker run` | Check keywords not yet checked today, deliver changes and a due digest | 1 per keyword not yet checked today, plus re-checks |
| `google-rank-tracker run --dry-run` | Same, but print what would be sent; send and save nothing | same as `run` |
| `google-rank-tracker run --force` | Search again even if checked today (replaces today's check) | 1 per keyword |
| `google-rank-tracker run --keyword "python dataclasses"` | Only this keyword (no digest) | 1 |
| `google-rank-tracker preview "any search" [--country gb]` | Show the results and where each tracked site ranks; saves nothing | 1 |
| `google-rank-tracker digest [--dry-run]` | Send the weekly digest now | 0 |
| `google-rank-tracker status` | Latest positions from the history | 0 |
| `google-rank-tracker report --out site` | Write the chart page to `site/index.html` | 0 |
| `google-rank-tracker test-destinations` | Send a short test message to each destination | 0 |

Each API call reads `pages` result pages, and each page is one billed request.

## Config reference

Everything lives in `config.yml`. Secrets never go in it.

| Key | Default | Meaning |
|---|---|---|
| `site` | (required) | Your domain, matched with its subdomains. Add a path to track one section: `example.com/blog`. |
| `competitors` | `[]` | Up to 7 domains, matched the same way. They show in the digest and the chart. |
| `keywords` | (required) | The searches, as typed into Google. A keyword can use another country: `{keyword: python kurs, country: de}`. |
| `country` | `us` | Two-letter code of the country Google is searched from. `null` lets the API pick, which makes positions jump. |
| `pages` | `2` | Google result pages read per keyword, 1-10. Each page is one billed request. A site that isn't on them counts as not ranked. |
| `full_urls` | `true` | Wait for every result's real URL so the ranking page is exact (up to about 40 s slower per keyword, billed the same). |
| `alerts.min_change` | `3` | Alert when your position moved at least this many places since the last message. |
| `alerts.top` | `10` | Alert on entering or leaving the top N. `0` turns it off. |
| `alerts.competitors` | `true` | Alert when a competitor moves above you, or you move above one. |
| `alerts.ai_overview` | `true` | Alert when the AI Overview starts or stops citing your site. |
| `alerts.url_change` | `true` | Alert when a different page of your site ranks. |
| `alerts.recheck` | `true` | Search a keyword that would alert once more; only the second answer counts. |
| `digest.weekday` | `monday` | Day of the weekly digest (UTC). It's sent by the first run on or after that day. `null` means no digest. |
| `digest.new_in_top` | `10` | List sites that entered the top N this week. `0` turns it off. |
| `delivery.destinations` | `[slack, discord, webhook]` | Where messages go. A destination whose secret isn't set is skipped. |
| `delivery.baseline_message` | `true` | Post where you stand on the first run, and when a keyword is added. |
| `report.days` | `90` | Days of history in the chart page. |
| `report.title` | `""` | Chart page title. When empty, it's "Google rankings: \<site>". |
| `database` | `state/ranks.db` | The SQLite history. |

| Environment variable | Used for |
|---|---|
| `SCRAPEUNBLOCKER_KEY` | Your ScrapeUnblocker API key (required) |
| `SLACK_WEBHOOK_URL` | Slack incoming webhook |
| `DISCORD_WEBHOOK_URL` | Discord channel webhook |
| `ALERT_WEBHOOK_URL` | Any URL that accepts a JSON POST |
| `RANK_REPORT_URL` | Link to the chart page in every message (set for you when GitHub Pages is on) |

## Cost

With the shipped `config.yml` (5 keywords, `pages: 2`), a day costs up to **10 requests**: 5 calls of 2 result pages each. That's about 300 a month. A day on which a keyword changed costs 2 more requests per changed keyword for the recheck. Any further run on the same day costs nothing, because each keyword is searched at most once a day. To estimate your own setup: keywords × pages per day, plus pages × each keyword that changed.

## Adding another destination

A destination is a small class with `name`, `send`, `preview` and `test`, registered in `REGISTRY` in [`src/google_rank_tracker/sinks.py`](src/google_rank_tracker/sinks.py). Each message renders itself in a style (plain text, Slack or Discord) and splits into parts that fit a length limit. Here is Google Chat as an example:

```python
class GoogleChatSink(ChatSink):
    """Google Chat incoming webhook."""

    name = "google_chat"
    style = messages.PLAIN
    limit = 4000

    def payload(self, text: str) -> dict[str, Any]:
        return {"text": text}


REGISTRY["google_chat"] = SinkSpec("GOOGLE_CHAT_WEBHOOK_URL", GoogleChatSink)
```

Then add `"google_chat"` to `DESTINATIONS` in `config.py`, list it under `delivery.destinations`, pass the secret in `.github/workflows/run.yml`, and add a test with a fake HTTP client like the ones in `tests/test_sinks.py`.

## Development

```bash
python -m pip install -e ".[dev]"
make lint     # ruff check + ruff format --check
make test     # offline tests; the SDK and every webhook are mocked, no API credit is used
make report   # build site/index.html from your history
```

The code is a small package in `src/google_rank_tracker/`:

- `collect.py`: serpApi calls via the SDK, with retries.
- `process.py`: positions, alerts and the digest.
- `messages.py`: message text.
- `sinks.py`: destinations.
- `state.py`: the SQLite history.
- `report.py`: the chart page.
- `cli.py`: the command line.

## Links

- Just want the raw Google results as JSON or CSV? See [google-search-scraper](https://github.com/ScrapeUnblocker/google-search-scraper).
- [ScrapeUnblocker](https://scrapeunblocker.com/?utm_source=github&utm_medium=integration&utm_campaign=example-repos)
- [Documentation](https://docs.scrapeunblocker.com/?utm_source=github&utm_medium=integration&utm_campaign=example-repos)
- [Google SERP scraping guide (serpApi)](https://docs.scrapeunblocker.com/guides/serp-scraping?utm_source=github&utm_medium=integration&utm_campaign=example-repos)
- [Python SDK](https://docs.scrapeunblocker.com/sdks/python?utm_source=github&utm_medium=integration&utm_campaign=example-repos)

## License

[MIT](LICENSE). Copyright (c) 2026 ScrapeUnblocker.
