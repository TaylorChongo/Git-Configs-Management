"""Structured audit logging for every action the tool performs.

Each action is logged as one JSON object per line (JSON Lines), for example::

    {"timestamp": "2026-10-16T09:12:03+00:00", "level": "INFO",
     "logger": "config_cli.git_repository", "event": "committed",
     "branch": "main", "sha": "4efd138...", "files": ["app.yaml"]}

JSON Lines can be searched with ordinary tools (grep, jq) or loaded into a
log system, which makes the log a usable audit trail.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

PACKAGE_LOGGER = "config_cli"

# The audit log lives inside the repository's .git folder: it belongs to that
# repository, but it is not versioned or pushed with the configuration.
AUDIT_LOG_NAME = "config-cli-audit.log"


def audit_log_path(repo_path: Path) -> Path:
    """Return where the audit log for a repository is written."""
    return repo_path / ".git" / AUDIT_LOG_NAME


def audit(
    logger: logging.Logger, event: str, level: int = logging.INFO, **details: Any
) -> None:
    """Log one action with structured details.

    Args:
        logger: The module's logger.
        event: A short, stable name for the action, e.g. ``"committed"``.
        level: The logging level.
        **details: Facts about the action, e.g. ``branch="main"``.
    """
    logger.log(level, event, extra={"details": details})


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        entry.update(getattr(record, "details", {}))
        return json.dumps(entry, default=str)


class ReadableFormatter(logging.Formatter):
    """Format log records as short ``key=value`` lines for the terminal."""

    def format(self, record: logging.LogRecord) -> str:
        details = getattr(record, "details", {})
        pairs = " ".join(f"{key}={value}" for key, value in details.items())
        return f"[{record.levelname}] {record.getMessage()} {pairs}".rstrip()


class AuditFileHandler(logging.FileHandler):
    """A file handler that only creates the log once the folder exists.

    The file is opened lazily (``delay=True``), so ``init`` and ``clone`` can
    log to a ``.git`` folder that is only created during the command. If the
    folder never appears (e.g. ``--repo`` is not a repository), records are
    skipped instead of failing.
    """

    def __init__(self, path: Path) -> None:
        super().__init__(path, encoding="utf-8", delay=True)

    def emit(self, record: logging.LogRecord) -> None:
        if self.stream is None and not Path(self.baseFilename).parent.is_dir():
            return
        super().emit(record)


def configure_logging(log_file: Path | None, verbose: bool = False) -> None:
    """Send the tool's log records to the audit file and, optionally, stderr.

    Calling it again replaces the previous handlers, so it is safe to call
    once per command (and once per test).

    Args:
        log_file: Where to append JSON Lines records, or ``None`` for no file.
        verbose: Also print readable records to stderr.
    """
    shutdown_logging()
    logger = logging.getLogger(PACKAGE_LOGGER)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if log_file is not None:
        file_handler = AuditFileHandler(log_file)
        file_handler.setFormatter(JsonFormatter())
        logger.addHandler(file_handler)
    if verbose:
        stream_handler = logging.StreamHandler(sys.stderr)
        stream_handler.setFormatter(ReadableFormatter())
        logger.addHandler(stream_handler)


def shutdown_logging() -> None:
    """Remove and close the handlers added by :func:`configure_logging`.

    Closing matters on Windows, where an open log file cannot be deleted.
    Records go back to propagating to the root logger, the library default.
    """
    logger = logging.getLogger(PACKAGE_LOGGER)
    for handler in list(logger.handlers):
        if isinstance(handler, logging.NullHandler):
            continue
        logger.removeHandler(handler)
        handler.close()
    logger.propagate = True
    logger.setLevel(logging.NOTSET)
