"""RSS / Atom feed fetching via feedparser.

Returns a list of :class:`~remarkable_morning.config.Article` objects with
``raw_html`` populated from the feed entry content (or summary as fallback).
Full-text extraction is deferred to :mod:`remarkable_morning.extract`.
"""

from __future__ import annotations

import feedparser

from remarkable_morning.config import Article, SourceConfig, make_article_id


def fetch_rss(source: SourceConfig) -> list[Article]:
    """Fetch all RSS feeds for *source* and return parsed articles.

    Respects ``source.limit`` as a cap on the total number of articles
    returned across all feeds for this source.
    """
    articles: list[Article] = []
    for feed_url in source.feeds:
        feed = feedparser.parse(feed_url)
        for entry in feed.entries:
            if len(articles) >= source.limit:
                break
            url = entry.get("link", "")
            articles.append(
                Article(
                    id=make_article_id(source.name, url),
                    title=entry.get("title", ""),
                    url=url,
                    source=source.name,
                    language=source.language,
                    published=_published(entry),
                    author=entry.get("author", ""),
                    raw_html=_entry_html(entry),
                )
            )
        if len(articles) >= source.limit:
            break
    return articles[: source.limit]


def _entry_html(entry) -> str:
    """Best-effort raw HTML from a feedparser entry."""
    # Atom feeds: entry.content (list of content dicts)
    content = entry.get("content")
    if content and isinstance(content, list) and content:
        return content[0].get("value", "")
    # RSS feeds: entry.summary
    return entry.get("summary", "")


def _published(entry) -> str:
    """Normalised publication timestamp string."""
    for key in ("published", "updated", "created"):
        val = entry.get(key)
        if val:
            return val
    return ""
