"""EPUB assembly via ebooklib.

Builds a reflowable EPUB with:
  * a cover page (title, date, morning briefing, article/source counts, TOC by category)
  * one chapter per article (header with source/byline/date/also-covered-by, body text, source URL footer)

Output file: ``morning-paper-YYYY-MM-DD.epub``
"""

from __future__ import annotations

import datetime
import logging
from collections import Counter
from html import escape as _html_escape
from pathlib import Path

import httpx
from ebooklib import epub

from remarkable_morning.config import Article, Config

logger = logging.getLogger(__name__)


def build_epub(
    articles: list[Article],
    briefing: str,
    config: Config,
    date: datetime.date | None = None,
    output_dir: str | Path = ".",
) -> Path:
    """Assemble an EPUB from curated *articles* and return the file path.

    Parameters
    ----------
    articles
        Curated, non-duplicate articles (output of :func:`remarkable_morning.curate.curate`).
    briefing
        The AI-generated morning briefing string.
    config
        The pipeline configuration (used for metadata).
    date
        The issue date (defaults to today).
    output_dir
        Where to write the ``.epub`` file (defaults to current directory).
    """
    if date is None:
        date = datetime.date.today()

    date_str = date.strftime("%Y-%m-%d")
    date_display = date.strftime("%A, %B %-d")
    filename = f"morning-paper-{date_str}.epub"
    output_path = Path(output_dir) / filename

    book = epub.EpubBook()
    book.set_identifier(f"morning-paper-{date_str}")
    book.set_title("Remarkable Morning")
    book.set_language(config.primary_language)
    book.add_author("Remarkable Morning")

    # --- Cover page ---
    cover_html = _cover_html(articles, briefing, date_display, config)
    cover = epub.EpubHtml(title="Cover", file_name="cover.xhtml", content=cover_html)
    book.add_item(cover)

    # --- One chapter per article ---
    chapters: list[epub.EpubHtml] = []
    toc_entries: list[epub.Link] = []
    category_counts = Counter(a.category or "Uncategorized" for a in articles)

    for i, article in enumerate(articles, start=1):
        file_name = f"article_{i:03d}.xhtml"
        chapter_title = _chapter_title(article, i)

        # Download and embed article image if available
        image_item = None
        if article.image_url:
            image_data = _download_image(article.image_url)
            if image_data:
                img_ext = _image_extension(article.image_url)
                img_filename = f"images/article_{i:03d}{img_ext}"
                image_item = epub.EpubItem(
                    uid=f"img{i}",
                    file_name=img_filename,
                    media_type=_image_media_type(img_ext),
                    content=image_data,
                )
                book.add_item(image_item)

        chapter_html = _article_html(article, i, image_item)
        chapter = epub.EpubHtml(
            title=chapter_title,
            file_name=file_name,
            content=chapter_html,
        )
        book.add_item(chapter)
        chapters.append(chapter)
        toc_entries.append(epub.Link(file_name, chapter_title, f"ch{i}"))

    # --- TOC + spine ---
    book.toc = tuple(toc_entries) if toc_entries else ()
    book.spine = [cover] + chapters

    # --- Required items ---
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    # --- CSS ---
    style = _css()
    css_item = epub.EpubItem(
        uid="style",
        file_name="style/morning.css",
        media_type="text/css",
        content=style,
    )
    book.add_item(css_item)

    # --- Write ---
    epub.write_epub(str(output_path), book, {})
    logger.info("EPUB written: %s (%d articles)", output_path, len(articles))
    return output_path


# ---------------------------------------------------------------------------
# HTML builders
# ---------------------------------------------------------------------------

def _cover_html(
    articles: list[Article],
    briefing: str,
    date_display: str,
    config: Config,
) -> str:
    sources = set(a.source for a in articles)
    category_counts = Counter(a.category or "Uncategorized" for a in articles)

    toc_lines = "\n".join(
        f"<li>{_escape(cat)} ({count})</li>"
        for cat, count in category_counts.most_common()
    )

    languages = sorted(set(a.language for a in articles))
    lang_display = " · ".join(l.upper() for l in languages) if languages else "EN"

    safe_briefing = _escape(briefing)
    safe_date = _escape(date_display)

    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Remarkable Morning</title>
