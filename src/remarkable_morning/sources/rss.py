"""RSS / Atom feed fetching via feedparser.

Returns a list of :class:`~remarkable_morning.config.Article` objects with
``raw_html`` populated from the feed entry content (or summary as fallback).
Full-text extraction is deferred to :mod:`remarkable_morning.extract`.

feedparser.parse fetches URLs internally with no timeout, so we pre-fetch
the feed content via httpx (with a 15s timeout) and pass the body to
feedparser for parsing only.
"""

from __future__ import annotations

import logging

import feedparser
import httpx
import re

from remarkable_morning.config import Article, SourceConfig, make_article_id

logger = logging.getLogger(__name__)

_FEED_TIMEOUT = 15


def fetch_rss(source: SourceConfig) -> list[Article]:
    """Fetch all RSS feeds for *source* and return parsed articles.

    Respects ``source.limit`` as a cap on the total number of articles
    returned across all feeds for this source.
    """
    articles: list[Article] = []
    for feed_url in source.feeds:
        content = _fetch_feed(feed_url)
        if content is None:
            continue
        feed = feedparser.parse(content)
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
                    image_url=_extract_image_url(entry),
                )
            )
        if len(articles) >= source.limit:
            break
    return articles[: source.limit]


def _fetch_feed(url: str) -> str | None:
    """Fetch feed content via httpx with a timeout, or None on failure."""
    try:
        resp = httpx.get(url, timeout=_FEED_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to fetch feed %s: %s", url, exc)
        return None


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


def _extract_image_url(entry) -> str:
    """Extract the best available image URL from a feedparser entry.

    Checks media_content (full-size), media_thumbnail (thumbnail), 
    and <img> tags in content/summary HTML.
    """
    # 1. media_content — full-size images
    media = entry.get("media_content")
    if media and isinstance(media, list) and media:
        for m in media:
            if isinstance(m, dict) and m.get("medium") == "image" and m.get("url"):
                return m["url"]

    # 2. media_thumbnail — thumbnails (better than nothing)
    thumb = entry.get("media_thumbnail")
    if thumb and isinstance(thumb, list) and thumb:
        for t in thumb:
            if isinstance(t, dict) and t.get("url"):
                return t["url"]

    # 3. <img> tags in content or summary HTML
    content = ""
    c = entry.get("content")
    if c and isinstance(c, list) and c:
        content = c[0].get("value", "")
    if not content:
        content = entry.get("summary", "")
    if content:
        imgs = re.findall(r'<img[^>]+src=["\']([^"\']+)["\']', content, re.IGNORECASE)
        if imgs:
            return imgs[0]

    return ""
