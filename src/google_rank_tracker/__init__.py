"""google-rank-tracker: daily Google positions for your keywords, delivered where you look.

Each run reads Google results through the ScrapeUnblocker serpApi (one check per keyword
per day), stores every result in an SQLite history, compares your site's position with
what you were last told, and sends real changes (moves, top-10 entries and exits,
competitor overtakes, AI Overview citations) plus a weekly digest to Slack, Discord or a
webhook. ``google-rank-tracker report`` turns the history into a chart page.

    from google_rank_tracker import load_config, Store, build_digest
"""

from .collect import Serp, collect, fetch_serp, parse_serp
from .config import Config, ConfigError, Keyword, Site, load_config
from .process import Change, Tracking, WeeklyDigest, build_digest, evaluate, rank_in
from .state import Store

__version__ = "0.1.0"

__all__ = [
    "Change",
    "Config",
    "ConfigError",
    "Keyword",
    "Serp",
    "Site",
    "Store",
    "Tracking",
    "WeeklyDigest",
    "__version__",
    "build_digest",
    "collect",
    "evaluate",
    "fetch_serp",
    "load_config",
    "parse_serp",
    "rank_in",
]
