"""Tests for EPUB assembly via ebooklib."""

import datetime
import zipfile
from pathlib import Path

import pytest
from ebooklib import epub

from remarkable_morning.config import Article, Config, CurationConfig
from remarkable_morning.epub import build_epub


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def config():
    return Config(
        primary_language="en",
        curation=CurationConfig(categories=["World", "AI & Tech", "Politics"]),
    )


@pytest.fixture
def curated_articles():
    return [
        Article(
            id="a1",
            title="World Event Happens",
            url="http://guardian.com/1",
            source="The Guardian",
            language="en",
            published="2026-11-10T05:00:00Z",
            author="John Smith",
            full_text="This is the first article. It has multiple paragraphs.\n\nSecond paragraph here with more content.",
            category="World",
            also_covered_by=["BBC News", "CBC News"],
        ),
        Article(
            id="a2",
            title="Tech Company Launches Product",
            url="http://ars.com/1",
            source="Ars Technica",
            language="en",
            published="2026-11-10T04:00:00Z",
            author="Jane Doe",
            full_text="A major tech company launched a new product today. " * 5,
            category="AI & Tech",
        ),
        Article(
            id="a3",
            title="Political Development",
            url="http://bbc.com/1",
            source="BBC News",
            language="en",
            published="2026-11-10T03:00:00Z",
            full_text="A political development occurred. " * 5,
            category="Politics",
        ),
    ]


BRIEFING = "Here's what happened overnight. A world event unfolded. A tech company launched a product. Political developments occurred."


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_build_epub_creates_file(curated_articles, config, tmp_path):
    path = build_epub(curated_articles, BRIEFING, config, output_dir=tmp_path)
    assert path.exists()
    assert path.name == "morning-paper-2026-11-10.epub"
    assert path.stat().st_size > 0


def test_build_epub_filename_includes_date(curated_articles, config, tmp_path):
    date = datetime.date(2026, 7, 4)
    path = build_epub(curated_articles, BRIEFING, config, date=date, output_dir=tmp_path)
    assert path.name == "morning-paper-2026-07-04.epub"


def test_build_epub_is_valid_zip(curated_articles, config, tmp_path):
    path = build_epub(curated_articles, BRIEFING, config, output_dir=tmp_path)
    assert zipfile.is_zipfile(path)


def test_build_epub_can_be_read_by_ebooklib(curated_articles, config, tmp_path):
    """Round-trip: write EPUB, read it back with ebooklib, verify structure."""
    path = build_epub(curated_articles, BRIEFING, config, output_dir=tmp_path)
    book = epub.read_epub(str(path))

    # Should have cover + 3 article chapters + nav + ncx
    items = list(book.get_items())
    item_types = {item.get_type() for item in items}
    assert 9 in item_types  # ITEM_HTML

    html_items = [
        item for item in items if item.get_type() == 9
    ]
    # cover + 3 articles = 4 content HTML files (nav may also appear as type 9)
    content_html = [
        item for item in html_items
        if item.get_name() == "cover.xhtml" or item.get_name().startswith("article_")
    ]
    assert len(content_html) == 4


def test_build_epub_cover_has_briefing_and_stats(curated_articles, config, tmp_path):
    path = build_epub(curated_articles, BRIEFING, config, output_dir=tmp_path)
    book = epub.read_epub(str(path))

    cover = None
    for item in book.get_items():
        if item.get_name() == "cover.xhtml":
            cover = item
            break
    assert cover is not None
    content = cover.get_content().decode("utf-8")
    assert "MORNING PAPER" in content
    assert BRIEFING in content
    assert "3 articles" in content
    assert "TABLE OF CONTENTS" in content
    assert "World" in content
    assert "Tech" in content


def test_build_epub_article_has_header_and_body(curated_articles, config, tmp_path):
    path = build_epub(curated_articles, BRIEFING, config, output_dir=tmp_path)
    book = epub.read_epub(str(path))

    html_items = [
        item for item in book.get_items()
        if item.get_type() == 9 and item.get_name().startswith("article_")
    ]
    assert len(html_items) == 3

    first = html_items[0].get_content().decode("utf-8")
    assert "World Event Happens" in first
    assert "The Guardian" in first
    assert "John Smith" in first
    assert "Also covered by: BBC News, CBC News" in first
    assert "http://guardian.com/1" in first
    assert "first article" in first


def test_build_epub_toc_has_entries(curated_articles, config, tmp_path):
    path = build_epub(curated_articles, BRIEFING, config, output_dir=tmp_path)
    book = epub.read_epub(str(path))
    toc = book.toc
    assert len(toc) == 3


def test_build_epub_empty_articles(config, tmp_path):
    path = build_epub([], "", config, output_dir=tmp_path)
    assert path.exists()
    book = epub.read_epub(str(path))
    # Only cover, no article chapters
    html_items = [
        item for item in book.get_items()
        if item.get_type() == 9 and item.get_name().startswith("article_")
    ]
    assert len(html_items) == 0


def test_build_epub_default_date_is_today(curated_articles, config, tmp_path):
    path = build_epub(curated_articles, BRIEFING, config, output_dir=tmp_path)
    today = datetime.date.today().strftime("%Y-%m-%d")
    assert today in path.name


def test_build_epub_xml_escaping(config, tmp_path):
    from html import escape as _esc
    article = Article(
        id="x1",
        title="Story with <script> & quotes",
        url="http://example.com?a=1&b=2",
        source="Test",
        full_text="Text with <tag> and ampersand",
        category="World",
    )
    path = build_epub([article], "Brief", config, output_dir=tmp_path)
    book = epub.read_epub(str(path))
    html_items = [
        item for item in book.get_items()
        if item.get_type() == 9 and item.get_name().startswith("article_")
    ]
    content = html_items[0].get_content().decode("utf-8")
    # The title should be escaped — no raw <script> tag, but escaped entity present
    escaped_script = _esc("<script>", quote=True)
    assert "<script>" not in content
    assert escaped_script in content
