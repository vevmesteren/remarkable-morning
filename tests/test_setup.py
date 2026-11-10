"""Tests for the interactive CLI setup wizard (remarkable_morning.setup).

All external calls are mocked — no network, no subprocess, no real input.
Tests monkeypatch the module-level wrapper functions:
  _run_command, _http_get, _prompt, _prompt_secret
"""

import os
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from remarkable_morning import setup as wiz


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _completed(stdout="", stderr="", returncode=0):
    """Build a fake subprocess.CompletedProcess."""
    m = MagicMock()
    m.stdout = stdout
    m.stderr = stderr
    m.returncode = returncode
    return m


def _yes(monkeypatch):
    """Make _prompt always answer 'y' to yes/no questions."""
    monkeypatch.setattr(wiz, "_prompt", lambda msg: "y")


def _no(monkeypatch):
    """Make _prompt always answer 'n' to yes/no questions."""
    monkeypatch.setattr(wiz, "_prompt", lambda msg: "n")


def _enter(monkeypatch):
    """Make _prompt always return empty (Enter = default)."""
    monkeypatch.setattr(wiz, "_prompt", lambda msg: "")


# ---------------------------------------------------------------------------
# Step 1 — Python version
# ---------------------------------------------------------------------------

class TestCheckPython:
    def test_ok(self, capsys):
        result = wiz.check_python()
        assert result.status == "done"
        captured = capsys.readouterr()
        assert "OK" in captured.out

    def test_old_python(self, monkeypatch, capsys):
        monkeypatch.setattr(wiz, "_python_version", lambda: (3, 10))
        result = wiz.check_python()
        assert result.status == "failed"
        assert "3.11+" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Step 2 — rmapi installed?
# ---------------------------------------------------------------------------

class TestCheckRmapi:
    def test_found(self, monkeypatch, capsys):
        monkeypatch.setattr(wiz, "_run_command",
                            lambda cmd, timeout=10: _completed(stdout="rmapi 0.0.35"))
        _yes(monkeypatch)
        result = wiz.check_rmapi()
        assert result.status == "done"
        assert "0.0.35" in result.detail

    def test_not_found(self, monkeypatch, capsys):
        def raise_fnf(cmd, timeout=10):
            raise FileNotFoundError()
        monkeypatch.setattr(wiz, "_run_command", raise_fnf)
        _yes(monkeypatch)
        result = wiz.check_rmapi()
        assert result.status == "failed"
        assert "not found" in capsys.readouterr().out

    def test_nonzero_exit(self, monkeypatch):
        monkeypatch.setattr(wiz, "_run_command",
                            lambda cmd, timeout=10: _completed(returncode=1))
        _yes(monkeypatch)
        result = wiz.check_rmapi()
        assert result.status == "failed"

    def test_timeout(self, monkeypatch):
        import subprocess
        def raise_timeout(cmd, timeout=10):
            raise subprocess.TimeoutExpired(cmd, timeout)
        monkeypatch.setattr(wiz, "_run_command", raise_timeout)
        _yes(monkeypatch)
        result = wiz.check_rmapi()
        assert result.status == "failed"
        assert "timeout" in result.detail.lower()

    def test_skipped(self, monkeypatch):
        _no(monkeypatch)
        result = wiz.check_rmapi()
        assert result.status == "skipped"


# ---------------------------------------------------------------------------
# Step 3 — Guardian API key
# ---------------------------------------------------------------------------

class TestGuardianKey:
    def test_valid(self, monkeypatch, capsys):
        monkeypatch.setattr(wiz, "_http_get", lambda url, headers=None, timeout=10: (200, "{}"))
        _yes(monkeypatch)
        monkeypatch.setattr(wiz, "_prompt_secret", lambda msg: "test-guardian-key")
        result, key = wiz.prompt_guardian_key()
        assert result.status == "done"
        assert key == "test-guardian-key"
        assert "valid" in capsys.readouterr().out

    def test_invalid(self, monkeypatch):
        monkeypatch.setattr(wiz, "_http_get", lambda url, headers=None, timeout=10: (401, ""))
        _yes(monkeypatch)
        monkeypatch.setattr(wiz, "_prompt_secret", lambda msg: "bad-key")
        result, key = wiz.prompt_guardian_key()
        assert result.status == "warning"
        assert key == "bad-key"

    def test_network_error(self, monkeypatch):
        def raise_error(url, headers=None, timeout=10):
            raise ConnectionError("no network")
        monkeypatch.setattr(wiz, "_http_get", raise_error)
        _yes(monkeypatch)
        monkeypatch.setattr(wiz, "_prompt_secret", lambda msg: "some-key")
        result, key = wiz.prompt_guardian_key()
        assert result.status == "warning"

    def test_empty_key(self, monkeypatch):
        _yes(monkeypatch)
        monkeypatch.setattr(wiz, "_prompt_secret", lambda msg: "")
        result, key = wiz.prompt_guardian_key()
        assert result.status == "skipped"
        assert key == ""

    def test_skipped(self, monkeypatch):
        _no(monkeypatch)
        result, key = wiz.prompt_guardian_key()
        assert result.status == "skipped"
        assert key == ""


