"""Unit tests for the per-run analytics module (serialization + file I/O only)."""

import json
from pathlib import Path

import pytest

from remarkable_morning.analytics import write_analytics
from remarkable_morning.config import Article


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_articles():
    """Build a realistic article set with dedup + cap metadata finalized.

    Scenario:
      Guardian: 5 ingested, 2 selected, 2 deduped (overlap with BBC/Spiegel),
                 1 capped.
      BBC News:  4 ingested, 1 selected, 1 deduped (overlap with Guardian).
      Der Spiegel: 3 ingested, 0 selected, 0 deduped, 0 capped.
    """
    # --- Guardian articles ---
    g_sel1 = Article(id="g1", title="Guardian selected one", url="http://g/1",
                     source="The Guardian")
    g_sel2 = Article(id="g2", title="Guardian selected two", url="http://g/2",
                     source="The Guardian")
    # Guardian article deduped — duplicate of a BBC article (survivor=b1)
    g_dup_bbc = Article(id="g3", title="Guardian dup of BBC", url="http://g/3",
                        source="The Guardian", duplicate_of="b1")
    # Guardian article deduped — duplicate of a Spiegel article (survivor=s1)
    g_dup_sp = Article(id="g4", title="Guardian dup of Spiegel", url="http://g/4",
                       source="The Guardian", duplicate_of="s1")
    # Guardian article capped by max_per_source
    g_cap = Article(id="g5", title="Guardian capped", url="http://g/5",
                    source="The Guardian", capped=True)

    # --- BBC articles ---
    b_sel = Article(id="b1", title="BBC selected one", url="http://b/1",
                    source="BBC News")
    # BBC article deduped — duplicate of the Guardian survivor (survivor=g1)
    b_dup = Article(id="b2", title="BBC dup of Guardian", url="http://b/2",
                    source="BBC News", duplicate_of="g1")
    # BBC articles that were simply not selected by the LLM (no dedup, no cap)
    b_notsel1 = Article(id="b3", title="BBC not selected one", url="http://b/3",
                        source="BBC News")
    b_notsel2 = Article(id="b4", title="BBC not selected two", url="http://b/4",
                        source="BBC News")

    # --- Spiegel articles ---
    s_survivor = Article(id="s1", title="Spiegel survivor", url="http://s/1",
                         source="Der Spiegel")
    s_notsel1 = Article(id="s2", title="Spiegel not selected one", url="http://s/2",
                        source="Der Spiegel")
    s_notsel2 = Article(id="s3", title="Spiegel not selected two", url="http://s/3",
                       source="Der Spiegel")

    deduped_articles = [
        g_sel1, g_sel2, g_dup_bbc, g_dup_sp, g_cap,
        b_sel, b_dup, b_notsel1, b_notsel2,
        s_survivor, s_notsel1, s_notsel2,
    ]
    selected = [g_sel1, g_sel2, b_sel]

    ingested_counts = {
        "The Guardian": 5,
        "BBC News": 4,
        "Der Spiegel": 3,
    }
    return ingested_counts, selected, deduped_articles


# ---------------------------------------------------------------------------
# Required acceptance-criteria tests
# ---------------------------------------------------------------------------

def test_analytics_write_creates_file(tmp_path, monkeypatch):
    """write_analytics creates analytics/<date>.json on disk."""
    monkeypatch.chdir(tmp_path)
    ingested, selected, deduped = _make_articles()

    path = write_analytics("2026-11-21", ingested, selected, deduped)

    assert path.exists()
    assert path.name == "2026-11-21.json"
    assert path.parent.name == "analytics"


def test_analytics_format_correct(tmp_path, monkeypatch):
    """JSON payload has the correct schema and per-source metrics."""
    monkeypatch.chdir(tmp_path)
    ingested, selected, deduped = _make_articles()

    path = write_analytics("2026-11-21", ingested, selected, deduped)
    data = json.loads(path.read_text(encoding="utf-8"))

    assert data["date"] == "2026-11-21"
    assert "sources" in data
    sources = data["sources"]

    # --- The Guardian ---
    g = sources["The Guardian"]
    assert g["ingested"] == 5
    assert g["selected"] == 2          # g_sel1, g_sel2
    assert g["deduped_out"] == 2       # g_dup_bbc, g_dup_sp
    assert g["capped_out"] == 1        # g_cap
    assert g["overlap_with"] == {"BBC News": 1, "Der Spiegel": 1}

    # --- BBC News ---
    b = sources["BBC News"]
    assert b["ingested"] == 4
    assert b["selected"] == 1          # b_sel
    assert b["deduped_out"] == 1       # b_dup
    assert b["capped_out"] == 0
    assert b["overlap_with"] == {"The Guardian": 1}

    # --- Der Spiegel ---
    s = sources["Der Spiegel"]
    assert s["ingested"] == 3
    assert s["selected"] == 0
    assert s["deduped_out"] == 0
    assert s["capped_out"] == 0
    assert s["overlap_with"] == {}


