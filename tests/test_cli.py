"""Tests for the command-line interface."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

import pytest
from git import Repo

from config_cli import __version__
from config_cli.cli import EXIT_ERROR, EXIT_OK, build_parser, main
from config_cli.git_repository import MAIN_BRANCH


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