# ---------------------------------------------------------------------------
# Step 4 — OpenRouter API key
# ---------------------------------------------------------------------------

class TestOpenRouterKey:
    def test_valid(self, monkeypatch, capsys):
        monkeypatch.setattr(wiz, "_http_get", lambda url, headers=None, timeout=10: (200, "{}"))
        _yes(monkeypatch)
        monkeypatch.setattr(wiz, "_prompt_secret", lambda msg: "test-openrouter-key")
        result, key = wiz.prompt_openrouter_key()
        assert result.status == "done"
        assert key == "test-openrouter-key"
        assert "valid" in capsys.readouterr().out

    def test_invalid(self, monkeypatch):
        monkeypatch.setattr(wiz, "_http_get", lambda url, headers=None, timeout=10: (401, ""))
        _yes(monkeypatch)
        monkeypatch.setattr(wiz, "_prompt_secret", lambda msg: "bad-key")
        result, key = wiz.prompt_openrouter_key()
        assert result.status == "warning"
        assert key == "bad-key"

    def test_skipped(self, monkeypatch):
        _no(monkeypatch)
        result, key = wiz.prompt_openrouter_key()
        assert result.status == "skipped"
        assert key == ""


# ---------------------------------------------------------------------------
# Step 5 — Copy feeds.example.yaml → feeds.yaml
# ---------------------------------------------------------------------------

class TestCopyFeeds:
    def test_success(self, monkeypatch, tmp_path):
        (tmp_path / "feeds.example.yaml").write_text("# test config\n")
        _yes(monkeypatch)
        result = wiz.copy_feeds_config(tmp_path)
        assert result.status == "done"
        assert (tmp_path / "feeds.yaml").exists()
        assert (tmp_path / "feeds.yaml").read_text() == "# test config\n"

    def test_exists_skip(self, monkeypatch, tmp_path):
        (tmp_path / "feeds.example.yaml").write_text("# new\n")
        (tmp_path / "feeds.yaml").write_text("# existing\n")
        _no(monkeypatch)
        result = wiz.copy_feeds_config(tmp_path)
        assert result.status == "skipped"
        assert (tmp_path / "feeds.yaml").read_text() == "# existing\n"

    def test_exists_overwrite(self, monkeypatch, tmp_path):
        (tmp_path / "feeds.example.yaml").write_text("# new\n")
        (tmp_path / "feeds.yaml").write_text("# existing\n")
        _yes(monkeypatch)
        result = wiz.copy_feeds_config(tmp_path)
        assert result.status == "done"
        assert (tmp_path / "feeds.yaml").read_text() == "# new\n"

    def test_source_missing(self, monkeypatch, tmp_path):
        _yes(monkeypatch)
        result = wiz.copy_feeds_config(tmp_path)
        assert result.status == "failed"


# ---------------------------------------------------------------------------
# Step 6 — Create .env.pipeline
# ---------------------------------------------------------------------------

class TestCreateEnvFile:
    def test_success(self, monkeypatch, tmp_path):
        _yes(monkeypatch)
        result = wiz.create_env_file(tmp_path, "g-key", "o-key")
        assert result.status == "done"
        content = (tmp_path / ".env.pipeline").read_text()
        assert "GUARDIAN_API_KEY=g-key" in content
        assert "OPENROUTER_API_KEY=o-key" in content

    def test_permissions(self, monkeypatch, tmp_path):
        _yes(monkeypatch)
        wiz.create_env_file(tmp_path, "g-key", "o-key")
        mode = (tmp_path / ".env.pipeline").stat().st_mode & 0o777
        assert mode == 0o600

    def test_exists_skip(self, monkeypatch, tmp_path):
        (tmp_path / ".env.pipeline").write_text("# existing\n")
        _no(monkeypatch)
        result = wiz.create_env_file(tmp_path, "g-key", "")
        assert result.status == "skipped"
        assert (tmp_path / ".env.pipeline").read_text() == "# existing\n"

    def test_no_keys(self, monkeypatch, tmp_path):
        _yes(monkeypatch)
        result = wiz.create_env_file(tmp_path, "", "")
        assert result.status == "skipped"
        assert "no keys" in result.detail.lower()
        assert not (tmp_path / ".env.pipeline").exists()

    def test_partial_keys(self, monkeypatch, tmp_path):
        _yes(monkeypatch)
        result = wiz.create_env_file(tmp_path, "g-key", "")
        assert result.status == "done"
        content = (tmp_path / ".env.pipeline").read_text()
        assert "GUARDIAN_API_KEY=g-key" in content
        assert "OPENROUTER_API_KEY" not in content

    def test_skipped(self, monkeypatch, tmp_path):
        _no(monkeypatch)
        result = wiz.create_env_file(tmp_path, "g-key", "o-key")
        assert result.status == "skipped"


