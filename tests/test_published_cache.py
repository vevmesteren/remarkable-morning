"""Tests for the persistent cross-day dedup cache (published_cache.py).

Covers the 9 cases required by issue #17:
basic, url_match, title_match, title_fuzzy, lookback_window, lookback_zero,
prune, persistence, pipeline_integration.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from remarkable_morning.config import Article, Config, CurationConfig, SourceConfig
from remarkable_morning.published_cache import PublishedCache, default_cache_path


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

TODAY = "2026-11-14"
EARLIER = "2026-11-12"  # 2 days before TODAY — within a 3-day lookback
OLD = "2026-08-01"      # well outside any lookback


def _article(
    title: str = "Major Event Unfolds in World Affairs Today",
    url: str = "http://example.com/1",
    source: str = "BBC News",
    published: str = TODAY,
) -> Article:
    return Article(
        id="x1",
        title=title,
        url=url,
        source=source,
        published=published,
        full_text="content " * 10,
    )


def _entry(
    url: str = "http://example.com/1",
    title: str = "Major Event Unfolds in World Affairs Today",
    source: str = "BBC News",
    run_date: str = EARLIER,
    published_date: str = "",
) -> dict:
    from remarkable_morning.published_cache import _normalize_title
    return {
        "url": url,
        "normalized_title": _normalize_title(title),
        "source": source,
        "published_date": published_date or run_date,
        "run_date": run_date,
    }


# --------------------------------------------------------------------------- #
# 1. test_published_cache_basic
# --------------------------------------------------------------------------- #

def test_published_cache_basic(tmp_path):
    """Add an article, verify it's detected as previously published,
    and that a different article is not."""
    cache = PublishedCache()
    cache.add_run([_article()], run_date=EARLIER)
    # Same article → detected
    assert cache.is_previously_published(_article(), lookback_days=3, today=TODAY)
    # Different article → not detected
    other = _article(title="Totally Different Story About Another Topic Here", url="http://example.com/2")
    assert not cache.is_previously_published(other, lookback_days=3, today=TODAY)


# --------------------------------------------------------------------------- #
# 2. test_published_cache_url_match
# --------------------------------------------------------------------------- #

def test_published_cache_url_match():
    """Same URL on a different day → filtered (URL exact match)."""
    cache = PublishedCache(entries=[_entry(
        title="Completely Different Headline That Should Not Match Fuzzy",
    )])
    # Different title, same URL → URL match hits.
    article = _article(title="A Radically Different Headline About Another Subject")
    assert cache.is_previously_published(article, lookback_days=3, today=TODAY)


# --------------------------------------------------------------------------- #
# 3. test_published_cache_title_match
# --------------------------------------------------------------------------- #

def test_published_cache_title_match():
    """Different URL, identical normalized title → filtered."""
    cache = PublishedCache(entries=[_entry(url="http://old.example/1")])
    article = _article(url="http://new.example/1")
    assert cache.is_previously_published(article, lookback_days=3, today=TODAY)


# --------------------------------------------------------------------------- #
# 4. test_published_cache_title_fuzzy
# --------------------------------------------------------------------------- #

def test_published_cache_title_fuzzy():
    """Slightly reworded title (similarity ≥ 0.90) → filtered."""
    cached_title = "Australia considers criminal penalties for rogue AI use"
    cache = PublishedCache(entries=[_entry(
        title=cached_title,
        url="http://old.example/australia",
    )])
    # One word swapped — similarity ~0.92, above the 0.90 cross-day threshold.
    incoming = _article(
        title="Australia considers criminal penalties for rogue AI misuse",
        url="http://new.example/australia",
    )
    assert cache.is_previously_published(incoming, lookback_days=3, today=TODAY)


# --------------------------------------------------------------------------- #
# 5. test_published_cache_lookback_window
# --------------------------------------------------------------------------- #

def test_published_cache_lookback_window():
    """Entry older than the lookback window → NOT filtered (passes through)."""
    cache = PublishedCache(entries=[_entry(run_date=OLD)])
    article = _article()
    # 3-day lookback from TODAY — OLD (Aug 1) is way outside.
    assert not cache.is_previously_published(article, lookback_days=3, today=TODAY)
    # 200-day lookback — OLD is inside → filtered.
    assert cache.is_previously_published(article, lookback_days=200, today=TODAY)


# --------------------------------------------------------------------------- #
# 6. test_published_cache_lookback_zero
# --------------------------------------------------------------------------- #

def test_published_cache_lookback_zero():
    """``dedup_lookback_days=0`` → cache disabled, nothing filtered."""
    cache = PublishedCache(entries=[_entry()])
    article = _article()
    assert not cache.is_previously_published(article, lookback_days=0, today=TODAY)


# --------------------------------------------------------------------------- #
# 7. test_published_cache_prune
# --------------------------------------------------------------------------- #

def test_published_cache_prune():
    """Entries older than the prune window are removed."""
    cache = PublishedCache(entries=[
        _entry(run_date=OLD),          # should be pruned
        _entry(run_date=EARLIER),     # should survive (within 90 days of TODAY)
        _entry(run_date=TODAY),        # survives
    ])
    removed = cache.prune(older_than_days=90, today=TODAY)
    assert removed == 1
    assert len(cache.entries) == 2
    assert all(e["run_date"] != OLD for e in cache.entries)


# --------------------------------------------------------------------------- #
# 8. test_published_cache_persistence
# --------------------------------------------------------------------------- #

def test_published_cache_persistence(tmp_path):
    """save → load → verify round-trip preserves data."""
    cache = PublishedCache()
    cache.add_run([_article(), _article(url="http://example.com/2")], run_date=EARLIER)
    path = tmp_path / "published_cache.json"
    cache.save(path)

    loaded = PublishedCache.load(path)
    assert len(loaded.entries) == 2
    assert loaded.entries[0]["url"] == "http://example.com/1"
    assert loaded.entries[1]["url"] == "http://example.com/2"
    # Round-trip preserves run_date
    assert all(e["run_date"] == EARLIER for e in loaded.entries)


def test_published_cache_load_missing_file(tmp_path):
    """Loading a non-existent path returns an empty cache (lazy creation)."""
    path = tmp_path / "does_not_exist.json"
    cache = PublishedCache.load(path)
    assert cache.entries == []


def test_published_cache_load_corrupt_file(tmp_path):
    """A corrupt cache file is tolerated — returns an empty cache."""
    path = tmp_path / "corrupt.json"
    path.write_text("{not valid json")
    cache = PublishedCache.load(path)
    assert cache.entries == []


# --------------------------------------------------------------------------- #
# 9. test_pipeline_integration
# --------------------------------------------------------------------------- #

def test_pipeline_integration(tmp_path, monkeypatch):
    """Mock a cache file, run the pipeline, verify previously-published
    articles are filtered before curation."""

    # Write a feeds.yaml with dedup_lookback_days=3.
    import textwrap
    feeds = tmp_path / "feeds.yaml"
    feeds.write_text(textwrap.dedent(f"""\
        schedule: "05:30"
        remarkable_folder: "/News"
        retention_days: 7
        primary_language: en
        sources:
          - name: "BBC News"
            type: rss
            feeds: ["http://example.com/rss"]
            limit: 10
        curation:
          model: "z-ai/glm-5.2"
          max_articles: 25
          categories: [World, AI & Tech]
          briefing: true
          dedup: true
          dedup_lookback_days: 3
    """))

    # Pre-populate a cache with one article URL (a1) — should be filtered.
    cache_path = tmp_path / "published_cache.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_payload = {
        "version": 1,
        "entries": [
            {
                "url": "http://guardian.com/1",
                "normalized_title": "world event happens",
                "source": "The Guardian",
                "published_date": EARLIER,
                "run_date": EARLIER,
            }
        ],
    }
    cache_path.write_text(json.dumps(cache_payload))

    articles = [
        Article(id="a1", title="World Event Happens", url="http://guardian.com/1",
                source="The Guardian", full_text="content " * 10),
        Article(id="a2", title="Tech News Today", url="http://ars.com/1",
                source="Ars Technica", full_text="content " * 10),
    ]
    epub_path = tmp_path / "morning-paper-2026-11-10.epub"
    epub_path.write_text("fake")

    # Point the cache default path at our temp file.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    # The cache loader resolves ~/.local/share/remarkable-morning/published_cache.json
    # but we placed it directly in tmp_path. Override the loader via patch.
    from remarkable_morning import pipeline as pl

    captured_articles_seen_by_curate = {}

    def fake_curate(arts, cfg, key):
        captured_articles_seen_by_curate["ids"] = [a.id for a in arts]
        return "Briefing.", arts

    with patch("remarkable_morning.pipeline.rss.fetch_rss", return_value=articles), \
         patch("remarkable_morning.pipeline.guardian.fetch_guardian", return_value=[]), \
         patch("remarkable_morning.pipeline.extract_all", side_effect=lambda arts: arts), \
         patch("remarkable_morning.pipeline.curate", side_effect=fake_curate), \
         patch("remarkable_morning.pipeline.build_epub", return_value=epub_path), \
         patch("remarkable_morning.pipeline.upload_epub", return_value=True), \
         patch("remarkable_morning.pipeline.cleanup_old_files", return_value=0), \
         patch("remarkable_morning.published_cache.default_cache_path", return_value=cache_path), \
         patch.dict("os.environ", {"GUARDIAN_API_KEY": "k", "OPENROUTER_API_KEY": "k"}), \
         patch("remarkable_morning.pipeline._today", return_value=TODAY):

        result = pl.run(config_path=str(feeds), output_dir=str(tmp_path))

    assert result is not None
    # a1 was previously published → must NOT reach curation.
    seen = captured_articles_seen_by_curate["ids"]
    assert "a1" not in seen, f"a1 should have been filtered by cross-day dedup, got {seen}"
    assert "a2" in seen, f"a2 should have passed through, got {seen}"
    # The cache should have been updated with a2 (the selected article).
    saved = json.loads(cache_path.read_text())
    urls = [e["url"] for e in saved["entries"]]
    assert "http://ars.com/1" in urls
    # The old entry for a1 should still be there (within lookback).
    assert "http://guardian.com/1" in urls


def test_pipeline_lookback_zero_skips_cache(tmp_path):
    """With dedup_lookback_days=0 the pipeline never loads/updates the cache."""
    import textwrap
    feeds = tmp_path / "feeds.yaml"
    feeds.write_text(textwrap.dedent("""\
        schedule: "05:30"
        remarkable_folder: "/News"
        retention_days: 7
        primary_language: en
        sources:
          - name: "BBC News"
            type: rss
            feeds: ["http://example.com/rss"]
            limit: 10
        curation:
          model: "z-ai/glm-5.2"
          max_articles: 25
          categories: [World]
          briefing: true
          dedup: true
          dedup_lookback_days: 0
    """))

    cache_path = tmp_path / "published_cache.json"
    articles = [
        Article(id="a1", title="World Event Happens", url="http://guardian.com/1",
                source="The Guardian", full_text="content " * 10),
    ]
    epub_path = tmp_path / "morning-paper-2026-11-10.epub"
    epub_path.write_text("fake")

    from remarkable_morning import pipeline as pl

    save_called = []

    def fake_save(self, path=None):
        save_called.append(path)

    with patch("remarkable_morning.pipeline.rss.fetch_rss", return_value=articles), \
         patch("remarkable_morning.pipeline.guardian.fetch_guardian", return_value=[]), \
         patch("remarkable_morning.pipeline.extract_all", side_effect=lambda arts: arts), \
         patch("remarkable_morning.pipeline.curate", return_value=("Briefing.", articles)), \
         patch("remarkable_morning.pipeline.build_epub", return_value=epub_path), \
         patch("remarkable_morning.pipeline.upload_epub", return_value=True), \
         patch("remarkable_morning.pipeline.cleanup_old_files", return_value=0), \
         patch("remarkable_morning.published_cache.PublishedCache.save", new=fake_save), \
         patch.dict("os.environ", {"GUARDIAN_API_KEY": "k", "OPENROUTER_API_KEY": "k"}), \
         patch("remarkable_morning.pipeline._today", return_value=TODAY):

        result = pl.run(config_path=str(feeds), output_dir=str(tmp_path))

    assert result is not None
    # With lookback=0 the cache is loaded but never filtered/saved.
    assert save_called == [], f"cache.save should not be called when lookback=0, got {save_called}"
