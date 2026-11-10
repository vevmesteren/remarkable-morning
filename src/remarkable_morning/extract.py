"""Full-text extraction via trafilatura.

Takes an :class:`~remarkable_morning.config.Article` with ``raw_html`` and
populates ``full_text``.  Articles that already have ``full_text`` (Guardian
API) are passed through unchanged.  When extraction from ``raw_html`` yields
too little text, falls back to fetching the article URL.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

import trafilatura

from remarkable_morning.config import Article

logger = logging.getLogger(__name__)

# Minimum word count for extracted text to be considered valid.
_MIN_WORDS = 20
# Per-article deadline for fetch_url fallback (seconds).
_FETCH_TIMEOUT = 15


def extract_text(article: Article) -> Article:
    """Populate ``article.full_text`` from ``article.raw_html``.

    Returns the same article object (mutated in place) so callers can chain.
    Guardian articles (``full_text`` already set) are returned unchanged.
    """
    if article.full_text:
        return article

    if article.raw_html:
        text = _extract(article.raw_html)
        if text and len(text.split()) >= _MIN_WORDS:
            article.full_text = text
            return article

    # Fallback: download the article page and extract from there.
    # trafilatura.fetch_url has no built-in timeout, so we wrap it in a
    # thread with a hard deadline to prevent the pipeline from hanging.
    if article.url:
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(trafilatura.fetch_url, article.url)
                try:
                    downloaded = future.result(timeout=_FETCH_TIMEOUT)
                except FuturesTimeout:
                    logger.warning("fetch_url timed out after %ds for %s", _FETCH_TIMEOUT, article.url)
                    return article
            if downloaded:
                text = _extract(downloaded)
                if text:
                    article.full_text = text
        except Exception as exc:  # noqa: BLE001 — network errors are non-fatal
            logger.warning("fetch_url fallback failed for %s: %s", article.url, exc)

    return article


def extract_all(articles: list[Article]) -> list[Article]:
    """Extract full text for every article in *articles* (in order)."""
    return [extract_text(a) for a in articles]


def _extract(html_text: str) -> str | None:
    """Run trafilatura extraction with consistent settings."""
    return trafilatura.extract(
        html_text,
        include_links=False,
        include_tables=False,
        include_images=False,
        favor_recall=True,
    )
