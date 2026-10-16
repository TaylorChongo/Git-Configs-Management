"""Tests for the command-line interface."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pytest
from git import Repo

from config_cli import __version__
from config_cli.cli import EXIT_ERROR, EXIT_OK, build_parser, main
from config_cli.config_manager import ConfigManager
from config_cli.config_parser import ConfigParser
from config_cli.git_repository import MAIN_BRANCH, GitRepository


def test_cli_parser_has_expected_program_name() -> None:
    """The CLI parser should expose the configured application name."""
    assert build_parser().prog == "config-cli"


# --- General behaviour -----------------------------------------------------


def test_version_flag_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    """--version should print the version and exit successfully."""
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])

    assert excinfo.value.code == 0
    assert capsys.readouterr().out.strip() == f"config-cli {__version__}"


def test_help_lists_commands(capsys: pytest.CaptureFixture[str]) -> None:
    """--help should show the available commands."""
    with pytest.raises(SystemExit):
        main(["--help"])

    assert "init" in capsys.readouterr().out


def test_missing_command_is_a_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Running without a command should print usage and exit with code 2."""
    with pytest.raises(SystemExit) as excinfo:
        main([])

    assert excinfo.value.code == 2
    assert "usage: config-cli" in capsys.readouterr().err


def test_unknown_command_is_a_usage_error() -> None:
    """A command that does not exist should be rejected by argparse."""
    with pytest.raises(SystemExit) as excinfo:
        main(["fly"])

    assert excinfo.value.code == 2


def test_runs_as_python_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`python -m config_cli ...` should run the same main function."""
    monkeypatch.setattr(sys, "argv", ["config-cli", "-C", str(tmp_path), "init"])

    with pytest.raises(SystemExit) as excinfo:
        runpy.run_module("config_cli", run_name="__main__")

    assert excinfo.value.code == EXIT_OK
    assert (tmp_path / ".git").is_dir()


# --- init ------------------------------------------------------------------


def test_init_creates_repository(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """init should create a config repository and say where."""
    target = tmp_path / "configs"

    exit_code = main(["--repo", str(target), "init"])

    assert exit_code == EXIT_OK
    assert Repo(target).active_branch.name == MAIN_BRANCH
    assert str(target.resolve()) in capsys.readouterr().out


def test_init_defaults_to_current_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without --repo, init should use the folder the command is run from."""
    monkeypatch.chdir(tmp_path)

    assert main(["init"]) == EXIT_OK
    assert (tmp_path / ".git").is_dir()


def test_init_twice_reports_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A tool error should print a short message to stderr and return 1."""
    main(["-C", str(tmp_path), "init"])
    capsys.readouterr()

    exit_code = main(["-C", str(tmp_path), "init"])

    captured = capsys.readouterr()
    assert exit_code == EXIT_ERROR
    assert captured.out == ""
    assert captured.err.startswith("Error: ")
    assert "already exists" in captured.err
    assert "Traceback" not in captured.err


# --- save, set and show ----------------------------------------------------


@pytest.fixture
def repo_dir(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    """A freshly initialized config repository, with init's output discarded."""
    path = tmp_path / "configs"
    assert main(["-C", str(path), "init"]) == EXIT_OK
    capsys.readouterr()
    return path


def run(repo_dir: Path, *args: str) -> int:
    """Run a config-cli command against the test repository."""
    return main(["-C", str(repo_dir), *args])


def committed(repo_dir: Path, name: str) -> bytes:
    """Return a file's content in the latest commit."""
    blob = Repo(repo_dir).head.commit.tree / name
    content: bytes = blob.data_stream.read()
    return content


def test_command_outside_a_repository_reports_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Commands other than init need an existing config repository."""
    assert main(["-C", str(tmp_path), "show", "app.yaml"]) == EXIT_ERROR
    assert "not a Git repository" in capsys.readouterr().err


def test_save_from_local_file(
    repo_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """save --from copies a local file into the repository and commits it."""
    source = tmp_path / "local.yaml"
    source.write_bytes(b"port: 5432\n")

    exit_code = run(repo_dir, "save", "app.yaml", "--from", str(source))

    assert exit_code == EXIT_OK
    assert committed(repo_dir, "app.yaml") == b"port: 5432\n"
    assert Repo(repo_dir).head.commit.message == "Create app.yaml"
    assert "Saved app.yaml on main (commit " in capsys.readouterr().out


def test_save_converts_between_formats(repo_dir: Path, tmp_path: Path) -> None:
    """A JSON source saved under a .yaml name is stored as YAML."""
    source = tmp_path / "local.json"
    source.write_text('{"port": 5432, "debug": false}', encoding="utf-8")

    run(repo_dir, "save", "app.yaml", "--from", str(source), "-m", "Import")

    assert committed(repo_dir, "app.yaml") == b"port: 5432\ndebug: false\n"
    assert Repo(repo_dir).head.commit.message == "Import"


def test_save_unchanged_says_no_changes(
    repo_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Saving identical content again reports that nothing changed."""
    source = tmp_path / "local.yaml"
    source.write_bytes(b"port: 5432\n")
    run(repo_dir, "save", "app.yaml", "--from", str(source))
    capsys.readouterr()

    assert run(repo_dir, "save", "app.yaml", "--from", str(source)) == EXIT_OK
    assert "No changes" in capsys.readouterr().out


