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
    book.set_title("Morning Paper")
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
        chapter_html = _article_html(article, i)
        file_name = f"article_{i:03d}.xhtml"
        chapter_title = _chapter_title(article, i)
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
<head><title>Morning Paper</title>
<link rel="stylesheet" type="text/css" href="style/morning.css"/>
</head>
<body class="cover">
<h1 class="cover-title">MORNING PAPER</h1>
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


def _article_html(article: Article, index: int) -> str:
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
"""


def _chapter_title(article: Article, index: int) -> str:
    return f"{index}. {article.title[:60]}"


def _escape(text: str) -> str:
    """Minimal HTML escaping."""
    return _html_escape(text, quote=True)


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