<link rel="stylesheet" type="text/css" href="style/morning.css"/>
</head>
<body class="cover">
<h1 class="cover-title">REMARKABLE MORNING</h1>
<p class="cover-date">{safe_date}</p>
<div class="briefing">{safe_briefing}</div>
<p class="cover-stats">{len(articles)} articles · {len(sources)} sources</p>
<p class="cover-langs">{lang_display}</p>
<hr/>
<h2>TABLE OF CONTENTS</h2>
<ul class="toc">
{toc_lines}
</ul>
</body>
</html>"""


def _article_html(article: Article, index: int, image_item=None) -> str:
    header_parts = [f"<h2>{_escape(article.title)}</h2>"]
    meta_parts = [_escape(article.source)]
    if article.author:
        meta_parts.append(_escape(article.author))
    if article.published:
        meta_parts.append(_escape(_format_date(article.published)))
    header_parts.append(f'<p class="byline">{" · ".join(meta_parts)}</p>')

    if article.also_covered_by:
        covered = ", ".join(_escape(s) for s in article.also_covered_by)
        header_parts.append(
            f'<p class="also-covered">Also covered by: {covered}</p>'
        )

    # Embed article image if downloaded
    image_html = ""
    if image_item:
        image_html = f'<div class="article-image"><img src="{image_item.file_name}" alt=""/></div>'

    body = article.full_text or "[Article text unavailable]"
    # Convert plain text to paragraphs
    paragraphs = "\n".join(
        f"<p>{_escape(para)}</p>"
        for para in body.split("\n\n")
        if para.strip()
    )

    return f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>{_escape(_chapter_title(article, index))}</title>
<link rel="stylesheet" type="text/css" href="style/morning.css"/>
</head>
<body>
<div class="article-header">
{''.join(header_parts)}
</div>
{image_html}
<hr/>
<div class="article-body">
{paragraphs}
</div>
<hr/>
<p class="source-url">Source: {_escape(article.url)}</p>
</body>
</html>"""


def _css() -> str:
    return """body { font-family: serif; line-height: 1.6; margin: 1em; }
.cover { text-align: center; }
.cover-title { font-size: 2em; letter-spacing: 0.1em; margin-top: 2em; }
.cover-date { font-size: 1.2em; color: #555; }
.briefing { font-style: italic; margin: 2em 1em; text-align: left; }
.cover-stats, .cover-langs { color: #777; }
.toc { list-style: none; padding-left: 0; }
.byline { color: #555; font-size: 0.9em; }
.also-covered { color: #777; font-size: 0.85em; font-style: italic; }
.source-url { font-size: 0.8em; color: #999; word-break: break-all; }
.article-header h2 { margin-bottom: 0.2em; }
.article-image { text-align: center; margin: 1em 0; }
.article-image img { max-width: 100%; }
"""


def _chapter_title(article: Article, index: int) -> str:
    source = article.source or ""
    title = article.title or ""
    # Truncate title to keep TOC readable
    max_title = 55
    if len(title) > max_title:
        title = title[:max_title].rstrip() + "…"
    return f"{index}. {source}: {title}" if source else f"{index}. {title}"


def _escape(text: str) -> str:
    """Minimal HTML escaping for XHTML text content (&, <, > only)."""
    return _html_escape(text, quote=False)


def _format_date(date_str: str) -> str:
    """Best-effort date formatting for display."""
    # Try common ISO format
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d", "%a, %d %b %Y %H:%M:%S %Z"):
        try:
            dt = datetime.datetime.strptime(date_str.strip(), fmt)
            return dt.strftime("%b %-d, %Y")
        except (ValueError, TypeError):
            continue
    return date_str


# ---------------------------------------------------------------------------
# Image helpers
# ---------------------------------------------------------------------------

_IMAGE_TIMEOUT = 10
_IMAGE_MAX_SIZE = 2_000_000  # 2 MB max — skip very large images


def _download_image(url: str) -> bytes | None:
    """Download an image with a timeout. Returns None on failure."""
    try:
        resp = httpx.get(url, timeout=_IMAGE_TIMEOUT, follow_redirects=True)
        resp.raise_for_status()
        if len(resp.content) > _IMAGE_MAX_SIZE:
            logger.debug("Skipping large image (%d bytes): %s", len(resp.content), url)
            return None
        return resp.content
    except Exception as exc:  # noqa: BLE001
        logger.debug("Image download failed for %s: %s", url, exc)
        return None


def _image_extension(url: str) -> str:
    """Guess image file extension from URL."""
    url_lower = url.lower().split("?")[0]
    for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        if ext in url_lower:
            return ext
    return ".jpg"


def _image_media_type(ext: str) -> str:
    """Map file extension to MIME type."""
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(ext, "image/jpeg")
