"""On-disk output of generated charts (used when output.mode = file).

Charts are written as ``<request_id>.png`` and the output directory is pruned
by count and age after every write so it cannot grow without bound.
"""

import logging
import re
import time
from pathlib import Path

from src import config

logger = logging.getLogger(__name__)

# request_id comes from the bot; sanitize it before using it as a filename to
# avoid path traversal and invalid characters.
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _safe_name(request_id: str) -> str:
    name = _UNSAFE.sub("_", request_id).strip("._")
    return (name or "chart")[:64]


def save_chart(image_bytes: bytes, request_id: str) -> Path:
    """Write a chart to OUTPUT_DIR as <request_id>.png and prune old files."""
    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = config.OUTPUT_DIR / f"{_safe_name(request_id)}.png"
    path.write_bytes(image_bytes)
    prune_output()
    return path


def prune_output() -> None:
    """Delete charts beyond max_files (oldest first) and older than max_age. Never raises."""
    try:
        files = sorted(
            config.OUTPUT_DIR.glob("*.png"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,  # newest first; index >= max_files are the oldest
        )
    except OSError as e:
        logger.warning("Could not list output dir for pruning: %s", e)
        return

    max_age_seconds = config.OUTPUT_MAX_AGE_HOURS * 3600
    now = time.time()

    for index, f in enumerate(files):
        too_many = config.OUTPUT_MAX_FILES > 0 and index >= config.OUTPUT_MAX_FILES
        too_old = False
        if max_age_seconds > 0:
            try:
                too_old = (now - f.stat().st_mtime) > max_age_seconds
            except OSError:
                too_old = False
        if too_many or too_old:
            try:
                f.unlink()
                logger.debug("Pruned old chart %s", f.name)
            except OSError as e:
                logger.warning("Could not delete %s: %s", f, e)
