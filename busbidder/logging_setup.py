"""Central logging configuration.

Important events (data loaded, records rejected, bookings created/cancelled)
and errors are written to a log file.  The console is reserved for the user
interface, so the log handler is file-only.
"""

from __future__ import annotations

import logging
from pathlib import Path

LOGGER_NAME = "busbidder"
_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"

# Until configure_logging() runs (e.g. when the settings file itself is
# missing), log records are discarded instead of leaking to the console.
logging.getLogger(LOGGER_NAME).addHandler(logging.NullHandler())
logging.getLogger(LOGGER_NAME).propagate = False


def configure_logging(log_file: Path, level: int = logging.INFO) -> logging.Logger:
    """Attach a single UTF-8 file handler to the application logger."""
    log_file = Path(log_file)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    # Replace earlier handlers so repeated calls (e.g. in tests) do not duplicate lines.
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter(_FORMAT))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a child logger such as ``busbidder.loader``."""
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
