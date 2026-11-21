"""Shared pytest fixtures."""

import pytest


@pytest.fixture(autouse=True)
def _isolate_analytics_dir(tmp_path, monkeypatch):
    """Redirect per-run analytics writes to a per-test tmp directory.

    The pipeline's analytics module writes to ``analytics/`` relative to
    the CWD. Without isolation, every integration test that calls
    ``pipeline.run()`` would litter the repo with real analytics files.
    This fixture patches the module-level ``ANALYTICS_DIR`` constant so
    writes land inside ``tmp_path`` instead.
    """
    from remarkable_morning import analytics
    monkeypatch.setattr(analytics, "ANALYTICS_DIR", tmp_path / "analytics")
