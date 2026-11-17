"""Persistent cross-day dedup cache.

Stores URLs and normalized titles of articles published in each daily
edition.  Before curation, the pipeline loads the cache and filters out
articles that were already published within the lookback window.

Cache file location: ``~/.local/share/remarkable-morning/published_cache.json``
(XDG data dir, outside the repo).

This complements — does NOT replace — the existing intra-run dedup in
:mod:`remarkable_morning.curate`.  It runs as a pre-curation gate so the
LLM never sees previously published articles.
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Constants — kept slightly stricter than intra-run dedup to reduce
# false positives across days (where titles from different sources can
# diverge more).
# --------------------------------------------------------------------------- #

CACHE_VERSION = 1
_CROSS_DAY_TITLE_SIMILARITY_THRESHOLD = 0.90
_MIN_TITLE_WORDS = 5  # Only fuzzy-match titles with ≥ 5 words.
_PRUNE_DEFAULT_DAYS = 90  # Cap unbounded growth.


# --------------------------------------------------------------------------- #
# Path resolution
# --------------------------------------------------------------------------- #

def default_cache_path() -> Path:
    """Return the default cache path under the XDG data dir.

    Respects ``XDG_DATA_HOME`` when set; otherwise falls back to
    ``~/.local/share``.  The cache lives outside the repo so it survives
    branch switches and is never committed.
    """
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "remarkable-morning" / "published_cache.json"


# --------------------------------------------------------------------------- #
# Title normalization — reuses the exact algorithm from curate.py.
# We reimplement locally rather than import from curate to avoid pulling
# the heavy curate module (which imports httpx) into the cache path.
# Keep these in sync if curate._normalize_title ever changes.
# --------------------------------------------------------------------------- #

def _normalize_title(title: str) -> str:
    """Normalize a title for comparison.

    Lowercase, strip punctuation, collapse whitespace.  Identical to
    ``curate._normalize_title`` — kept in sync deliberately.
    """
    t = re.sub(r"[^\w\s]", "", (title or "").lower())
    return re.sub(r"\s+", " ", t).strip()


# --------------------------------------------------------------------------- #
# PublishedCache
# --------------------------------------------------------------------------- #

class PublishedCache:
    """In-memory representation of the published-article cache.

    Load with :meth:`load`, query with :meth:`is_previously_published`,
    record a run with :meth:`add_run`, then persist with :meth:`save`.
    Call :meth:`prune` before saving to bound growth.
    """

    def __init__(self, entries: list[dict[str, Any]] | None = None) -> None:
        self.entries: list[dict[str, Any]] = list(entries or [])

    # ----- loading / saving --------------------------------------------- #

    @classmethod
    def load(cls, path: str | Path | None = None) -> "PublishedCache":
        """Load the cache from *path* (default: :func:`default_cache_path`).

        If the file does not exist, returns an empty cache (lazy creation:
        the first run with no cache performs no filtering).
        """
        path = Path(path) if path is not None else default_cache_path()
        if not path.exists():
            logger.debug("Published cache not found at %s — starting empty", path)
            return cls()
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to read published cache at %s (%s) — starting empty", path, exc)
            return cls()
        entries = data.get("entries", []) if isinstance(data, dict) else []
        if not isinstance(entries, list):
            entries = []
        return cls(entries=entries)

    def save(self, path: str | Path | None = None) -> None:
        """Persist the cache to *path* (default: :func:`default_cache_path`).

        Creates parent directories as needed.  Atomic write via temp file
        so a crash mid-write never leaves a half-written cache.
        """
        path = Path(path) if path is not None else default_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": CACHE_VERSION, "entries": self.entries}
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        tmp.replace(path)
        logger.debug("Published cache saved: %d entries → %s", len(self.entries), path)

    # ----- querying ------------------------------------------------------ #

    def is_previously_published(
        self,
        article: Any,
        lookback_days: int,
        today: str | None = None,
    ) -> bool:
        """Return True if *article* was published in a previous run.

        Two matching strategies, both checked:

        1. **URL exact match** — the article URL appears in a cache entry
           whose ``run_date`` is within the lookback window.
        2. **Normalized title fuzzy match** — ``SequenceMatcher`` ratio
           ≥ 0.90 against a cached entry's ``normalized_title``, for
           titles with ≥ 5 words.  Only entries within the lookback
           window are considered.

        ``lookback_days=0`` disables the cache entirely (always False).
        ``today`` defaults to :func:`datetime.date.today` and is a
        parameter for testability.
        """
        if lookback_days <= 0:
            return False

        today_date = _parse_date(today) if today else datetime.date.today()
        cutoff = today_date - datetime.timedelta(days=lookback_days)

        url = getattr(article, "url", "") or ""
        title = getattr(article, "title", "") or ""
        norm = _normalize_title(title)
        word_count = len(norm.split())

        for entry in self.entries:
            run_date = _parse_date(entry.get("run_date", ""))
            if run_date < cutoff:
                continue

            # Strategy 1: URL exact match.
            if url and entry.get("url") == url:
                logger.debug("Cross-day dedup hit (URL): %s", url)
                return True

            # Strategy 2: normalized title fuzzy match.
            if word_count >= _MIN_TITLE_WORDS:
                cached_norm = entry.get("normalized_title", "")
                if not cached_norm or len(cached_norm.split()) < _MIN_TITLE_WORDS:
                    continue
                ratio = SequenceMatcher(None, norm, cached_norm).ratio()
                if ratio >= _CROSS_DAY_TITLE_SIMILARITY_THRESHOLD:
                    logger.debug(
                        "Cross-day dedup hit (title, ratio=%.2f): %r ≈ %r",
                        ratio, title[:60], cached_norm[:60],
                    )
                    return True

        return False

    # ----- mutation ------------------------------------------------------ #

    def add_run(self, articles: Iterable[Any], run_date: str | None = None) -> None:
        """Append one entry per article in *articles* for *run_date*.

        ``run_date`` defaults to today's ISO date.  Each entry records
        ``url``, ``normalized_title``, ``source``, ``published_date`` and
        ``run_date``.  Articles missing a URL are skipped (no stable key).
        """
        date = run_date or str(datetime.date.today())
        added = 0
        for article in articles:
            url = getattr(article, "url", "") or ""
            if not url:
                continue
            title = getattr(article, "title", "") or ""
            self.entries.append({
                "url": url,
                "normalized_title": _normalize_title(title),
                "source": getattr(article, "source", "") or "",
                "published_date": getattr(article, "published", "") or date,
                "run_date": date,
            })
            added += 1
        logger.debug("Published cache: added %d entries for run %s", added, date)

    def prune(self, older_than_days: int = _PRUNE_DEFAULT_DAYS, today: str | None = None) -> int:
        """Drop entries whose ``run_date`` is older than *older_than_days*.

        Returns the number of entries removed.  Bound growth at 90 days
        by default.
        """
        today_date = _parse_date(today) if today else datetime.date.today()
        cutoff = today_date - datetime.timedelta(days=older_than_days)
        before = len(self.entries)
        self.entries = [
            e for e in self.entries
            if _parse_date(e.get("run_date", "")) >= cutoff
        ]
        removed = before - len(self.entries)
        if removed:
            logger.debug("Published cache: pruned %d entries older than %d days", removed, older_than_days)
        return removed


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def _parse_date(value: str) -> datetime.date:
    """Parse an ISO date string (``YYYY-MM-DD``) defensively.

    Falls back to ``datetime.date.min`` (which will always be pruned)
    for malformed values so a single bad entry never crashes the cache.
    """
    if not value:
        return datetime.date.min
    s = value.strip()
    # Tolerate full ISO datetimes by taking the date prefix.
    if "T" in s:
        s = s.split("T", 1)[0]
    if not _DATE_RE.match(s):
        return datetime.date.min
    try:
        return datetime.date.fromisoformat(s)
    except ValueError:
        return datetime.date.min
