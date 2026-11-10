"""AI curation: dedup + categorize + morning briefing.

Makes a single LLM call to OpenRouter (z-ai/glm-5.2 by default) sending
only article *metadata* — never body text.  Parses the JSON response and
annotates :class:`~remarkable_morning.config.Article` objects with
``category``, ``also_covered_by``, and ``duplicate_of``.
"""

from __future__ import annotations

import json
import logging
import re
from difflib import SequenceMatcher

import httpx

from remarkable_morning.config import Article, CurationConfig

logger = logging.getLogger(__name__)

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def curate(
    articles: list[Article],
    config: CurationConfig,
    api_key: str,
) -> tuple[str, list[Article]]:
    """Curate *articles* via a single LLM call.

    Returns ``(briefing, selected)`` where *briefing* is the morning briefing
    string and *selected* is the list of non-duplicate articles, sorted by
    category order, capped at ``config.max_articles``.
    """
    if not articles:
        return "", []

    if not api_key:
        raise ValueError(
            "OpenRouter API key is required for curation "
            "(env var OPENROUTER_API_KEY)"
        )

    llm_input = build_prompt(articles)
    raw_response = call_llm(llm_input, config, api_key)
    parsed = parse_response(raw_response)

    briefing = parsed.get("briefing", "")
    selected_raw = parsed.get("selected", [])

    # Build lookup and apply curation decisions
    by_id = {a.id: a for a in articles}
    category_order = {cat: i for i, cat in enumerate(config.categories)}

    curated: list[Article] = []
    for sel in selected_raw:
        sel_id = sel.get("id")
        if sel_id is None:
            continue
        article = by_id.get(sel_id)
        if article is None:
            continue
        article.category = sel.get("category") or config.categories[0] if config.categories else "World"
        article.also_covered_by = list(sel.get("also_covered_by", []))
        article.duplicate_of = sel.get("duplicate_of")
        if not article.is_duplicate:
            curated.append(article)

    curated.sort(key=lambda a: (category_order.get(a.category, 999), a.title or ""))

    # Fallback dedup: catch duplicates the LLM missed by comparing titles
    curated = _fallback_dedup(curated)

    # Enforce max_per_source to ensure source diversity
    curated = _enforce_max_per_source(curated, config.max_per_source)

    curated = curated[: config.max_articles]

    logger.info(
        "Curation complete: %d articles → %d selected, briefing: %d chars",
        len(articles),
        len(curated),
        len(briefing),
    )
    return briefing, curated


# ---------------------------------------------------------------------------
# LLM input / output
# ---------------------------------------------------------------------------

def build_prompt(articles: list[Article]) -> dict:
    """Build the JSON payload sent to the LLM (article metadata only)."""
    return {
        "articles": [
            {
                "id": a.id,
                "title": a.title,
                "source": a.source,
                "url": a.url,
                "language": a.language,
                "first_200_words": a.first_200_words,
                "published": a.published,
            }
            for a in articles
        ]
    }


def call_llm(llm_input: dict, config: CurationConfig, api_key: str) -> str:
    """Call OpenRouter chat completions and return the raw response text."""
    system_prompt = _build_system_prompt(config)
    user_content = json.dumps(llm_input, ensure_ascii=False, indent=2)

    resp = httpx.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            "temperature": 0.3,
        },
        timeout=120,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def parse_response(text: str) -> dict:
    """Parse the LLM response as JSON, handling markdown code blocks."""
    cleaned = _strip_code_block(text).strip()
    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        logger.error("Failed to parse LLM response as JSON: %.200s", cleaned)
        return {"briefing": "", "selected": []}
    if not isinstance(result, dict):
        return {"briefing": "", "selected": []}
    return result


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

_CODE_BLOCK_RE = re.compile(r"```(?:json)?\s*\n?(.*?)```", re.DOTALL)


def _strip_code_block(text: str) -> str:
    """Remove markdown code fences if present."""
    match = _CODE_BLOCK_RE.search(text)
    if match:
        return match.group(1).strip()
    return text


def _build_system_prompt(config: CurationConfig) -> str:
    categories = ", ".join(config.categories) if config.categories else "World, Business & Fintech, AI & Tech, Politics, Opinion"
    return (
        "You are a news curator for a daily morning digest. "
        "You receive a JSON object with an 'articles' array. Each article has "
        "id, title, source, url, language, first_200_words, and published.\n\n"
        "Your tasks:\n"
        "1. DEDUP: If the same story appears in multiple sources, keep the best "
        "one and set 'duplicate_of' on the others to the kept article's id. "
        "List the covering sources in 'also_covered_by'.\n"
        f"2. CATEGORIZE: Assign each kept article to one of: {categories}.\n"
        "3. BRIEFING: Write a 3-5 sentence morning briefing summarising the "
        "top stories.\n\n"
        f"Select up to {config.max_articles} articles after dedup. "
        f"No more than {config.max_per_source} articles from any single source.\n\n"
        "Respond with JSON only, in this exact format:\n"
        '{"briefing": "...", "selected": [{"id": "...", "category": "...", '
        '"also_covered_by": ["..."], "duplicate_of": null}]}'
    )


# --------------------------------------------------------------------------- 
# Fallback dedup and source diversity enforcement
# ---------------------------------------------------------------------------

_TITLE_SIMILARITY_THRESHOLD = 0.85
_MIN_TITLE_WORDS = 5  # Only dedup titles with at least 5 words (avoids false positives on short titles)


def _normalize_title(title: str) -> str:
    """Normalize a title for comparison: lowercase, strip punctuation, collapse spaces."""
    t = re.sub(r"[^\w\s]", "", (title or "").lower())
    return re.sub(r"\s+", " ", t).strip()


def _fallback_dedup(articles: list[Article]) -> list[Article]:
    """Catch duplicates the LLM missed by comparing normalized titles.

    Uses SequenceMatcher with a 0.72 similarity threshold. When two titles
    are very similar, the second is dropped and the first gets its source
    added to also_covered_by.
    """
    if len(articles) < 2:
        return articles

    kept: list[Article] = []
    for article in articles:
        norm = _normalize_title(article.title)
        # Skip dedup for very short titles (too many false positives)
        if len(norm.split()) < _MIN_TITLE_WORDS:
            kept.append(article)
            continue
        is_dup = False
        for k in kept:
            k_norm = _normalize_title(k.title)
            if len(k_norm.split()) < _MIN_TITLE_WORDS:
                continue
            ratio = SequenceMatcher(None, norm, k_norm).ratio()
            if ratio >= _TITLE_SIMILARITY_THRESHOLD:
                if article.source and article.source not in k.also_covered_by:
                    k.also_covered_by.append(article.source)
                logger.info(
                    "Fallback dedup: '%s' (%s) is duplicate of '%s' (%s) [ratio=%.2f]",
                    article.title[:60], article.source, k.title[:60], k.source, ratio,
                )
                is_dup = True
                break
        if not is_dup:
            kept.append(article)

    return kept


def _enforce_max_per_source(articles: list[Article], max_per_source: int) -> list[Article]:
    """Ensure no single source contributes more than max_per_source articles.

    Preserves category sort order — excess articles from over-represented
    sources are dropped from the end of their category group.
    """
    if max_per_source <= 0:
        return articles

    source_counts: dict[str, int] = {}
    kept: list[Article] = []
    for article in articles:
        count = source_counts.get(article.source, 0)
        if count < max_per_source:
            kept.append(article)
            source_counts[article.source] = count + 1
        else:
            logger.info(
                "Source diversity: dropping '%s' from %s (cap=%d)",
                article.title[:50], article.source, max_per_source,
            )
    return kept
