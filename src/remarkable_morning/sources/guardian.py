"""Guardian Content API client (httpx).

Fetches articles by section using the Guardian Open Platform API.
The API returns full article text via ``show-fields=bodyText``, so
``Article.full_text`` is populated directly and ``raw_html`` is left empty
(no trafilatura extraction needed for Guardian articles).
"""

from __future__ import annotations

import httpx

from remarkable_morning.config import Article, SourceConfig, make_article_id

GUARDIAN_API_URL = "https://content.guardianapis.com/search"


def fetch_guardian(source: SourceConfig, api_key: str) -> list[Article]:
    """Fetch Guardian articles across *source.sections*.

    Respects ``source.limit`` as the total cap.  The per-section page size
    is distributed evenly across the configured sections.
    """
    if not api_key:
        raise ValueError(
            f"Guardian source '{source.name}' requires an API key "
            f"(env var {source.api_key_env})"
        )
    if not source.sections:
        sections = [None]
    else:
        sections = source.sections

    per_section = (
        max(1, source.limit // len(source.sections))
        if source.sections
        else source.limit
    )

    articles: list[Article] = []
    for section in sections:
        params: dict[str, str | int] = {
            "api-key": api_key,
            "show-fields": "bodyText,byline",
            "page-size": per_section,
            "order-by": "newest",
        }
        if section:
            params["section"] = section

        resp = httpx.get(GUARDIAN_API_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        results = data.get("response", {}).get("results", [])
        for item in results:
            if len(articles) >= source.limit:
                break
            fields = item.get("fields", {})
            api_id = item.get("id", "")
            articles.append(
                Article(
                    id=make_article_id("guardian", api_id),
                    title=item.get("webTitle", ""),
                    url=item.get("webUrl", ""),
                    source=source.name,
                    language="en",
                    published=item.get("webPublicationDate", ""),
                    author=fields.get("byline", ""),
                    raw_html="",
                    full_text=fields.get("bodyText", ""),
                )
            )
        if len(articles) >= source.limit:
            break
    return articles[: source.limit]
