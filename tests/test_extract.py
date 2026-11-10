"""Tests for full-text extraction via trafilatura."""

from unittest.mock import patch

from remarkable_morning.config import Article
from remarkable_morning.extract import extract_all, extract_text

# A realistic HTML page snippet that trafilatura can extract meaningful text from.
SAMPLE_HTML = """
<html>
<head><title>Test Article</title></head>
<body>
<nav>Home About Contact</nav>
<article>
<h1>Major World Event Unfolds Today</h1>
<p>A significant development occurred today in world affairs, according to
multiple sources familiar with the matter. The event, which had been
anticipated for several weeks, finally came to pass in the early morning
hours.</p>
<p>Officials confirmed that the situation is developing rapidly and that
further statements would be issued throughout the day. Analysts noted
that this represents a turning point in the ongoing situation.</p>
<p>Local residents reported seeing unusual activity beginning overnight,
though the full scope of the event only became clear at dawn. International
observers are expected to arrive within the week.</p>
<p>The economic impact is still being assessed, but early indicators suggest
moderate effects on regional markets. Several companies have already
announced contingency plans.</p>
</article>
<footer>Copyright 2026. All rights reserved.</footer>
</body>
</html>
"""


def test_extract_text_from_raw_html():
    article = Article(
        id="test-1",
        title="Test Article",
        url="http://example.com/article",
        raw_html=SAMPLE_HTML,
    )
    extract_text(article)
    assert article.full_text
    assert "Major World Event" in article.full_text
    # Should NOT contain nav or footer boilerplate
    assert "Home About Contact" not in article.full_text


def test_extract_text_preserves_guardian_articles():
    article = Article(
        id="guardian-1",
        title="Guardian Article",
        url="http://theguardian.com/article",
        raw_html="",
        full_text="Already extracted by Guardian API.",
    )
    extract_text(article)
    assert article.full_text == "Already extracted by Guardian API."


def test_extract_text_empty_raw_html_no_url():
    article = Article(
        id="test-2",
        title="Empty",
        url="",
        raw_html="",
    )
    extract_text(article)
    assert article.full_text == ""


def test_extract_text_fallback_to_fetch_url():
    article = Article(
        id="test-3",
        title="Short Summary",
        url="http://example.com/full-article",
        raw_html="<p>Too short.</p>",  # < 20 words, triggers fallback
    )
    # _extract is called twice: first on raw_html (short), then on fetched HTML (long)
    long_text = " ".join(f"word{i}" for i in range(50))
    with patch(
        "remarkable_morning.extract._extract", side_effect=["Too short.", long_text]
    ), patch(
        "remarkable_morning.extract._fetch_url", return_value="downloaded"
    ):
        extract_text(article)
    assert article.full_text == long_text
    assert len(article.full_text.split()) >= 20


def test_extract_text_fetch_url_failure_is_non_fatal():
    article = Article(
        id="test-4",
        title="Short",
        url="http://example.com/broken",
        raw_html="<p>Short.</p>",
    )
    with patch(
        "remarkable_morning.extract._fetch_url", side_effect=Exception("Network error")
    ):
        extract_text(article)
    # Should not raise; full_text stays empty
    assert article.full_text == ""


def test_extract_all_processes_list():
    articles = [
        Article(id="a1", title="A1", url="http://ex.com/1", raw_html=SAMPLE_HTML),
        Article(id="a2", title="A2", url="", raw_html="", full_text="Already done."),
        Article(id="a3", title="A3", url="", raw_html=""),
    ]
    results = extract_all(articles)
    assert len(results) == 3
    assert results[0].full_text  # extracted
    assert results[1].full_text == "Already done."  # preserved
    assert results[2].full_text == ""  # nothing to extract, stays empty