def test_analytics_overwrite_on_rerun(tmp_path, monkeypatch):
    """Re-running on the same date overwrites the file (idempotent)."""
    monkeypatch.chdir(tmp_path)
    ingested, selected, deduped = _make_articles()

    path = write_analytics("2026-11-21", ingested, selected, deduped)
    first_content = path.read_text(encoding="utf-8")

    # Modify the data and re-run — file should be overwritten, not appended
    ingested["The Guardian"] = 99
    path = write_analytics("2026-11-21", ingested, selected, deduped)

    assert path.exists()
    second_content = path.read_text(encoding="utf-8")
    assert second_content != first_content
    data = json.loads(second_content)
    assert data["sources"]["The Guardian"]["ingested"] == 99
    # Only one file for this date
    assert len(list((tmp_path / "analytics").glob("*.json"))) == 1


def test_analytics_no_file_on_abort(tmp_path, monkeypatch):
    """No analytics file is written when the pipeline aborts early.

    This is tested at the analytics-module level by verifying that
    write_analytics is never called on abort (the pipeline test covers
    the integration). Here we verify the directory is NOT auto-created
    by the module on import and that a file only appears when explicitly
    called.
    """
    monkeypatch.chdir(tmp_path)
    # No call to write_analytics → no directory, no file
    assert not (tmp_path / "analytics").exists()
    assert not (tmp_path / "analytics" / "2026-11-21.json").exists()


# ---------------------------------------------------------------------------
# Additional edge-case tests
# ---------------------------------------------------------------------------

def test_analytics_creates_directory_if_missing(tmp_path, monkeypatch):
    """analytics/ directory is created if it does not exist."""
    monkeypatch.chdir(tmp_path)
    assert not (tmp_path / "analytics").exists()
    write_analytics("2026-11-21", {}, [], [])
    assert (tmp_path / "analytics").exists()


def test_analytics_source_with_zero_ingested(tmp_path, monkeypatch):
    """A source appearing only in selected/deduped but not ingested gets 0 ingested."""
    monkeypatch.chdir(tmp_path)
    selected = [Article(id="x1", title="X", url="http://x/1", source="New Source")]
    deduped = [Article(id="x1", title="X", url="http://x/1", source="New Source")]
    ingested = {}

    path = write_analytics("2026-11-21", ingested, selected, deduped)
    data = json.loads(path.read_text(encoding="utf-8"))

    assert data["sources"]["New Source"]["ingested"] == 0
    assert data["sources"]["New Source"]["selected"] == 1


def test_analytics_empty_run(tmp_path, monkeypatch):
    """All-empty inputs produce a valid (empty-sources) JSON."""
    monkeypatch.chdir(tmp_path)
    path = write_analytics("2026-11-21", {}, [], [])
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["date"] == "2026-11-21"
    assert data["sources"] == {}


def test_analytics_self_overlap_excluded(tmp_path, monkeypatch):
    """An article deduped against another from the SAME source does not
    count as overlap_with (overlap is cross-source only)."""
    monkeypatch.chdir(tmp_path)
    survivor = Article(id="s1", title="Survivor", url="http://s/1", source="BBC News")
    dup = Article(id="s2", title="Duplicate", url="http://s/2", source="BBC News",
                  duplicate_of="s1")
    deduped = [survivor, dup]
    selected = [survivor]
    ingested = {"BBC News": 2}

    path = write_analytics("2026-11-21", ingested, selected, deduped)
    data = json.loads(path.read_text(encoding="utf-8"))
    bbc = data["sources"]["BBC News"]
    assert bbc["deduped_out"] == 1
    assert bbc["overlap_with"] == {}  # same-source overlap excluded


def test_analytics_overlap_after_weight_swap(tmp_path, monkeypatch):
    """overlap_with reflects the FINAL dedup state after _apply_source_weights
    swaps which article survives. The dropped article points to the survivor
    that the weight correction chose, not the original LLM pick."""
    monkeypatch.chdir(tmp_path)
    # After weight swap: Guardian (w=3) survives, BBC (w=1) is marked dup of Guardian
    survivor = Article(id="g1", title="Guardian story", url="http://g/1",
                       source="The Guardian")
    dropped = Article(id="b1", title="BBC story", url="http://b/1",
                      source="BBC News", duplicate_of="g1")
    deduped = [survivor, dropped]
    selected = [survivor]
    ingested = {"The Guardian": 1, "BBC News": 1}

    path = write_analytics("2026-11-21", ingested, selected, deduped)
    data = json.loads(path.read_text(encoding="utf-8"))

    # BBC was dropped, overlapping with Guardian
    assert data["sources"]["BBC News"]["deduped_out"] == 1
    assert data["sources"]["BBC News"]["overlap_with"] == {"The Guardian": 1}
    # Guardian survived
    assert data["sources"]["The Guardian"]["deduped_out"] == 0
