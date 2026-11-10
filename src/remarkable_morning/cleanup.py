"""Remove old EPUB files from the reMarkable via ``rmapi``.

Lists files in the configured folder, parses dates from ``morning-paper-YYYY-MM-DD.epub``
filenames, and trashes any file older than ``retention_days``.
"""

from __future__ import annotations

import datetime
import logging
import re
import subprocess
from pathlib import Path

from remarkable_morning.config import Config

logger = logging.getLogger(__name__)

# Matches ``morning-paper-2026-11-10.epub`` in rmapi ls output lines.
_DATE_RE = re.compile(r"morning-paper-(\d{4}-\d{2}-\d{2})\.epub")


def cleanup_old_files(config: Config) -> int:
    """Remove EPUB files older than ``retention_days`` from the reMarkable.

    Returns the number of files removed.
    """
    folder = config.remarkable_folder
    retention = config.retention_days

    logger.info("Cleaning files older than %d days in %s", retention, folder)

    result = subprocess.run(
        ["rmapi", "ls", folder],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        logger.error("rmapi ls failed (exit %d): %s", result.returncode, result.stderr.strip())
        return 0

    cutoff = datetime.date.today() - datetime.timedelta(days=retention)
    removed = 0

    for line in result.stdout.splitlines():
        match = _DATE_RE.search(line)
        if not match:
            continue
        file_date = datetime.date.fromisoformat(match.group(1))
        if file_date >= cutoff:
            continue  # Still within retention window

        # Full path for rmapi rm
        filename = f"{folder}/{match.group(0)}"

        logger.info("Removing old file: %s (dated %s)", filename, file_date)
        rm_result = subprocess.run(
            ["rmapi", "rm", filename],
            capture_output=True,
            text=True,
        )
        if rm_result.returncode == 0:
            removed += 1
        else:
            logger.warning("rmapi rm failed for %s: %s", filename, rm_result.stderr.strip())

    logger.info("Cleanup complete: %d file(s) removed", removed)
    return removed