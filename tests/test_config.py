from pathlib import Path

import pytest

from google_rank_tracker.config import ConfigError, Keyword, Site, load_config, parse_config

ROOT = Path(__file__).resolve().parents[1]


def test_shipped_config_loads_with_working_defaults():
    config = load_config(ROOT / "config.yml")
    assert config.site.spec == "python.org"
    assert [c.spec for c in config.competitors] == [
        "realpython.com",
        "w3schools.com",
        "geeksforgeeks.org",
    ]
    assert len(config.keywords) == 5
    assert all(k.country == "us" for k in config.keywords)
    assert config.pages == 2
    assert config.alerts.min_change == 3 and config.alerts.top == 10 and config.alerts.recheck
    assert config.digest.weekday == 1
    assert config.delivery.destinations == ("slack", "discord", "webhook")
    assert config.database == ROOT / "state" / "ranks.db"
    assert config.sites[0] is config.site


@pytest.mark.parametrize(
    ("raw", "host", "path"),
    [
        ("example.com", "example.com", ""),
        ("www.Example.com", "example.com", ""),
        ("https://www.example.com/blog/", "example.com", "/blog"),
        ("shop.example.co.uk/en", "shop.example.co.uk", "/en"),
    ],
)
def test_site_parse(raw, host, path):
    site = Site.parse(raw, "site")
    assert (site.host, site.path) == (host, path)


def test_site_matches_subdomains_and_path_boundaries():
    site = Site.parse("example.com", "site")
    assert site.matches("example.com")
    assert site.matches("www.example.com")
    assert site.matches("docs.example.com")
    assert not site.matches("notexample.com")
    assert not site.matches("example.com.evil.net")

    blog = Site.parse("example.com/blog", "site")
    assert blog.matches("example.com", "/blog")
    assert blog.matches("www.example.com", "/blog/post-1")
    assert not blog.matches("example.com", "/blogger")
    assert not blog.matches("example.com", "/")


def test_keyword_country_override_and_label():
    config = parse_config(
        {
            "site": "example.com",
            "keywords": ["a  shoe ", {"keyword": "laufschuhe", "country": "DE"}],
        }
    )
    assert config.keywords == (Keyword("a shoe", "us"), Keyword("laufschuhe", "de"))
    assert config.label(config.keywords[0]) == "a shoe"
    assert config.label(config.keywords[1]) == "laufschuhe (DE)"
    assert config.find_keywords("A SHOE") == [Keyword("a shoe", "us")]


def test_digest_off_and_empty_destinations():
    config = parse_config(
        {
            "site": "example.com",
            "keywords": ["x"],
            "digest": {"weekday": None},
            "delivery": {"destinations": []},
        }
    )
    assert config.digest.weekday is None
    assert config.delivery.destinations == ()


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"pages": 11}, "pages: must be 1-10"),
        ({"pages": "2"}, "expected a whole number"),
        ({"colour": "red"}, "unknown setting(s) colour"),
        ({"country": "usa"}, "two-letter country code"),
        ({"keywords": []}, "keywords: expected a list"),
        ({"keywords": ["a", "a"]}, "listed twice"),
        ({"competitors": ["example.com"]}, "listed twice"),
        ({"competitors": ["not a domain"]}, "is not a domain"),
        ({"digest": {"weekday": "someday"}}, "digest.weekday"),
        ({"delivery": {"destinations": ["email"]}}, "delivery.destinations"),
        ({"alerts": {"min_change": 0}}, "alerts.min_change"),
        ({"alerts": {"recheck": "yes"}}, "expected true or false"),
    ],
)
def test_bad_values_are_explained(change, message):
    raw = {"site": "example.com", "keywords": ["x"], **change}
    with pytest.raises(ConfigError, match=None) as info:
        parse_config(raw)
    assert message in str(info.value)


def test_too_many_competitors():
    raw = {"site": "example.com", "keywords": ["x"], "competitors": [f"c{i}.com" for i in range(8)]}
    with pytest.raises(ConfigError, match="at most 7"):
        parse_config(raw)


def test_missing_and_broken_files(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yml")
    bad = tmp_path / "bad.yml"
    bad.write_text("site: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(bad)
