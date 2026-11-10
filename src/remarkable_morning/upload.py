"""Upload EPUB to reMarkable Paper Pro via ``rmapi``.

Calls ``rmapi put <file> <destination>`` as a subprocess.
``rmapi`` is a Go binary installed separately (not a Python dep).
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from remarkable_morning.config import Config

logger = logging.getLogger(__name__)


def upload_epub(epub_path: str | Path, config: Config) -> bool:
    """Upload *epub_path* to the reMarkable ``/News`` folder via rmapi.

    Returns ``True`` on success, ``False`` on failure.
    """
    epub_path = Path(epub_path)
    dest = f"{config.remarkable_folder}/{epub_path.name}"

    logger.info("Uploading %s → %s", epub_path, dest)

    result = subprocess.run(
        ["rmapi", "put", str(epub_path), dest],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        logger.error(
            "rmapi put failed (exit %d): %s",
            result.returncode,
            result.stderr.strip(),
        )
        return False

    logger.info("Upload complete: %s", dest)
    return True