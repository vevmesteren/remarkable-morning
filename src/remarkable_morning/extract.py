"""Full-text extraction via trafilatura.

Takes an :class:`~remarkable_morning.config.Article` with ``raw_html`` and
populates ``full_text``.  Articles that already have ``full_text`` (Guardian
API) are passed through unchanged.  When extraction from ``raw_html`` yields
too little text, falls back to fetching the article URL.
"""

from __future__ import annotations

import logging
import subprocess
import sys

import trafilatura

from remarkable_morning.config import Article

logger = logging.getLogger(__name__)

# Minimum word count for extracted text to be considered valid.
_MIN_WORDS = 20
# Per-article deadline for fetch_url fallback (seconds).
_FETCH_TIMEOUT = 15

# Module-level reference so tests can patch the fetch function.
_fetch_url = None


def _real_fetch_url(url: str) -> str | None:
    """Call trafilatura.fetch_url in a subprocess with a hard timeout.

    trafilatura.fetch_url is a blocking C call that ignores SIGALRM,
    so we run it in a subprocess that we can kill after _FETCH_TIMEOUT.
    """
    script = (
        "import trafilatura; "
        f"result = trafilatura.fetch_url({url!r}); "
        "import sys; sys.stdout.write(result or '')"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=_FETCH_TIMEOUT,
        )
        return result.stdout if result.stdout else None
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"fetch_url exceeded {_FETCH_TIMEOUT}s deadline")


def extract_text(article: Article) -> Article:
    """Populate ``article.full_text`` from ``article.raw_html``.

    Returns the same article object (mutated in place) so callers can chain.
    Guardian articles (``full_text`` already set) are returned unchanged.
    """
    if article.full_text:
        return article

    if article.raw_html:
        text = _extract(article.raw_html)
        if text and len(text.split()) >= _MIN_WORDS and _is_clean_text(text):
            article.full_text = text
            return article

    # Fallback: download the article page and extract from there.
    if article.url:
        fetcher = _fetch_url or _real_fetch_url
        try:
            downloaded = fetcher(article.url)
            if downloaded:
                text = _extract(downloaded)
                if text and _is_clean_text(text):
                    article.full_text = text
                elif text:
                    logger.warning("Discarding gibberish extraction for %s", article.url)
        except TimeoutError:
            logger.warning("fetch_url timed out after %ds for %s", _FETCH_TIMEOUT, article.url)
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


# ---------------------------------------------------------------------------
# Gibberish detection
# ---------------------------------------------------------------------------

# Common English/German/French bigrams — text with very few of these is likely
# scrambled anti-scraping garbage (e.g. Sifted obfuscates article body text).
_COMMON_BIGRAMS = frozenset([
    # English
    "th", "he", "in", "er", "an", "re", "on", "at", "en", "nd", "ti", "es",
    "or", "te", "of", "ed", "is", "it", "al", "ar", "st", "to", "ng", "se",
    # German
    "ch", "de", "ei", "ie", "ge", "un", "di", "be", "au", "zu", "da", "ic",
    # French
    "le", "ne", "qu", "co", "me", "la", "ce", "ai", "jo", "ur", "tr", "pa",
])

# Minimum ratio of common bigrams for text to be considered clean.
_MIN_CLEAN_RATIO = 0.12
# If any 80-char chunk has < this ratio, the text is partially scrambled.
_MIN_CHUNK_RATIO = 0.10
# Minimum text length to run gibberish detection on (short texts skip the check).
_GIBBERISH_MIN_LEN = 100
# Chunk size for per-segment gibberish detection.
_CHUNK_SIZE = 80


def _is_clean_text(text: str) -> bool:
    """Heuristic check: detect scrambled/obfuscated anti-scraping text.

    Some sites (e.g. Sifted) render the first sentence normally, then
    scramble the rest with character substitution. We check the overall
    ratio of common letter bigrams AND scan each 80-char chunk — if any
    chunk is clearly gibberish (<10% common bigrams), the text is rejected.
    """
    if not text or len(text) < _GIBBERISH_MIN_LEN:
        return True  # Too short to check — assume clean

    # Sample up to 3000 chars to keep it fast
    sample = text[:3000].lower()
    # Extract only alpha characters
    alpha = "".join(c for c in sample if c.isalpha())
    if len(alpha) < _GIBBERISH_MIN_LEN:
        return True  # Mostly non-alpha — probably numbers/punctuation, not gibberish

    # Overall ratio check
    total_bigrams = len(alpha) - 1
    if total_bigrams <= 0:
        return True
    common_count = sum(1 for i in range(total_bigrams) if alpha[i:i+2] in _COMMON_BIGRAMS)
    overall_ratio = common_count / total_bigrams
    if overall_ratio < _MIN_CLEAN_RATIO:
        logger.debug("Gibberish detected (overall ratio=%.3f)", overall_ratio)
        return False

    # Per-chunk check: scan 80-char windows to catch partial scrambling
    # (e.g. clean first sentence + gibberish rest)
    for start in range(0, len(alpha), _CHUNK_SIZE):
        chunk = alpha[start:start + _CHUNK_SIZE]
        if len(chunk) < 40:
            break  # Skip final short chunk
        chunk_bigrams = len(chunk) - 1
        if chunk_bigrams <= 0:
            continue
        chunk_common = sum(1 for i in range(chunk_bigrams) if chunk[i:i+2] in _COMMON_BIGRAMS)
        chunk_ratio = chunk_common / chunk_bigrams
        if chunk_ratio < _MIN_CHUNK_RATIO:
            logger.debug("Gibberish chunk at offset %d (ratio=%.3f)", start, chunk_ratio)
            return False

    return True
