"""Tests for RSS and Guardian source fetching (mocked — no network)."""

from unittest.mock import MagicMock, patch

import pytest

from remarkable_morning.config import Article, SourceConfig, make_article_id
from remarkable_morning.sources import guardian, rss

# ---------------------------------------------------------------------------
# RSS fetching
# ---------------------------------------------------------------------------

def _mock_feed_entry(
    title="Breaking News",
    link="http://example.com/article-1",
    summary="<p>This is a summary.</p>",
    author="Reporter Name",
    published="Tue, 10 Nov 2026 05:30:00 GMT",
):
    return {
        "title": title,
        "link": link,
        "summary": summary,
        "author": author,
        "published": published,
    }

# Dummy content returned by _fetch_feed — feedparser.parse receives this
# but is also mocked, so the content value itself is irrelevant.
_FEED_CONTENT = "<rss>mock</rss>"


def test_fetch_rss_returns_articles():
    entries = [
        _mock_feed_entry(title="Article A", link="http://ex.com/a"),
        _mock_feed_entry(title="Article B", link="http://ex.com/b"),
        _mock_feed_entry(title="Article C", link="http://ex.com/c"),
    ]
    mock_feed = MagicMock()
    mock_feed.entries = entries
    with patch("remarkable_morning.sources.rss._fetch_feed", return_value=_FEED_CONTENT), \
         patch("remarkable_morning.sources.rss.feedparser.parse", return_value=mock_feed):
        source = SourceConfig(
            name="Test Feed",
            type="rss",
            feeds=["http://example.com/rss"],
            limit=10,
            language="en",
        )
        articles = rss.fetch_rss(source)

    assert len(articles) == 3
    assert all(isinstance(a, Article) for a in articles)
    assert articles[0].title == "Article A"
    assert articles[0].url == "http://ex.com/a"
    assert articles[0].source == "Test Feed"
    assert articles[0].language == "en"
    assert articles[0].raw_html == "<p>This is a summary.</p>"
    assert articles[0].author == "Reporter Name"


def test_fetch_rss_respects_limit():
    entries = [_mock_feed_entry(link=f"http://ex.com/{i}") for i in range(10)]
    mock_feed = MagicMock()
    mock_feed.entries = entries
    with patch("remarkable_morning.sources.rss._fetch_feed", return_value=_FEED_CONTENT), \
         patch("remarkable_morning.sources.rss.feedparser.parse", return_value=mock_feed):
        source = SourceConfig(
            name="Test",
            type="rss",
            feeds=["http://example.com/rss"],
            limit=3,
        )
        articles = rss.fetch_rss(source)
    assert len(articles) == 3


def test_fetch_rss_multiple_feeds():
    feed1 = MagicMock()
    feed1.entries = [_mock_feed_entry(title="F1-A", link="http://f1/a")]
    feed2 = MagicMock()
    feed2.entries = [_mock_feed_entry(title="F2-A", link="http://f2/a")]
    with patch("remarkable_morning.sources.rss._fetch_feed", return_value=_FEED_CONTENT), \
         patch(
        "remarkable_morning.sources.rss.feedparser.parse",
        side_effect=[feed1, feed2],
    ):
        source = SourceConfig(
            name="Multi",
            type="rss",
            feeds=["http://f1", "http://f2"],
            limit=10,
        )
        articles = rss.fetch_rss(source)
    assert len(articles) == 2
    assert articles[0].title == "F1-A"
    assert articles[1].title == "F2-A"


def test_fetch_rss_uses_content_over_summary():
    entry = {
        "title": "Has Content",
        "link": "http://ex.com/c",
        "summary": "<p>Short summary</p>",
        "content": [{"value": "<p>Full content here</p>"}],
        "published": "2026-11-10T05:30:00Z",
    }
    mock_feed = MagicMock()
    mock_feed.entries = [entry]
    with patch("remarkable_morning.sources.rss._fetch_feed", return_value=_FEED_CONTENT), \
         patch("remarkable_morning.sources.rss.feedparser.parse", return_value=mock_feed):
        source = SourceConfig(name="T", type="rss", feeds=["x"], limit=5)
        articles = rss.fetch_rss(source)
    assert articles[0].raw_html == "<p>Full content here</p>"


def test_fetch_rss_empty_feed():
    mock_feed = MagicMock()
    mock_feed.entries = []
    with patch("remarkable_morning.sources.rss._fetch_feed", return_value=_FEED_CONTENT), \
         patch("remarkable_morning.sources.rss.feedparser.parse", return_value=mock_feed):
        source = SourceConfig(name="T", type="rss", feeds=["x"], limit=5)
        articles = rss.fetch_rss(source)
    assert articles == []


def test_fetch_rss_feed_fetch_failure_skips_feed():
    """If _fetch_feed returns None (network error), the feed is skipped."""
    mock_feed = MagicMock()
    mock_feed.entries = [_mock_feed_entry()]
    with patch("remarkable_morning.sources.rss._fetch_feed", return_value=None), \
         patch("remarkable_morning.sources.rss.feedparser.parse", return_value=mock_feed) as mock_parse:
        source = SourceConfig(name="T", type="rss", feeds=["http://broken.example/rss"], limit=5)
        articles = rss.fetch_rss(source)
    assert articles == []
    mock_parse.assert_not_called()  # feedparser.parse should NOT be called when fetch fails


