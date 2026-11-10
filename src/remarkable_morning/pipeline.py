"""Pipeline orchestrator — ties all stages together.

Run order:

1. Load config (``feeds.yaml``)
2. Fetch all sources (RSS via feedparser, Guardian via httpx)
3. Extract full text (trafilatura)
4. Curate via LLM (dedup + categorize + briefing)
5. Assemble EPUB (ebooklib)
6. Upload to reMarkable (rmapi put)
7. Cleanup old files (rmapi rm)

Each stage is logged with timing.  The pipeline is designed to be
resilient — failed sources are logged and skipped, not fatal.
"""

from __future__ import annotations

import logging
import os
import sys
import time
from pathlib import Path

from remarkable_morning.cleanup import cleanup_old_files
from remarkable_morning.config import Config, load_config
from remarkable_morning.curate import curate
from remarkable_morning.epub import build_epub
from remarkable_morning.extract import extract_all
from remarkable_morning.sources import guardian, rss
from remarkable_morning.upload import upload_epub

logger = logging.getLogger(__name__)


def run(
    config_path: str | Path = "feeds.yaml",
    output_dir: str | Path = ".",
) -> Path | None:
    """Run the full Remarkable Morning pipeline.

    Returns the path to the generated EPUB, or ``None`` if no articles
    were selected after curation.
    """
    # --- 1. Load config ---
    config = load_config(config_path)
    start = time.time()
    logger.info("=== Remarkable Morning — %s ===", _today())

    guardian_key = os.environ.get("GUARDIAN_API_KEY", "")
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")

    # --- 2. Fetch all sources ---
    t0 = time.time()
    articles = _fetch_all_sources(config, guardian_key)
    logger.info("Fetched %d articles in %.1fs", len(articles), time.time() - t0)

    if not articles:
        logger.warning("No articles fetched — aborting pipeline")
        return None

    # --- 3. Extract full text ---
    t0 = time.time()
    articles = extract_all(articles)
    extracted = sum(1 for a in articles if a.full_text)
    logger.info(
        "Extracted text for %d/%d articles in %.1fs",
        extracted,
        len(articles),
        time.time() - t0,
    )

    # --- 4. Curate via LLM ---
    t0 = time.time()
    briefing, selected = curate(articles, config.curation, openrouter_key)
    logger.info("Curated %d articles in %.1fs", len(selected), time.time() - t0)

    if not selected:
        logger.warning("No articles selected after curation — skipping EPUB")
        return None

    # --- 5. Assemble EPUB ---
    t0 = time.time()
    epub_path = build_epub(selected, briefing, config, output_dir=output_dir)
    logger.info("EPUB assembled: %s in %.1fs", epub_path, time.time() - t0)

    # --- 6. Upload ---
    t0 = time.time()
    uploaded = upload_epub(epub_path, config)
    logger.info("Upload %s in %.1fs", "succeeded" if uploaded else "failed", time.time() - t0)

    # --- 7. Cleanup ---
    t0 = time.time()
    removed = cleanup_old_files(config)
    logger.info("Cleaned up %d old file(s) in %.1fs", removed, time.time() - t0)

    logger.info("=== Pipeline complete in %.1fs ===", time.time() - start)
    return epub_path


def _fetch_all_sources(config: Config, guardian_key: str) -> list:
    """Fetch articles from all configured sources.

    Failed sources are logged and skipped — one broken feed does not
    stop the pipeline.
    """
    from remarkable_morning.config import Article

    all_articles: list[Article] = []

    for source in config.sources:
        try:
            if source.type == "rss":
                articles = rss.fetch_rss(source)
            elif source.type == "guardian_api":
                articles = guardian.fetch_guardian(source, guardian_key)
            else:
                logger.warning(
                    "Unknown source type '%s' for '%s' — skipping",
                    source.type,
                    source.name,
                )
                continue
            logger.info("  %s: %d articles", source.name, len(articles))
            all_articles.extend(articles)
        except Exception as exc:  # noqa: BLE001
            logger.error("  %s: FAILED — %s", source.name, exc)

    return all_articles


def _today() -> str:
    """Today's date as ISO string (isolated for testability)."""
    import datetime

    return str(datetime.date.today())


def main() -> int:
    """CLI entry point — runs the pipeline and exits 0 on success."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    config_path = sys.argv[1] if len(sys.argv) > 1 else "feeds.yaml"
    output_dir = sys.argv[2] if len(sys.argv) > 2 else "."
    try:
        run(config_path, output_dir)
    except Exception as exc:
        logger.error("Pipeline failed: %s", exc, exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
