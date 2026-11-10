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