def test_save_missing_source_reports_error(
    repo_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A --from file that does not exist is reported, and nothing is committed."""
    commits_before = len(list(Repo(repo_dir).iter_commits()))

    exit_code = run(repo_dir, "save", "app.yaml", "--from", str(tmp_path / "x.yaml"))

    assert exit_code == EXIT_ERROR
    assert "not found" in capsys.readouterr().err
    assert len(list(Repo(repo_dir).iter_commits())) == commits_before


def test_save_requires_from(repo_dir: Path) -> None:
    """--from is required, so leaving it out is a usage error."""
    with pytest.raises(SystemExit) as excinfo:
        run(repo_dir, "save", "app.yaml")

    assert excinfo.value.code == 2


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("5433", b"port: 5433\n"),
        ("true", b"port: true\n"),
        ("null", b"port: null\n"),
        ("localhost", b"port: localhost\n"),
        ('"5433"', b"port: '5433'\n"),
        ("[1, 2]", b"port:\n- 1\n- 2\n"),
        ("NaN", b"port: NaN\n"),
    ],
)
def test_set_reads_value_as_json_or_text(
    repo_dir: Path, typed: str, stored: bytes
) -> None:
    """VALUE becomes a number, boolean, list... or stays text if not JSON."""
    assert run(repo_dir, "set", "app.yaml", "port", typed) == EXIT_OK

    assert committed(repo_dir, "app.yaml") == stored


def test_set_nested_key_and_report(
    repo_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """set creates nested sections and reports the commit."""
    run(repo_dir, "set", "app.yaml", "database.port", "5432")

    assert committed(repo_dir, "app.yaml") == b"database:\n  port: 5432\n"
    assert Repo(repo_dir).head.commit.message == "Set database.port in app.yaml"
    assert "Saved app.yaml on main" in capsys.readouterr().out


def test_set_on_a_draft_says_so(
    repo_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The report says when the change went onto a draft."""
    ConfigManager(GitRepository.open(repo_dir), ConfigParser()).start_draft("db")

    run(repo_dir, "set", "app.yaml", "port", "1")

    assert "Saved app.yaml on draft 'db'" in capsys.readouterr().out


def test_set_invalid_key_reports_error(
    repo_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Problems found by the engine are reported as short errors."""
    assert run(repo_dir, "set", "app.yaml", "a..b", "1") == EXIT_ERROR
    assert "invalid key 'a..b'" in capsys.readouterr().err


def test_show_whole_file(repo_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """show NAME prints the file in its own format."""
    run(repo_dir, "set", "app.json", "port", "5432")
    capsys.readouterr()

    assert run(repo_dir, "show", "app.json") == EXIT_OK
    assert capsys.readouterr().out == '{\n  "port": 5432\n}\n'


@pytest.mark.parametrize(
    ("key", "printed"),
    [
        ("database.host", "localhost\n"),
        ("database.port", "5432\n"),
        ("database", '{\n  "host": "localhost",\n  "port": 5432\n}\n'),
    ],
)
def test_show_one_key(
    repo_dir: Path, capsys: pytest.CaptureFixture[str], key: str, printed: str
) -> None:
    """show NAME KEY prints text as-is and anything else as JSON."""
    run(repo_dir, "set", "app.yaml", "database.host", "localhost")
    run(repo_dir, "set", "app.yaml", "database.port", "5432")
    capsys.readouterr()

    assert run(repo_dir, "show", "app.yaml", key) == EXIT_OK
    assert capsys.readouterr().out == printed


def test_show_missing_key_reports_error(
    repo_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Asking for a key that is not there is a clear error."""
    run(repo_dir, "set", "app.yaml", "port", "5432")
    capsys.readouterr()

    assert run(repo_dir, "show", "app.yaml", "host") == EXIT_ERROR
    assert "Key 'host' not found" in capsys.readouterr().err
