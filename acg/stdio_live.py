"""Line-buffered stdio for long-running CLIs (live logs to terminals and files)."""

from __future__ import annotations

import sys


def prefer_line_buffered_stdio() -> None:
    """Best-effort: flush after each newline so redirected logs update promptly."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(line_buffering=True)
        except (OSError, ValueError, TypeError):
            pass
