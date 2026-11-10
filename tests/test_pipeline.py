"""Integration tests for upload, cleanup, and the full pipeline (mocked — no network/rmapi)."""

import datetime
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from remarkable_morning.cleanup import cleanup_old_files
from remarkable_morning.config import Article, Config, CurationConfig, SourceConfig
from remarkable_morning.pipeline import run
from remarkable_morning.upload import upload_epub


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _mock_subprocess(stdout="", stderr="", returncode=0):
    result = MagicMock()
    result.stdout = stdout
    result.stderr = stderr
    result.returncode = returncode
    return result


def _valid_yaml(tmp_path, sources_override=None):
    """Write a minimal feeds.yaml and return the path."""
    sources = sources_override or textwrap.dedent("""\
        sources:
          - name: "BBC News"
            type: rss
            feeds: ["http://example.com/rss"]
            limit: 10
    """)
    yaml_text = textwrap.dedent("""\
        schedule: "05:30"
        remarkable_folder: "/News"
        retention_days: 7
        primary_language: en
    """) + sources + textwrap.dedent("""\
        curation:
          model: "z-ai/glm-5.2"
          max_articles: 25
          categories: [World, AI & Tech]
          briefing: true
          dedup: true
    """)
    path = tmp_path / "feeds.yaml"
    path.write_text(yaml_text)
    return path


def _mock_articles():
    return [
        Article(
            id="a1",
            title="World Event",
            url="http://guardian.com/1",
            source="The Guardian",
            full_text="Full text of the first article. " * 10,
            category="World",
        ),
        Article(
            id="a2",
            title="Tech News",
            url="http://ars.com/1",
            source="Ars Technica",
            full_text="Full text of the second article. " * 10,
            category="AI & Tech",
        ),
    ]


# ---------------------------------------------------------------------------
# Upload tests
# ---------------------------------------------------------------------------

def test_upload_success(tmp_path):
    config = Config(remarkable_folder="/News")
    epub = tmp_path / "test.epub"
    epub.write_bytes(b"fake epub")

    with patch("remarkable_morning.upload.subprocess.run", return_value=_mock_subprocess()):
        assert upload_epub(epub, config) is True


def test_upload_failure(tmp_path):
    config = Config(remarkable_folder="/News")
    epub = tmp_path / "test.epub"
    epub.write_bytes(b"fake epub")

    with patch(
        "remarkable_morning.upload.subprocess.run",
        return_value=_mock_subprocess(returncode=1, stderr="Error: not connected"),
    ):
        assert upload_epub(epub, config) is False


def test_upload_uses_correct_destination(tmp_path):
    config = Config(remarkable_folder="/News")
    epub = tmp_path / "morning-paper-2026-11-10.epub"
    epub.write_text("fake")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _mock_subprocess()

    with patch("remarkable_morning.upload.subprocess.run", side_effect=fake_run):
        upload_epub(epub, config)

    assert captured["cmd"][0] == "rmapi"
    assert captured["cmd"][1] == "put"
    assert captured["cmd"][2] == str(epub)
    assert captured["cmd"][3] == "/News"


# ---------------------------------------------------------------------------
# Cleanup tests
# ---------------------------------------------------------------------------

def test_cleanup_removes_old_files():
    config = Config(remarkable_folder="/News", retention_days=7)
    old_date = (datetime.date.today() - datetime.timedelta(days=10)).strftime("%Y-%m-%d")
    recent_date = datetime.date.today().strftime("%Y-%m-%d")
    ls_output = (
        f"[file]\tmorning-paper-{old_date}.epub\n"
        f"[file]\tmorning-paper-{recent_date}.epub\n"
        f"[dir]\tsomefolder\n"
    )

    with patch(
        "remarkable_morning.cleanup.subprocess.run",
        side_effect=[
            _mock_subprocess(stdout=ls_output),  # rmapi ls
            _mock_subprocess(),                  # rmapi rm (old file)
        ],
    ):
        removed = cleanup_old_files(config)

    assert removed == 1