def test_fetch_rss_article_ids_unique_and_deterministic():
    entries = [
        _mock_feed_entry(link="http://ex.com/1"),
        _mock_feed_entry(link="http://ex.com/2"),
    ]
    mock_feed = MagicMock()
    mock_feed.entries = entries
    with patch("remarkable_morning.sources.rss._fetch_feed", return_value=_FEED_CONTENT), \
         patch("remarkable_morning.sources.rss.feedparser.parse", return_value=mock_feed):
        source = SourceConfig(name="T", type="rss", feeds=["x"], limit=5)
        articles = rss.fetch_rss(source)
    ids = [a.id for a in articles]
    assert len(set(ids)) == 2
    # Re-fetch produces same IDs
    with patch("remarkable_morning.sources.rss._fetch_feed", return_value=_FEED_CONTENT), \
         patch("remarkable_morning.sources.rss.feedparser.parse", return_value=mock_feed):
        articles2 = rss.fetch_rss(source)
    assert [a.id for a in articles2] == ids


# ---------------------------------------------------------------------------
# Guardian fetching
# ---------------------------------------------------------------------------

GUARDIAN_RESPONSE = {
    "response": {
        "results": [
            {
                "id": "world/2026/nov/10/test-article",
                "webTitle": "World Event Happens",
                "webUrl": "https://theguardian.com/world/2026/nov/10/test-article",
                "webPublicationDate": "2026-11-10T05:00:00Z",
                "fields": {
                    "bodyText": "Full article text from the Guardian API.",
                    "byline": "John Smith",
                },
            },
            {
                "id": "world/2026/nov/10/second-article",
                "webTitle": "Second Story",
                "webUrl": "https://theguardian.com/world/2026/nov/10/second-article",
                "webPublicationDate": "2026-11-10T04:00:00Z",
                "fields": {
                    "bodyText": "Second article body text.",
                    "byline": "Jane Doe",
                },
            },
        ],
    }
}


def _mock_response(json_data):
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    return resp


def test_fetch_guardian_returns_articles():
    with patch("remarkable_morning.sources.guardian.httpx.get", return_value=_mock_response(GUARDIAN_RESPONSE)):
        source = SourceConfig(
            name="The Guardian",
            type="guardian_api",
            api_key_env="GUARDIAN_API_KEY",
            sections=["world"],
            limit=15,
        )
        articles = guardian.fetch_guardian(source, api_key="test-key")

    assert len(articles) == 2
    a = articles[0]
    assert a.title == "World Event Happens"
    assert a.url.startswith("https://theguardian.com")
    assert a.source == "The Guardian"
    assert a.language == "en"
    assert a.full_text == "Full article text from the Guardian API."
    assert a.raw_html == ""
    assert a.author == "John Smith"
    assert a.published == "2026-11-10T05:00:00Z"


def test_fetch_guardian_respects_limit():
    with patch("remarkable_morning.sources.guardian.httpx.get", return_value=_mock_response(GUARDIAN_RESPONSE)):
        source = SourceConfig(
            name="The Guardian",
            type="guardian_api",
            api_key_env="GUARDIAN_API_KEY",
            sections=["world"],
            limit=1,
        )
        articles = guardian.fetch_guardian(source, api_key="test-key")
    assert len(articles) == 1


def test_fetch_guardian_multiple_sections():
    responses = [_mock_response(GUARDIAN_RESPONSE), _mock_response(GUARDIAN_RESPONSE)]
    with patch("remarkable_morning.sources.guardian.httpx.get", side_effect=responses):
        source = SourceConfig(
            name="The Guardian",
            type="guardian_api",
            api_key_env="GUARDIAN_API_KEY",
            sections=["world", "technology"],
            limit=4,
        )
        articles = guardian.fetch_guardian(source, api_key="test-key")
    # 2 results per section × 2 sections = 4, capped at limit=4
    assert len(articles) == 4


def test_fetch_guardian_missing_api_key():
    source = SourceConfig(
        name="The Guardian",
        type="guardian_api",
        api_key_env="GUARDIAN_API_KEY",
        sections=["world"],
        limit=10,
    )
    with pytest.raises(ValueError, match="API key"):
        guardian.fetch_guardian(source, api_key="")


def test_fetch_guardian_passes_api_key_and_section():
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _mock_response(GUARDIAN_RESPONSE)

    with patch("remarkable_morning.sources.guardian.httpx.get", side_effect=fake_get):
        source = SourceConfig(
            name="The Guardian",
            type="guardian_api",
            api_key_env="GUARDIAN_API_KEY",
            sections=["politics"],
            limit=10,
        )
        guardian.fetch_guardian(source, api_key="my-secret-key")

    assert captured["params"]["api-key"] == "my-secret-key"
    assert captured["params"]["section"] == "politics"
    assert "bodyText" in captured["params"]["show-fields"]