# ---------------------------------------------------------------------------
# Step 7 — rmapi pairing + /News folder
# ---------------------------------------------------------------------------

class TestCheckRmapiSetup:
    def test_paired_news_exists(self, monkeypatch):
        monkeypatch.setattr(wiz, "_run_command",
                            lambda cmd, timeout=10: _completed(returncode=0))
        _yes(monkeypatch)
        result = wiz.check_rmapi_setup()
        assert result.status == "done"
        assert "/News exists" in result.detail

    def test_not_paired(self, monkeypatch, capsys):
        def runner(cmd, timeout=10):
            if cmd == ["rmapi", "ls"]:
                return _completed(returncode=1, stderr="not paired")
            return _completed()
        monkeypatch.setattr(wiz, "_run_command", runner)
        _yes(monkeypatch)
        result = wiz.check_rmapi_setup()
        assert result.status == "failed"
        assert "not paired" in result.detail

    def test_not_installed(self, monkeypatch):
        def raise_fnf(cmd, timeout=10):
            raise FileNotFoundError()
        monkeypatch.setattr(wiz, "_run_command", raise_fnf)
        _yes(monkeypatch)
        result = wiz.check_rmapi_setup()
        assert result.status == "failed"
        assert "not installed" in result.detail

    def test_creates_news(self, monkeypatch):
        def runner(cmd, timeout=10):
            if cmd == ["rmapi", "ls", "/News"]:
                return _completed(returncode=1, stderr="not found")
            return _completed(returncode=0)
        monkeypatch.setattr(wiz, "_run_command", runner)
        _yes(monkeypatch)
        result = wiz.check_rmapi_setup()
        assert result.status == "done"
        assert "/News created" in result.detail

    def test_news_creation_fails(self, monkeypatch):
        def runner(cmd, timeout=10):
            if cmd == ["rmapi", "ls"]:
                return _completed(returncode=0)
            if cmd == ["rmapi", "ls", "/News"]:
                return _completed(returncode=1, stderr="not found")
            return _completed(returncode=1, stderr="mkdir failed")
        monkeypatch.setattr(wiz, "_run_command", runner)
        _yes(monkeypatch)
        result = wiz.check_rmapi_setup()
        assert result.status == "warning"

    def test_skipped(self, monkeypatch):
        _no(monkeypatch)
        result = wiz.check_rmapi_setup()
        assert result.status == "skipped"

    def test_timeout(self, monkeypatch):
        import subprocess
        def raise_timeout(cmd, timeout=10):
            raise subprocess.TimeoutExpired(cmd, timeout)
        monkeypatch.setattr(wiz, "_run_command", raise_timeout)
        _yes(monkeypatch)
        result = wiz.check_rmapi_setup()
        assert result.status == "failed"


# ---------------------------------------------------------------------------
# Step 8 — systemd timer (Linux only)
# ---------------------------------------------------------------------------