def test_cleanup_keeps_recent_files():
    config = Config(remarkable_folder="/News", retention_days=7)
    recent_date = datetime.date.today().strftime("%Y-%m-%d")
    ls_output = f"[file]\tmorning-paper-{recent_date}.epub\n"

    with patch(
        "remarkable_morning.cleanup.subprocess.run",
        return_value=_mock_subprocess(stdout=ls_output),
    ):
        removed = cleanup_old_files(config)

    assert removed == 0


def test_cleanup_handles_ls_failure():
    config = Config(remarkable_folder="/News", retention_days=7)

    with patch(
        "remarkable_morning.cleanup.subprocess.run",
        return_value=_mock_subprocess(returncode=1, stderr="connection error"),
    ):
        removed = cleanup_old_files(config)

    assert removed == 0


def test_cleanup_empty_folder():
    config = Config(remarkable_folder="/News", retention_days=7)

    with patch(
        "remarkable_morning.cleanup.subprocess.run",
        return_value=_mock_subprocess(stdout=""),
    ):
        removed = cleanup_old_files(config)

    assert removed == 0


def test_cleanup_uses_full_path_for_rm():
    config = Config(remarkable_folder="/News", retention_days=7)
    old_date = (datetime.date.today() - datetime.timedelta(days=10)).strftime("%Y-%m-%d")
    ls_output = f"[file]\tmorning-paper-{old_date}.epub\n"
    captured_cmds = []

    def fake_run(cmd, **kwargs):
        captured_cmds.append(cmd)
        if cmd[1] == "ls":
            return _mock_subprocess(stdout=ls_output)
        return _mock_subprocess()

    with patch("remarkable_morning.cleanup.subprocess.run", side_effect=fake_run):
        cleanup_old_files(config)

    rm_cmd = captured_cmds[1]
    assert rm_cmd[0] == "rmapi"
    assert rm_cmd[1] == "rm"
    assert rm_cmd[2] == f"/News/morning-paper-{old_date}.epub"


def test_cleanup_respects_retention_days():
    config = Config(remarkable_folder="/News", retention_days=30)
    old_date = (datetime.date.today() - datetime.timedelta(days=10)).strftime("%Y-%m-%d")
    ls_output = f"[file]\tmorning-paper-{old_date}.epub\n"

    with patch(
        "remarkable_morning.cleanup.subprocess.run",
        return_value=_mock_subprocess(stdout=ls_output),
    ):
        removed = cleanup_old_files(config)

    assert removed == 0  # 10 days old, retention is 30 days


# ---------------------------------------------------------------------------
# Full pipeline integration tests
# ---------------------------------------------------------------------------

def test_pipeline_full_run_mocked(tmp_path):
    """End-to-end pipeline with all external calls mocked."""
    articles = _mock_articles()
    feeds = _valid_yaml(tmp_path)
    epub_path = tmp_path / "morning-paper-2026-11-10.epub"
    epub_path.write_text("fake")

    with patch("remarkable_morning.pipeline.rss.fetch_rss", return_value=[articles[0]]), \
         patch("remarkable_morning.pipeline.guardian.fetch_guardian", return_value=[articles[1]]), \
         patch("remarkable_morning.pipeline.extract_all", side_effect=lambda arts: arts), \
         patch("remarkable_morning.pipeline.curate", return_value=("Briefing.", articles)), \
         patch("remarkable_morning.pipeline.build_epub", return_value=epub_path), \
         patch("remarkable_morning.pipeline.upload_epub", return_value=True), \
         patch("remarkable_morning.pipeline.cleanup_old_files", return_value=0), \
         patch.dict("os.environ", {"GUARDIAN_API_KEY": "test-key", "OPENROUTER_API_KEY": "test-key"}):

        result = run(config_path=str(feeds), output_dir=str(tmp_path))

    assert result is not None
    assert result.name == "morning-paper-2026-11-10.epub"


