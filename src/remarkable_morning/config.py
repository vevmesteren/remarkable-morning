"""Configuration loading and schema validation.

Also defines the :class:`Article` dataclass — the common data model
shared by every pipeline stage (sources → extract → curate → epub).
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Article — the common data model
# ---------------------------------------------------------------------------

@dataclass
class Article:
    """A single news article flowing through the pipeline.

    Stages populate fields incrementally:

    * **sources** set ``title``, ``url``, ``source``, ``language``,
      ``published``, ``author``, and either ``raw_html`` (RSS) or
      ``full_text`` (Guardian API).
    * **extract** fills ``full_text`` from ``raw_html`` via trafilatura.
    * **curate** fills ``category``, ``also_covered_by``, ``duplicate_of``.
    """

    id: str
    title: str
    url: str
    source: str = ""
    language: str = "en"
    published: str = ""
    author: str = ""
    raw_html: str = ""
    full_text: str = ""
    image_url: str = ""

    # curation output (populated by curate.py)
    category: str | None = None
    also_covered_by: list[str] = field(default_factory=list)
    duplicate_of: str | None = None

    @property
    def first_200_words(self) -> str:
        """First ~200 words of the article text — sent to the LLM for curation.

        Uses ``full_text`` when available (post-extraction); otherwise falls
        back to a stripped version of ``raw_html``.  Article body text is
        never sent to the LLM in full — only this short preview.
        """
        text = self.full_text
        if not text and self.raw_html:
            text = _strip_html(self.raw_html)
        words = text.split()
        return " ".join(words[:200])

    @property
    def is_duplicate(self) -> bool:
        """True when curate marked this article as a duplicate of another."""
        return self.duplicate_of is not None


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------

@dataclass
class SourceConfig:
    """A single source entry from ``feeds.yaml``."""

    name: str
    type: str  # "rss" | "guardian_api"
    feeds: list[str] = field(default_factory=list)
    api_key_env: str = ""
    sections: list[str] = field(default_factory=list)
    limit: int = 10
    language: str = "en"


@dataclass
class CurationConfig:
    model: str = "z-ai/glm-5.2"
    max_articles: int = 25
    max_per_source: int = 5
    categories: list[str] = field(default_factory=list)
    briefing: bool = True
    dedup: bool = True


@dataclass
class Config:
    schedule: str = "05:30"
    remarkable_folder: str = "/News"
    retention_days: int = 7
    primary_language: str = "en"
    sources: list[SourceConfig] = field(default_factory=list)
    curation: CurationConfig = field(
        default_factory=lambda: CurationConfig()
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(raw: str) -> str:
    """Crude HTML tag stripper (for fallback first_200_words)."""
    text = _TAG_RE.sub(" ", raw)
    return html.unescape(text).strip()


def make_article_id(source: str, key: str) -> str:
    """Deterministic 12-char article ID from source name + unique key."""
    raw = f"{source}:{key}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Loader + validator
# ---------------------------------------------------------------------------

_VALID_TYPES = {"rss", "guardian_api"}
_REQUIRED_TOP = ("sources", "curation")


def load_config(path: str | Path) -> Config:
    """Load ``feeds.yaml`` and validate it against the schema."""
    path = Path(path)
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if data is None:
        raise ValueError("Config file is empty")
    return _build_config(data)


def _build_config(data: Any) -> Config:
    if not isinstance(data, dict):
        raise ValueError("Config root must be a YAML mapping")

    for key in _REQUIRED_TOP:
        if key not in data:
            raise ValueError(f"Missing required top-level key: '{key}'")

    default_lang = data.get("primary_language", "en")
    sources: list[SourceConfig] = []
    seen_names: set[str] = set()

    for i, src in enumerate(data["sources"]):
        if not isinstance(src, dict):
            raise ValueError(f"Source #{i} must be a mapping, got {type(src).__name__}")
        if "name" not in src:
            raise ValueError(f"Source #{i} missing 'name'")
        if "type" not in src:
            raise ValueError(f"Source #{i} missing 'type'")
        name = src["name"]
        if name in seen_names:
            raise ValueError(f"Duplicate source name: '{name}'")
        seen_names.add(name)
        stype = src["type"]
        if stype not in _VALID_TYPES:
            raise ValueError(
                f"Source '{name}' has invalid type '{stype}' "
                f"(expected one of: {', '.join(sorted(_VALID_TYPES))})"
            )
        if stype == "rss" and not src.get("feeds"):
            raise ValueError(f"RSS source '{name}' missing or empty 'feeds'")
        if stype == "guardian_api" and not src.get("api_key_env"):
            raise ValueError(f"Guardian source '{name}' missing 'api_key_env'")
        sources.append(
            SourceConfig(
                name=name,
                type=stype,
                feeds=list(src.get("feeds", [])),
                api_key_env=src.get("api_key_env", ""),
                sections=list(src.get("sections", [])),
                limit=int(src.get("limit", 10)),
                language=src.get("language", default_lang),
            )
        )

    cur_raw = data["curation"]
    if not isinstance(cur_raw, dict):
        raise ValueError("'curation' must be a mapping")
    curation = CurationConfig(
        model=cur_raw.get("model", "z-ai/glm-5.2"),
        max_articles=int(cur_raw.get("max_articles", 25)),
        max_per_source=int(cur_raw.get("max_per_source", 5)),
        categories=list(cur_raw.get("categories", [])),
        briefing=bool(cur_raw.get("briefing", True)),
        dedup=bool(cur_raw.get("dedup", True)),
    )

    return Config(
        schedule=data.get("schedule", "05:30"),
        remarkable_folder=data.get("remarkable_folder", "/News"),
        retention_days=int(data.get("retention_days", 7)),
        primary_language=default_lang,
        sources=sources,
        curation=curation,
    )