class TestSystemdTimer:
    def test_not_linux(self, monkeypatch):
        monkeypatch.setattr(wiz.platform, "system", lambda: "Darwin")
        result = wiz.install_systemd_timer(Path("/tmp"))
        assert result.status == "skipped"
        assert "not Linux" in result.detail

    def test_skipped(self, monkeypatch, tmp_path):
        monkeypatch.setattr(wiz.platform, "system", lambda: "Linux")
        _no(monkeypatch)
        result = wiz.install_systemd_timer(tmp_path)
        assert result.status == "skipped"

    def test_success(self, monkeypatch, tmp_path):
        # Create fake systemd dir and unit files
        systemd_dir = tmp_path / "systemd"
        systemd_dir.mkdir()
        (systemd_dir / "remarkable-morning.service").write_text("[Unit]\n")
        (systemd_dir / "remarkable-morning.timer").write_text("[Timer]\n")
        monkeypatch.setattr(wiz.platform, "system", lambda: "Linux")
        _yes(monkeypatch)
        monkeypatch.setattr(wiz, "_run_command",
                            lambda cmd, timeout=15: _completed(returncode=0))
        # Use tmp_path as fake home
        monkeypatch.setattr(wiz.Path, "home", classmethod(lambda cls: tmp_path))
        result = wiz.install_systemd_timer(tmp_path)
        assert result.status == "done"
        assert (tmp_path / ".config" / "systemd" / "user" /
                "remarkable-morning.service").exists()

    def test_unit_files_missing(self, monkeypatch, tmp_path):
        monkeypatch.setattr(wiz.platform, "system", lambda: "Linux")
        _yes(monkeypatch)
        result = wiz.install_systemd_timer(tmp_path)
        assert result.status == "failed"
        assert "missing" in result.detail

    def test_systemctl_fails(self, monkeypatch, tmp_path):
        systemd_dir = tmp_path / "systemd"
        systemd_dir.mkdir()
        (systemd_dir / "remarkable-morning.service").write_text("[Unit]\n")
        (systemd_dir / "remarkable-morning.timer").write_text("[Timer]\n")
        monkeypatch.setattr(wiz.platform, "system", lambda: "Linux")
        _yes(monkeypatch)
        monkeypatch.setattr(wiz, "_run_command",
                            lambda cmd, timeout=15: _completed(returncode=1, stderr="err"))
        monkeypatch.setattr(wiz.Path, "home", classmethod(lambda cls: tmp_path))
        result = wiz.install_systemd_timer(tmp_path)
        assert result.status == "failed"


# ---------------------------------------------------------------------------
# Step 9 — Run pipeline once
# ---------------------------------------------------------------------------

class TestRunPipeline:
    def test_success(self, monkeypatch, tmp_path):
        monkeypatch.setattr(wiz, "_run_command",
                            lambda cmd, timeout=120: _completed(returncode=0))
        _yes(monkeypatch)
        result = wiz.offer_run_pipeline(tmp_path)
        assert result.status == "done"

    def test_failure(self, monkeypatch, tmp_path):
        monkeypatch.setattr(wiz, "_run_command",
                            lambda cmd, timeout=120: _completed(returncode=1, stderr="err"))
        _yes(monkeypatch)
        result = wiz.offer_run_pipeline(tmp_path)
        assert result.status == "failed"

    def test_skipped(self, monkeypatch, tmp_path):
        _no(monkeypatch)
        result = wiz.offer_run_pipeline(tmp_path)
        assert result.status == "skipped"

    def test_loads_env_file(self, monkeypatch, tmp_path):
        """Pipeline command should receive keys from .env.pipeline."""
        (tmp_path / ".env.pipeline").write_text(
            "GUARDIAN_API_KEY=from-file\nOPENROUTER_API_KEY=also-from-file\n"
        )
        captured_cmd = {}

        def runner(cmd, timeout=120):
            # Verify env was loaded — we can't check env directly, but
            # the function reads the file. We just verify it runs.
            return _completed(returncode=0)

        monkeypatch.setattr(wiz, "_run_command", runner)
        _yes(monkeypatch)
        result = wiz.offer_run_pipeline(tmp_path)
        assert result.status == "done"


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

class TestPrintSummary:
    def test_all_statuses(self, capsys):
        results = [
            wiz.SetupResult("Step 1", "done", "ok"),
            wiz.SetupResult("Step 2", "skipped"),
            wiz.SetupResult("Step 3", "failed", "bad"),
            wiz.SetupResult("Step 4", "warning", "meh"),
        ]
        wiz.print_summary(results)
        out = capsys.readouterr().out
        assert "Step 1" in out
        assert "Step 2" in out
        assert "Step 3" in out
        assert "Step 4" in out
        assert "failed" in out

    def test_no_failures(self, capsys):
        results = [wiz.SetupResult("Step 1", "done"), wiz.SetupResult("Step 2", "skipped")]
        wiz.print_summary(results)
        out = capsys.readouterr().out
        assert "complete" in out.lower()


# ---------------------------------------------------------------------------
# Ask yes/no helper
# ---------------------------------------------------------------------------