def test_pipeline_no_articles_returns_none(tmp_path):
    """No articles fetched → pipeline returns None."""
    feeds = _valid_yaml(tmp_path)

    with patch("remarkable_morning.pipeline.rss.fetch_rss", return_value=[]), \
         patch("remarkable_morning.pipeline.guardian.fetch_guardian", return_value=[]), \
         patch.dict("os.environ", {"GUARDIAN_API_KEY": "test-key", "OPENROUTER_API_KEY": "test-key"}):

        result = run(config_path=str(feeds), output_dir=str(tmp_path))

    assert result is None


def test_pipeline_continues_on_source_failure(tmp_path):
    """One source failing should not stop the pipeline — others continue."""
    articles = _mock_articles()
    feeds = _valid_yaml(
        tmp_path,
        sources_override=textwrap.dedent("""\
            sources:
              - name: "BBC News"
                type: rss
                feeds: ["http://example.com/rss"]
                limit: 10
              - name: "The Guardian"
                type: guardian_api
                api_key_env: "GUARDIAN_API_KEY"
                sections: [world]
                limit: 15
        """),
    )
    epub_path = tmp_path / "morning-paper-2026-11-10.epub"
    epub_path.write_text("fake")

    with patch("remarkable_morning.pipeline.rss.fetch_rss", return_value=articles), \
         patch(
             "remarkable_morning.pipeline.guardian.fetch_guardian",
             side_effect=Exception("API timeout"),
         ), \
         patch("remarkable_morning.pipeline.extract_all", side_effect=lambda arts: arts), \
         patch("remarkable_morning.pipeline.curate", return_value=("Briefing.", articles)), \
         patch("remarkable_morning.pipeline.build_epub", return_value=epub_path), \
         patch("remarkable_morning.pipeline.upload_epub", return_value=True), \
         patch("remarkable_morning.pipeline.cleanup_old_files", return_value=0), \
         patch.dict("os.environ", {"GUARDIAN_API_KEY": "test-key", "OPENROUTER_API_KEY": "test-key"}):

        result = run(config_path=str(feeds), output_dir=str(tmp_path))

    assert result is not None


def test_pipeline_no_curated_articles_returns_none(tmp_path):
    """Curation selects zero articles → pipeline returns None, no EPUB built."""
    articles = _mock_articles()
    feeds = _valid_yaml(tmp_path)

    with patch("remarkable_morning.pipeline.rss.fetch_rss", return_value=articles), \
         patch("remarkable_morning.pipeline.guardian.fetch_guardian", return_value=[]), \
         patch("remarkable_morning.pipeline.extract_all", side_effect=lambda arts: arts), \
         patch("remarkable_morning.pipeline.curate", return_value=("No news.", [])), \
         patch("remarkable_morning.pipeline.build_epub") as mock_build, \
         patch("remarkable_morning.pipeline.upload_epub") as mock_upload, \
         patch.dict("os.environ", {"GUARDIAN_API_KEY": "test-key", "OPENROUTER_API_KEY": "test-key"}):

        result = run(config_path=str(feeds), output_dir=str(tmp_path))

    assert result is None
    mock_build.assert_not_called()
    mock_upload.assert_not_called()


def test_pipeline_produces_real_epub(tmp_path):
    """Full pipeline with real EPUB assembly (only network/rmapi mocked)."""
    articles = _mock_articles()
    feeds = _valid_yaml(tmp_path)

    with patch("remarkable_morning.pipeline.rss.fetch_rss", return_value=articles), \
         patch("remarkable_morning.pipeline.guardian.fetch_guardian", return_value=[]), \
         patch("remarkable_morning.pipeline.extract_all", side_effect=lambda arts: arts), \
         patch("remarkable_morning.pipeline.curate", return_value=("Briefing.", articles)), \
         patch("remarkable_morning.pipeline.upload_epub", return_value=True), \
         patch("remarkable_morning.pipeline.cleanup_old_files", return_value=0), \
         patch.dict("os.environ", {"GUARDIAN_API_KEY": "test-key", "OPENROUTER_API_KEY": "test-key"}):

        result = run(config_path=str(feeds), output_dir=str(tmp_path))

    assert result is not None
    assert result.exists()
    assert result.suffix == ".epub"
    assert "morning-paper-" in result.name
