"""Logging setup for the ingestion service.

Call configure_logging() once when the program starts (main.py does this in
Step 6). Every other module only does:

    import logging
    logger = logging.getLogger(__name__)
    logger.info("Extracted %d pages from %s", page_count, file_name)

Try it from services/ingestion:   python -m app.logging_config
"""

from __future__ import annotations

import logging
import logging.config
import sys

from app.config import get_settings

# One line per message: time, level, which module wrote it, then the message.
# Example: 2026-10-10 09:55:01 INFO     app.parsing.pdf | Extracted 2 pages from MiFID_II_2023_v1.pdf
LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Libraries that log a lot at INFO/DEBUG level. We only want their warnings and errors.
NOISY_LIBRARIES = ["urllib3", "httpx", "httpcore", "multipart"]


def configure_logging(level: str | None = None) -> None:
    """Set up logging for the whole program.

    Accepts an optional level ("DEBUG", "INFO", ...). If not given, the level
    comes from the settings (IRIS_LOG_LEVEL). Returns nothing; it changes
    Python's global logging configuration. Calling it again simply replaces
    the previous configuration, so it never prints messages twice.
    """
    log_level = (level or get_settings().log_level).upper()

    logging.config.dictConfig(
        {
            "version": 1,
            # Keep loggers that modules created at import time (before this call) working.
            # With True, they would be silenced without any warning.
            "disable_existing_loggers": False,
            "formatters": {
                "standard": {"format": LOG_FORMAT, "datefmt": DATE_FORMAT},
            },
            "handlers": {
                # Write to standard output. Docker (Phase 12) collects whatever goes there.
                "console": {
                    "class": "logging.StreamHandler",
                    "stream": sys.stdout,
                    "formatter": "standard",
                },
            },
            "root": {"level": log_level, "handlers": ["console"]},
            "loggers": {name: {"level": "WARNING"} for name in NOISY_LIBRARIES},
        }
    )
    # Capture warnings.warn(...) messages from libraries in the same log.
    logging.captureWarnings(True)


if __name__ == "__main__":
    configure_logging()
    settings = get_settings()
    demo_logger = logging.getLogger("app.demo")

    demo_logger.debug("DEBUG details: shown only when IRIS_LOG_LEVEL=DEBUG")
    demo_logger.info("Logging configured for %s at level %s", settings.app_name, settings.log_level)
    demo_logger.warning("Example warning: EMIR_2021_v2.pdf contains an embedded document")
    try:
        1 / 0
    except ZeroDivisionError:
        # logger.exception logs at ERROR level and adds the full traceback.
        demo_logger.exception("Example error with traceback")