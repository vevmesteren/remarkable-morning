"""Tests for AI curation: prompt building, LLM call, response parsing, dedup."""

import json
from unittest.mock import MagicMock, patch

import pytest

from remarkable_morning.config import Article, CurationConfig
from remarkable_morning.curate import (
    build_prompt,
    call_llm,
    curate,
    parse_response,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def curation_config():
    return CurationConfig(
        model="z-ai/glm-5.2",
        max_articles=25,
        categories=["World", "Business & Fintech", "AI & Tech", "Politics", "Opinion"],
        briefing=True,
        dedup=True,
    )


@pytest.fixture
def sample_articles():
    return [
        Article(
            id="a1",
            title="Major Event Unfolds",
            url="http://guardian.com/1",
            source="The Guardian",
            language="en",
            full_text="Major event happened today in world affairs. " * 10,
        ),
        Article(
            id="a2",
            title="Same Event Reported",
            url="http://bbc.com/1",
            source="BBC News",
            language="en",
            full_text="The same major event was also reported. " * 10,
        ),
        Article(
            id="a3",
            title="Tech Company Launches Product",
            url="http://ars.com/1",
            source="Ars Technica",
            language="en",
            full_text="A tech company launched a new product today. " * 10,
        ),
    ]


MOCK_LLM_RESPONSE = {
    "briefing": "Here's what happened overnight. A major event unfolded. "
    "A tech company launched a product. Markets remained stable.",
    "selected": [
        {
            "id": "a1",
            "category": "World",
            "also_covered_by": ["BBC News"],
            "duplicate_of": None,
        },
        {
            "id": "a2",
            "category": "World",
            "also_covered_by": [],
            "duplicate_of": "a1",
        },
        {
            "id": "a3",
            "category": "AI & Tech",
            "also_covered_by": [],
            "duplicate_of": None,
        },
    ],
}


# ---------------------------------------------------------------------------
# build_prompt
# ---------------------------------------------------------------------------

def test_build_prompt_structure(sample_articles):
    prompt = build_prompt(sample_articles)
    assert "articles" in prompt
    assert len(prompt["articles"]) == 3

    entry = prompt["articles"][0]
    assert entry["id"] == "a1"
    assert entry["title"] == "Major Event Unfolds"
    assert entry["source"] == "The Guardian"
    assert entry["url"] == "http://guardian.com/1"
    assert entry["language"] == "en"
    assert "first_200_words" in entry
    # Body text is NOT in the prompt — only metadata + first_200_words
    assert "Major event happened today in world affairs" not in entry["first_200_words"] or \
        len(entry["first_200_words"].split()) <= 200


def test_build_prompt_limits_to_200_words():
    article = Article(
        id="x1",
        title="T",
        url="http://x",
        full_text=" ".join(f"word{i}" for i in range(500)),
    )
    prompt = build_prompt([article])
    words = prompt["articles"][0]["first_200_words"].split()
    assert len(words) == 200


# ---------------------------------------------------------------------------
# parse_response
# ---------------------------------------------------------------------------

def test_parse_response_plain_json():
    text = json.dumps(MOCK_LLM_RESPONSE)
    result = parse_response(text)
    assert result["briefing"].startswith("Here's what happened")
    assert len(result["selected"]) == 3


def test_parse_response_markdown_code_block():
    text = f"```json\n{json.dumps(MOCK_LLM_RESPONSE)}\n```"
    result = parse_response(text)
    assert result["briefing"].startswith("Here's what happened")
    assert len(result["selected"]) == 3


def test_parse_response_code_block_no_language():
    text = f"```\n{json.dumps(MOCK_LLM_RESPONSE)}\n```"
    result = parse_response(text)
    assert len(result["selected"]) == 3


def test_parse_response_invalid_json():
    result = parse_response("This is not JSON at all.")
    assert result == {"briefing": "", "selected": []}


def test_parse_response_with_surrounding_text():
    text = f"Here is the result:\n```json\n{json.dumps(MOCK_LLM_RESPONSE)}\n```\nDone."
    result = parse_response(text)
    assert result["briefing"].startswith("Here's what happened")


# ---------------------------------------------------------------------------
# call_llm
# ---------------------------------------------------------------------------

def _mock_httpx_response(content):
    resp = MagicMock()
    resp.json.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    resp.raise_for_status.return_value = None
    return resp


def test_call_llm_sends_request(curation_config):
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _mock_httpx_response("response text")

    with patch("remarkable_morning.curate.httpx.post", side_effect=fake_post):
        result = call_llm({"articles": []}, curation_config, "test-key")

    assert result == "response text"
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer test-key"
    assert captured["json"]["model"] == "z-ai/glm-5.2"
    assert captured["json"]["messages"][0]["role"] == "system"
    assert captured["json"]["messages"][1]["role"] == "user"


# ---------------------------------------------------------------------------
# curate — end-to-end with mocked LLM
# ---------------------------------------------------------------------------

def test_curate_returns_briefing_and_selected(sample_articles, curation_config):
    with patch(
        "remarkable_morning.curate.call_llm",
        return_value=json.dumps(MOCK_LLM_RESPONSE),
    ):
        briefing, selected = curate(sample_articles, curation_config, "test-key")

    assert briefing.startswith("Here's what happened")
    # a2 is a duplicate of a1 → excluded
    assert len(selected) == 2
    assert selected[0].id == "a1"
    assert selected[0].category == "World"
    assert selected[1].id == "a3"
    assert selected[1].category == "AI & Tech"


def test_curate_dedup_marks_duplicates(sample_articles, curation_config):
    with patch(
        "remarkable_morning.curate.call_llm",
        return_value=json.dumps(MOCK_LLM_RESPONSE),
    ):
        _, selected = curate(sample_articles, curation_config, "test-key")

    a2 = next(a for a in sample_articles if a.id == "a2")
    assert a2.duplicate_of == "a1"
    assert a2.is_duplicate
    assert a2 not in selected

    a1 = next(a for a in sample_articles if a.id == "a1")
    assert a1.also_covered_by == ["BBC News"]


def test_curate_sorts_by_category_order(sample_articles, curation_config):
    with patch(
        "remarkable_morning.curate.call_llm",
        return_value=json.dumps(MOCK_LLM_RESPONSE),
    ):
        _, selected = curate(sample_articles, curation_config, "test-key")

    # World (index 0) should come before AI & Tech (index 2)
    assert selected[0].category == "World"
    assert selected[1].category == "AI & Tech"


def test_curate_caps_at_max_articles(curation_config):
    articles = [
        Article(id=f"a{i}", title=f"Article {i}", url=f"http://x/{i}", source="S")
        for i in range(50)
    ]
    selected_raw = [
        {"id": f"a{i}", "category": "World", "also_covered_by": [], "duplicate_of": None}
        for i in range(50)
    ]
    mock_resp = {"briefing": "Short.", "selected": selected_raw}
    curation_config.max_articles = 5
    with patch("remarkable_morning.curate.call_llm", return_value=json.dumps(mock_resp)):
        _, selected = curate(articles, curation_config, "test-key")
    assert len(selected) == 5


def test_curate_empty_articles(curation_config):
    briefing, selected = curate([], curation_config, "test-key")
    assert briefing == ""
    assert selected == []


def test_curate_missing_api_key(sample_articles, curation_config):
    with pytest.raises(ValueError, match="API key"):
        curate(sample_articles, curation_config, "")


def test_curate_skips_unknown_ids(sample_articles, curation_config):
    mock_resp = {
        "briefing": "Brief.",
        "selected": [
            {"id": "a1", "category": "World", "also_covered_by": [], "duplicate_of": None},
            {"id": "nonexistent", "category": "World", "also_covered_by": [], "duplicate_of": None},
        ],
    }
    with patch("remarkable_morning.curate.call_llm", return_value=json.dumps(mock_resp)):
        _, selected = curate(sample_articles, curation_config, "test-key")
    assert len(selected) == 1
    assert selected[0].id == "a1"


def test_curate_invalid_llm_response(sample_articles, curation_config):
    with patch("remarkable_morning.curate.call_llm", return_value="not json"):
        briefing, selected = curate(sample_articles, curation_config, "test-key")
    assert briefing == ""
    assert selected == []


# ---------------------------------------------------------------------------
# Fallback dedup and source diversity tests
# ---------------------------------------------------------------------------

def test_fallback_dedup_catches_similar_titles(curation_config):
    """Two articles with very similar titles from different sources should be deduped."""
    articles = [
        Article(id="d1", title="Australia considers criminal penalties for rogue AI use", url="http://a/1", source="BBC News"),
        Article(id="d2", title="Australia considers criminal penalties for rogue AI in Wake", url="http://b/2", source="The Guardian"),
    ]
    mock_resp = {
        "briefing": "Brief.",
        "selected": [
            {"id": "d1", "category": "World", "also_covered_by": [], "duplicate_of": None},
            {"id": "d2", "category": "World", "also_covered_by": [], "duplicate_of": None},
        ],
    }
    with patch("remarkable_morning.curate.call_llm", return_value=json.dumps(mock_resp)):
        _, selected = curate(articles, curation_config, "test-key")
    # One should be dropped as duplicate of the other
    assert len(selected) == 1
    # The kept article should list the other source in also_covered_by
    sources_in_covered = set(selected[0].also_covered_by)
    other_source = "The Guardian" if selected[0].source == "BBC News" else "BBC News"
    assert other_source in sources_in_covered


def test_fallback_dedup_preserves_distinct_titles(curation_config):
    """Articles with genuinely different titles should all be kept."""
    articles = [
        Article(id="e1", title="European Central Bank raises interest rates by quarter point", url="http://a/1", source="FAZ"),
        Article(id="e2", title="Japanese yen hits thirty year low against the dollar", url="http://b/2", source="BBC News"),
    ]
    mock_resp = {
        "briefing": "Brief.",
        "selected": [
            {"id": "e1", "category": "Business & Fintech", "also_covered_by": [], "duplicate_of": None},
            {"id": "e2", "category": "Business & Fintech", "also_covered_by": [], "duplicate_of": None},
        ],
    }
    with patch("remarkable_morning.curate.call_llm", return_value=json.dumps(mock_resp)):
        _, selected = curate(articles, curation_config, "test-key")
    assert len(selected) == 2


def test_max_per_source_enforced(curation_config):
    """No single source should exceed max_per_source."""
    # Use genuinely distinct titles to avoid fallback dedup catching them
    articles = [
        Article(id="m0", title="European Central Bank raises interest rates by quarter point in Frankfurt", url="http://x/0", source="BBC News"),
        Article(id="m1", title="Japanese yen hits thirty year low against the dollar in Tokyo trading", url="http://x/1", source="BBC News"),
        Article(id="m2", title="Brazilian farmers protest new agricultural export tax in Sao Paulo", url="http://x/2", source="BBC News"),
        Article(id="m3", title="Canadian parliament debates housing crisis bill in Ottawa session", url="http://x/3", source="BBC News"),
        Article(id="m4", title="Indian tech sector reports record quarterly growth in Bangalore", url="http://x/4", source="BBC News"),
        Article(id="m5", title="South African mining strike enters third week without resolution", url="http://x/5", source="BBC News"),
        Article(id="m6", title="Australian wildfires force mass evacuations along the coast", url="http://x/6", source="BBC News"),
        Article(id="m7", title="Mexican president signs new trade agreement with European Union", url="http://x/7", source="BBC News"),
        Article(id="m8", title="Russian opposition leader sentenced to additional prison time", url="http://x/8", source="BBC News"),
        Article(id="m9", title="Nigerian oil production drops amid renewed regional conflicts", url="http://x/9", source="BBC News"),
    ]
    mock_resp = {
        "briefing": "Brief.",
        "selected": [
            {"id": f"m{i}", "category": "World", "also_covered_by": [], "duplicate_of": None}
            for i in range(10)
        ],
    }
    curation_config.max_per_source = 3
    curation_config.max_articles = 25
    with patch("remarkable_morning.curate.call_llm", return_value=json.dumps(mock_resp)):
        _, selected = curate(articles, curation_config, "test-key")
    source_counts = {}
    for a in selected:
        source_counts[a.source] = source_counts.get(a.source, 0) + 1
    assert all(c <= 3 for c in source_counts.values()), f"Source counts: {source_counts}"
    assert len(selected) <= 3  # All from BBC, capped at 3
