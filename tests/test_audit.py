"""Tests for structured audit logging."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from config_cli.audit import (
    AUDIT_LOG_NAME,
    PACKAGE_LOGGER,
    JsonFormatter,
    ReadableFormatter,
    audit,
    audit_log_path,
    configure_logging,
    shutdown_logging,
)

_log = logging.getLogger(f"{PACKAGE_LOGGER}.tests")


@pytest.fixture(autouse=True)
def reset_logging() -> Iterator[None]:
    """Make sure no handler from one test leaks into the next."""
    yield
    shutdown_logging()


def make_record(event: str, **details: object) -> logging.LogRecord:
    """Build a log record the way audit() would."""
    record = _log.makeRecord(
        _log.name, logging.INFO, __file__, 1, event, (), None, extra=None
    )
    record.details = details
    return record


def read_records(path: Path) -> list[dict[str, object]]:
    """Parse a JSON Lines log file."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def test_audit_log_lives_inside_dot_git(tmp_path: Path) -> None:
    """The audit log belongs to the repo but is never committed."""
    assert audit_log_path(tmp_path) == tmp_path / ".git" / AUDIT_LOG_NAME


def test_json_formatter_writes_one_json_object() -> None:
    """Each record becomes one JSON object with the details merged in."""
    record = make_record("committed", branch="main", files=["app.yaml"])

    entry = json.loads(JsonFormatter().format(record))

    assert entry["event"] == "committed"
    assert entry["level"] == "INFO"
    assert entry["logger"] == f"{PACKAGE_LOGGER}.tests"
    assert entry["branch"] == "main"
    assert entry["files"] == ["app.yaml"]
    assert entry["timestamp"].endswith("+00:00")


def test_json_formatter_handles_non_json_values() -> None:
    """Values JSON cannot represent, such as paths, are written as text."""
    record = make_record("cloned", path=Path("configs"))

    assert json.loads(JsonFormatter().format(record))["path"] == "configs"


def test_readable_formatter_is_one_short_line() -> None:
    """The terminal format is level, event, then key=value pairs."""
    record = make_record("committed", branch="main")

    assert ReadableFormatter().format(record) == "[INFO] committed branch=main"


def test_configure_logging_appends_json_lines_to_file(tmp_path: Path) -> None:
    """Records are appended to the log file, one JSON object per line."""
    log_file = tmp_path / "audit.log"
    configure_logging(log_file)

    audit(_log, "first", n=1)
    audit(_log, "second", logging.WARNING, n=2)
    shutdown_logging()

    records = read_records(log_file)
    assert [record["event"] for record in records] == ["first", "second"]
    assert records[1]["level"] == "WARNING"


def test_log_file_is_skipped_when_folder_is_missing(tmp_path: Path) -> None:
    """With no .git folder (not a repo), nothing is written and nothing fails."""
    log_file = tmp_path / "missing" / "audit.log"
    configure_logging(log_file)

    audit(_log, "ignored")
    shutdown_logging()

    assert not log_file.parent.exists()


def test_log_file_is_created_once_the_folder_appears(tmp_path: Path) -> None:
    """init creates .git during the command; logging starts once it exists."""
    log_file = tmp_path / ".git" / "audit.log"
    configure_logging(log_file)
    audit(_log, "before")

    log_file.parent.mkdir()
    audit(_log, "after")
    shutdown_logging()

    assert [record["event"] for record in read_records(log_file)] == ["after"]


def test_verbose_prints_readable_records(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """--verbose also shows the records on stderr."""
    configure_logging(None, verbose=True)

    audit(_log, "committed", branch="main")

    assert capsys.readouterr().err == "[INFO] committed branch=main\n"


def test_configure_twice_replaces_handlers(tmp_path: Path) -> None:
    """Configuring again must not duplicate records."""
    log_file = tmp_path / "audit.log"
    configure_logging(log_file)
    configure_logging(log_file)

    audit(_log, "once")
    shutdown_logging()

    assert len(read_records(log_file)) == 1


def test_shutdown_restores_library_defaults(tmp_path: Path) -> None:
    """After shutdown, records propagate again and only the NullHandler stays."""
    configure_logging(tmp_path / "audit.log", verbose=True)

    shutdown_logging()

    logger = logging.getLogger(PACKAGE_LOGGER)
    assert logger.propagate
    assert all(isinstance(h, logging.NullHandler) for h in logger.handlers)
