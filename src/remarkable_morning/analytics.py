"""Per-run source-level analytics — serialization and file I/O only.

Writes a single JSON file per run date to ``analytics/YYYY-MM-DD.json``
in the current working directory.  One file per date, overwritten on
re-run (idempotent — no append, no rolling array).  The directory is
created if it does not exist.

This module contains NO pipeline logic.  It receives pre-computed
ingested counts, the selected (curated) article list, and the full
article list carrying the final dedup state, then derives the per-source
metrics and serializes them.

JSON schema::

    {
      "date": "2026-11-21",
      "sources": {
        "The Guardian": {
          "ingested": 15,
          "selected": 5,
          "deduped_out": 3,
          "capped_out": 1,
          "overlap_with": {"BBC News": 2, "Der Spiegel": 1}
        },
        ...
      }
    }
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from remarkable_morning.config import Article

logger = logging.getLogger(__name__)

# Subdirectory (relative to the current working directory) where per-run
# analytics files are written.  Gitignored — see .gitignore.
ANALYTICS_DIR = Path("analytics")


def write_analytics(
    date: str,
    ingested_counts: dict[str, int],
    selected: Iterable[Article],
    deduped_articles: Iterable[Article],
) -> Path:
    """Write per-run source analytics to ``analytics/<date>.json``.

    Parameters
    ----------
    date:
        ISO run date (e.g. ``"2026-11-21"``).  Used as the filename and
        embedded in the JSON.
    ingested_counts:
        Per-source article count captured after extraction + textless
        filtering, BEFORE cross-day dedup.  ``{source: count}``.
    selected:
        The curated article list (survivors of dedup + cap).  Used to
        derive per-source ``selected`` counts.
    deduped_articles:
        The full article list carrying the FINAL dedup state after all
        correction passes (``_apply_source_weights`` +
        ``_fallback_dedup`` + ``_enforce_max_per_source``).  Each
        ``Article`` carries ``is_duplicate`` / ``duplicate_of`` for
        overlap drops and ``capped`` for per-source cap drops.

    Returns
    -------
    Path
        The path to the written JSON file.
    """
    payload = _build_payload(date, ingested_counts, list(selected), list(deduped_articles))

    ANALYTICS_DIR.mkdir(parents=True, exist_ok=True)
    path = ANALYTICS_DIR / f"{date}.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False, sort_keys=True)
        fh.write("\n")

    logger.info("Analytics written: %s", path)
    return path


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _build_payload(
    date: str,
    ingested_counts: dict[str, int],
    selected: list[Article],
    deduped_articles: list[Article],
) -> dict:
    """Assemble the JSON-serializable analytics payload."""
    # Collect every source that appears in any input so the report is
    # complete even for sources with zero selected / zero deduped.
    all_sources: set[str] = set()
    all_sources.update(ingested_counts)
    all_sources.update(a.source for a in selected if a.source)
    all_sources.update(a.source for a in deduped_articles if a.source)

    # --- selected: count the curated list per source ---
    selected_counts: dict[str, int] = defaultdict(int)
    for article in selected:
        if article.source:
            selected_counts[article.source] += 1

    # --- deduped_out + overlap_with: derived from final dedup state ---
    # Build id→source lookup so we can resolve duplicate_of to a source.
    source_by_id: dict[str, str] = {}
    for article in deduped_articles:
        source_by_id[article.id] = article.source

    deduped_out: dict[str, int] = defaultdict(int)
    overlap_with: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for article in deduped_articles:
        if not article.is_duplicate or not article.source:
            continue
        deduped_out[article.source] += 1
        dup_id = article.duplicate_of
        if not dup_id:
            continue
        survivor_source = source_by_id.get(dup_id, "")
        if survivor_source and survivor_source != article.source:
            overlap_with[article.source][survivor_source] += 1

    # --- capped_out: articles dropped by max_per_source, not dedup ---
    capped_out: dict[str, int] = defaultdict(int)
    for article in deduped_articles:
        if article.capped and article.source:
            capped_out[article.source] += 1

    sources: dict[str, dict] = {}
    for src in sorted(all_sources):
        sources[src] = {
            "ingested": ingested_counts.get(src, 0),
            "selected": selected_counts.get(src, 0),
            "deduped_out": deduped_out.get(src, 0),
            "capped_out": capped_out.get(src, 0),
            "overlap_with": dict(sorted(overlap_with.get(src, {}).items())),
        }

    return {"date": date, "sources": sources}
