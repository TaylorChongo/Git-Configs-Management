"""Tests for reading and writing configuration files with ConfigParser."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from config_cli.config_parser import ConfigParser
from config_cli.exceptions import ConfigCliError, ConfigNotFound, InvalidConfigError


@pytest.fixture
def parser() -> ConfigParser:
    """Provide a fresh ConfigParser for each test."""
    return ConfigParser()


# --- Happy paths -----------------------------------------------------------


@pytest.mark.parametrize("filename", ["app.json", "app.yaml", "app.yml", "APP.JSON"])
def test_write_then_read_returns_same_data(
    parser: ConfigParser, tmp_path: Path, filename: str
) -> None:
    """Data written to any supported format should read back unchanged."""
    path = tmp_path / filename
    data = {"database": {"host": "localhost", "port": 5432}, "debug": True}

    parser.write(path, data)

    assert parser.read(path) == data


def test_read_supports_top_level_list(parser: ConfigParser, tmp_path: Path) -> None:
    """A configuration whose top level is a list should be accepted."""
    path = tmp_path / "servers.yaml"
    path.write_text("- web1\n- web2\n", encoding="utf-8")

    assert parser.read(path) == ["web1", "web2"]


def test_read_empty_yaml_returns_empty_dict(
    parser: ConfigParser, tmp_path: Path
) -> None:
    """An empty YAML file should be treated as an empty configuration."""
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")

    assert parser.read(path) == {}


def test_write_creates_missing_parent_directories(
    parser: ConfigParser, tmp_path: Path
) -> None:
    """Writing into a folder that does not exist yet should create it."""
    path = tmp_path / "env" / "prod" / "app.yaml"

    parser.write(path, {"debug": False})

    assert path.is_file()


def test_write_json_is_indented_with_trailing_newline(
    parser: ConfigParser, tmp_path: Path
) -> None:
    """JSON output should be human-readable and end with a newline."""
    path = tmp_path / "app.json"

    parser.write(path, {"port": 5432})

    assert path.read_text(encoding="utf-8") == '{\n  "port": 5432\n}\n'


def test_write_yaml_keeps_key_order(parser: ConfigParser, tmp_path: Path) -> None:
    """YAML keys should stay in the order given, so Git diffs stay small."""
    path = tmp_path / "app.yaml"

    parser.write(path, {"zebra": 1, "apple": 2})

    assert path.read_text(encoding="utf-8") == "zebra: 1\napple: 2\n"


@pytest.mark.parametrize("filename", ["app.json", "app.yaml"])
def test_write_uses_unix_line_endings(
    parser: ConfigParser, tmp_path: Path, filename: str
) -> None:
    """Files should use LF endings on every OS to avoid noisy Git diffs."""
    path = tmp_path / filename

    parser.write(path, {"a": 1, "b": 2})

    assert b"\r\n" not in path.read_bytes()


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("a.json", True),
        ("a.YAML", True),
        ("a.yml", True),
        ("a.txt", False),
        (".gitattributes", False),
    ],
)
def test_supports_reports_known_formats(
    parser: ConfigParser, filename: str, expected: bool
) -> None:
    """supports should say whether a file's extension can be parsed."""
    assert parser.supports(Path(filename)) is expected


# --- Error paths -----------------------------------------------------------


def test_read_missing_file_raises_config_not_found(
    parser: ConfigParser, tmp_path: Path
) -> None:
    """Reading a file that does not exist should raise ConfigNotFound."""
    path = tmp_path / "missing.yaml"

    with pytest.raises(ConfigNotFound) as excinfo:
        parser.read(path)

    assert excinfo.value.path == path


@pytest.mark.parametrize("filename", ["notes.txt", "Makefile"])
def test_unsupported_format_is_rejected(
    parser: ConfigParser, tmp_path: Path, filename: str
) -> None:
    """Both read and write should refuse files that are not JSON or YAML."""
    path = tmp_path / filename

    with pytest.raises(InvalidConfigError, match="unsupported format"):
        parser.read(path)
    with pytest.raises(InvalidConfigError, match="unsupported format"):
        parser.write(path, {})


@pytest.mark.parametrize(
    ("filename", "content"),
    [("broken.json", "{not valid json"), ("broken.yaml", "key: [unclosed")],
)
def test_read_malformed_content_raises_invalid_config(
    parser: ConfigParser, tmp_path: Path, filename: str, content: str
) -> None:
    """Content that cannot be parsed should raise InvalidConfigError."""
    path = tmp_path / filename
    path.write_text(content, encoding="utf-8")

    with pytest.raises(InvalidConfigError, match="could not be parsed") as excinfo:
        parser.read(path)

    # `raise ... from error` keeps the original parser error for debugging.
    assert excinfo.value.__cause__ is not None


@pytest.mark.parametrize(
    ("filename", "content"), [("value.json", "42"), ("value.yaml", "just text")]
)
def test_read_scalar_top_level_raises_invalid_config(
    parser: ConfigParser, tmp_path: Path, filename: str, content: str
) -> None:
    """A file holding a single value is not a usable configuration."""
    path = tmp_path / filename
    path.write_text(content, encoding="utf-8")

    with pytest.raises(InvalidConfigError, match="object or a list"):
        parser.read(path)


@pytest.mark.parametrize("filename", ["app.json", "app.yaml"])
def test_write_unserializable_data_raises_invalid_config(
    parser: ConfigParser, tmp_path: Path, filename: str
) -> None:
    """Data that JSON/YAML cannot represent should raise InvalidConfigError."""
    # An arbitrary Python object has no JSON or YAML equivalent.
    data: dict[str, Any] = {"handler": object()}

    with pytest.raises(InvalidConfigError, match="could not be serialized"):
        parser.write(tmp_path / filename, data)

    # Data is converted before the file is opened, so nothing is left behind.
    assert not (tmp_path / filename).exists()


@pytest.mark.parametrize(
    ("filename", "expected"),
    [("app.json", '{\n  "port": 80\n}\n'), ("app.yaml", "port: 80\n")],
)
def test_dumps_formats_without_writing(
    parser: ConfigParser, tmp_path: Path, filename: str, expected: str
) -> None:
    """dumps should return the text in the file's format and touch no files."""
    assert parser.dumps(tmp_path / filename, {"port": 80}) == expected
    assert list(tmp_path.iterdir()) == []


def test_read_os_error_raises_config_cli_error(
    parser: ConfigParser, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A disk problem other than a missing file should raise ConfigCliError."""
    path = tmp_path / "app.yaml"
    path.write_text("a: 1\n", encoding="utf-8")

    def deny(*args: object, **kwargs: object) -> None:
        raise PermissionError("access denied")

    monkeypatch.setattr(Path, "open", deny)

    with pytest.raises(ConfigCliError, match="Could not read") as excinfo:
        parser.read(path)

    assert not isinstance(excinfo.value, ConfigNotFound)


def test_write_os_error_raises_config_cli_error(
    parser: ConfigParser, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A disk problem while writing should raise ConfigCliError."""

    def deny(*args: object, **kwargs: object) -> None:
        raise PermissionError("access denied")

    monkeypatch.setattr(Path, "open", deny)

    with pytest.raises(ConfigCliError, match="Could not write"):
        parser.write(tmp_path / "app.json", {"a": 1})
