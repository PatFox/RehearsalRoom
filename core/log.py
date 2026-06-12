"""App-wide logging to ~/.rehearsalroom/app.log.

The UI deliberately swallows most errors (a corrupt .stems file should not
crash the library view) — this log is the diagnostic trail for those silent
fallbacks. Use `get_logger(__name__)` anywhere in the app.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

_LOG_DIR  = Path.home() / ".rehearsalroom"
_LOG_FILE = _LOG_DIR / "app.log"
_configured = False


def _configure() -> None:
    global _configured
    if _configured:
        return
    _configured = True
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            _LOG_FILE, maxBytes=512 * 1024, backupCount=1, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
        ))
        root = logging.getLogger("rehearsalroom")
        root.setLevel(logging.INFO)
        root.addHandler(handler)
    except OSError:
        pass   # logging must never break the app


def get_logger(name: str) -> logging.Logger:
    _configure()
    return logging.getLogger(f"rehearsalroom.{name}")