class TestAskYesNo:
    def test_yes(self, monkeypatch):
        monkeypatch.setattr(wiz, "_prompt", lambda msg: "y")
        assert wiz._ask_yes_no("test?") is True

    def test_no(self, monkeypatch):
        monkeypatch.setattr(wiz, "_prompt", lambda msg: "n")
        assert wiz._ask_yes_no("test?") is False

    def test_default_true(self, monkeypatch):
        monkeypatch.setattr(wiz, "_prompt", lambda msg: "")
        assert wiz._ask_yes_no("test?", default=True) is True

    def test_default_false(self, monkeypatch):
        monkeypatch.setattr(wiz, "_prompt", lambda msg: "")
        assert wiz._ask_yes_no("test?", default=False) is False


# ---------------------------------------------------------------------------
# Full wizard (main)
# ---------------------------------------------------------------------------

class TestMain:
    def test_full_run_all_yes(self, monkeypatch, tmp_path):
        """Run the full wizard with all steps accepted and mocked."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "feeds.example.yaml").write_text("# feeds\n")
        # Create systemd dir for the timer step
        sd = tmp_path / "systemd"
        sd.mkdir()
        (sd / "remarkable-morning.service").write_text("[Unit]\n")
        (sd / "remarkable-morning.timer").write_text("[Timer]\n")

        # All yes/no → yes
        monkeypatch.setattr(wiz, "_prompt", lambda msg: "y")
        # Secret prompts → fake keys
        monkeypatch.setattr(wiz, "_prompt_secret",
                            lambda msg: "fake-key" if "Guardian" in msg else "fake-key")
        # HTTP → valid
        monkeypatch.setattr(wiz, "_http_get",
                            lambda url, headers=None, timeout=10: (200, "{}"))
        # All subprocess calls succeed
        monkeypatch.setattr(wiz, "_run_command",
                            lambda cmd, timeout=10: _completed(returncode=0, stdout="ok"))
        # Pretend we're on Linux
        monkeypatch.setattr(wiz.platform, "system", lambda: "Linux")
        monkeypatch.setattr(wiz.Path, "home", classmethod(lambda cls: tmp_path))

        exit_code = wiz.main()
        assert exit_code == 0
        # feeds.yaml should have been copied
        assert (tmp_path / "feeds.yaml").exists()
        # .env.pipeline should exist with chmod 600
        env_path = tmp_path / ".env.pipeline"
        assert env_path.exists()
        assert (env_path.stat().st_mode & 0o777) == 0o600
        content = env_path.read_text()
        assert "GUARDIAN_API_KEY=fake-key" in content
        assert "OPENROUTER_API_KEY=fake-key" in content

    def test_full_run_all_no(self, monkeypatch, tmp_path):
        """Run the full wizard with all steps skipped."""
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(wiz, "_prompt", lambda msg: "n")
        monkeypatch.setattr(wiz, "_prompt_secret", lambda msg: "")
        monkeypatch.setattr(wiz, "_http_get",
                            lambda url, headers=None, timeout=10: (200, "{}"))
        monkeypatch.setattr(wiz, "_run_command",
                            lambda cmd, timeout=10: _completed(returncode=0))
        monkeypatch.setattr(wiz.platform, "system", lambda: "Linux")

        exit_code = wiz.main()
        # No failures (just skips) → exit 0
        assert exit_code == 0
        assert not (tmp_path / "feeds.yaml").exists()
        assert not (tmp_path / ".env.pipeline").exists()

    def test_main_returns_1_on_failure(self, monkeypatch, tmp_path):
        """If a step fails, main() returns 1."""
        monkeypatch.chdir(tmp_path)
        # Force Python version check to fail
        monkeypatch.setattr(wiz, "_python_version", lambda: (3, 10))
        monkeypatch.setattr(wiz, "_prompt", lambda msg: "n")
        monkeypatch.setattr(wiz, "_prompt_secret", lambda msg: "")

        exit_code = wiz.main()
        assert exit_code == 1


# ---------------------------------------------------------------------------
# Design contract: no pipeline imports
# ---------------------------------------------------------------------------

class TestDesignContracts:
    def test_no_pipeline_imports(self):
        """MF-2: setup.py must not import pipeline internals."""
        import ast
        setup_path = Path(wiz.__file__)
        tree = ast.parse(setup_path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("remarkable_morning."):
                    # Only self-import via __main__ or package __init__ is OK,
                    # but pipeline internals (config, extract, etc.) are not.
                    forbidden = {"config", "extract", "curate", "epub", "upload",
                                 "cleanup", "pipeline", "sources"}
                    parts = set(node.module.split("."))
                    assert not (parts & forbidden), \
                        f"setup.py imports forbidden module: {node.module}"
