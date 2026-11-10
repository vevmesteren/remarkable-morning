"""Tests for config loading and schema validation."""

import textwrap
from pathlib import Path

import pytest

from remarkable_morning.config import Article, Config, load_config, make_article_id


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_YAML = textwrap.dedent("""\
    schedule: "05:30"
    remarkable_folder: "/News"
    retention_days: 7
    primary_language: en
    sources:
      - name: "BBC News"
        type: rss
        feeds:
          - "http://feeds.bbci.co.uk/news/world/rss.xml"
        limit: 10
      - name: "The Guardian"
        type: guardian_api
        api_key_env: "GUARDIAN_API_KEY"
        sections: [world, technology]
        limit: 15
      - name: "Der Spiegel"
        type: rss
        feeds: ["https://www.spiegel.de/index.rss"]
        limit: 8
        language: de
    curation:
      model: "z-ai/glm-5.2"
      max_articles: 25
      categories:
        - World
        - Business & Fintech
      briefing: true
      dedup: true
""")


@pytest.fixture
def valid_config(tmp_path: Path) -> Config:
    path = tmp_path / "feeds.yaml"
    path.write_text(VALID_YAML)
    return load_config(path)


# ---------------------------------------------------------------------------
# load_config — happy path
# ---------------------------------------------------------------------------

def test_load_config_parses_top_level(valid_config: Config):
    assert valid_config.schedule == "05:30"
    assert valid_config.remarkable_folder == "/News"
    assert valid_config.retention_days == 7
    assert valid_config.primary_language == "en"


def test_load_config_parses_sources(valid_config: Config):
    assert len(valid_config.sources) == 3
    bbc = valid_config.sources[0]
    assert bbc.name == "BBC News"
    assert bbc.type == "rss"
    assert bbc.feeds == ["http://feeds.bbci.co.uk/news/world/rss.xml"]
    assert bbc.limit == 10
    assert bbc.language == "en"

    guardian = valid_config.sources[1]
    assert guardian.name == "The Guardian"
    assert guardian.type == "guardian_api"
    assert guardian.api_key_env == "GUARDIAN_API_KEY"
    assert guardian.sections == ["world", "technology"]
    assert guardian.limit == 15

    spiegel = valid_config.sources[2]
    assert spiegel.language == "de"


def test_load_config_parses_curation(valid_config: Config):
    cur = valid_config.curation
    assert cur.model == "z-ai/glm-5.2"
    assert cur.max_articles == 25
    assert cur.categories == ["World", "Business & Fintech"]
    assert cur.briefing is True
    assert cur.dedup is True


# ---------------------------------------------------------------------------
# load_config — validation errors
# ---------------------------------------------------------------------------

def test_missing_sources_key(tmp_path: Path):
    path = tmp_path / "feeds.yaml"
    path.write_text("curation:\n  model: test\n")
    with pytest.raises(ValueError, match="sources"):
        load_config(path)


def test_missing_curation_key(tmp_path: Path):
    path = tmp_path / "feeds.yaml"
    path.write_text("sources:\n  - name: BBC\n    type: rss\n    feeds: ['x']\n")
    with pytest.raises(ValueError, match="curation"):
        load_config(path)


def test_invalid_source_type(tmp_path: Path):
    path = tmp_path / "feeds.yaml"
    path.write_text(
        "sources:\n  - name: Bad\n    type: twitter\n    feeds: ['x']\n"
        "curation:\n  model: test\n"
    )
    with pytest.raises(ValueError, match="invalid type"):
        load_config(path)


def test_rss_source_missing_feeds(tmp_path: Path):
    path = tmp_path / "feeds.yaml"
    path.write_text(
        "sources:\n  - name: BBC\n    type: rss\n"
        "curation:\n  model: test\n"
    )
    with pytest.raises(ValueError, match="missing or empty 'feeds'"):
        load_config(path)


def test_guardian_source_missing_api_key_env(tmp_path: Path):
    path = tmp_path / "feeds.yaml"
    path.write_text(
        "sources:\n  - name: Guardian\n    type: guardian_api\n"
        "curation:\n  model: test\n"
    )
    with pytest.raises(ValueError, match="api_key_env"):
        load_config(path)


def test_duplicate_source_name(tmp_path: Path):
    path = tmp_path / "feeds.yaml"
    path.write_text(
        "sources:\n"
        "  - name: BBC\n    type: rss\n    feeds: ['x']\n"
        "  - name: BBC\n    type: rss\n    feeds: ['y']\n"
        "curation:\n  model: test\n"
    )
    with pytest.raises(ValueError, match="Duplicate source name"):
        load_config(path)


def test_empty_config_file(tmp_path: Path):
    path = tmp_path / "feeds.yaml"
    path.write_text("")
    with pytest.raises(ValueError, match="empty"):
        load_config(path)


# ---------------------------------------------------------------------------
# Article dataclass
# ---------------------------------------------------------------------------

def test_article_first_200_words():
    article = Article(
        id="abc123",
        title="Test",
        url="http://example.com",
        full_text=" ".join(f"word{i}" for i in range(300)),
    )
    words = article.first_200_words.split()
    assert len(words) == 200
    assert words[0] == "word0"
    assert words[199] == "word199"


def test_article_first_200_words_fallback_to_raw_html():
    article = Article(
        id="abc",
        title="T",
        url="http://x",
        raw_html="<p>Hello <b>world</b> from the article.</p>",
    )
    result = article.first_200_words
    assert "Hello" in result
    assert "<p>" not in result
    assert "<b>" not in result


def test_article_is_duplicate():
    a = Article(id="x", title="t", url="u")
    assert not a.is_duplicate
    a.duplicate_of = "other-id"
    assert a.is_duplicate


def test_make_article_id_deterministic():
    id1 = make_article_id("BBC News", "http://bbc.co.uk/1")
    id2 = make_article_id("BBC News", "http://bbc.co.uk/1")
    id3 = make_article_id("BBC News", "http://bbc.co.uk/2")
    assert id1 == id2
    assert id1 != id3
    assert len(id1) == 12
