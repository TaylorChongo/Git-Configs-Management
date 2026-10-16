"""Tests for the command-line interface."""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path
from unittest import mock

import pytest
from git import Repo

from config_cli import __version__
from config_cli.audit import audit_log_path
from config_cli.cli import EXIT_ERROR, EXIT_OK, Services, build_parser, main
from config_cli.config_manager import ConfigManager
from config_cli.config_parser import ConfigParser
from config_cli.exceptions import BranchError
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


# --- Drafts, publish and sync ----------------------------------------------


def output(capsys: pytest.CaptureFixture[str]) -> str:
    """Return what was printed to stdout since the last call."""
    return capsys.readouterr().out


@pytest.fixture
def conflicting(repo_dir: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    """A repo where draft 'new-port' and main both changed the port."""
    run(repo_dir, "set", "app.yaml", "port", "5432")
    run(repo_dir, "draft", "start", "new-port")
    run(repo_dir, "set", "app.yaml", "port", "6000")
    run(repo_dir, "draft", "main")
    run(repo_dir, "set", "app.yaml", "port", "7000")
    capsys.readouterr()
    return repo_dir


def test_draft_start_switch_main_and_list(
    repo_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The draft commands move between drafts and report where you are."""
    assert run(repo_dir, "draft", "list") == EXIT_OK
    assert output(capsys) == "No drafts.\n"

    run(repo_dir, "draft", "start", "b")
    assert "Started draft 'b' (branch draft/b)" in output(capsys)
    run(repo_dir, "draft", "start", "a")
    run(repo_dir, "draft", "list")
    assert output(capsys).endswith("* a\n  b\n")

    run(repo_dir, "draft", "switch", "b")
    assert output(capsys) == "Switched to draft 'b'.\n"
    assert Repo(repo_dir).active_branch.name == "draft/b"

    run(repo_dir, "draft", "main")
    assert output(capsys) == "Switched to main.\n"
    assert Repo(repo_dir).active_branch.name == MAIN_BRANCH


def test_draft_requires_an_action(repo_dir: Path) -> None:
    """'draft' on its own is a usage error."""
    with pytest.raises(SystemExit) as excinfo:
        run(repo_dir, "draft")

    assert excinfo.value.code == 2


def test_publish_merges_draft(
    repo_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """publish puts the draft's changes on main."""
    run(repo_dir, "draft", "start", "db")
    run(repo_dir, "set", "app.yaml", "host", "db1")
    capsys.readouterr()

    assert run(repo_dir, "publish", "db", "-m", "Go live") == EXIT_OK

    assert "Published draft 'db' to main" in output(capsys)
    head = Repo(repo_dir).heads[MAIN_BRANCH].commit
    assert head.message.strip() == "Go live"
    assert committed(repo_dir, "app.yaml") == b"host: db1\n"


def test_publish_conflict_points_to_sync(
    conflicting: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A publish conflict tells the user the exact command to run next."""
    assert run(conflicting, "publish", "new-port") == EXIT_ERROR

    err = capsys.readouterr().err
    assert "Merge conflict with 'draft/new-port' in: app.yaml" in err
    assert "Run 'config-cli sync new-port' to resolve it." in err


def test_full_conflict_workflow(
    conflicting: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """publish -> sync -> status -> fix -> sync --finish -> publish."""
    run(conflicting, "publish", "new-port")
    assert run(conflicting, "sync", "new-port") == EXIT_ERROR
    assert "config-cli sync --finish" in capsys.readouterr().err

    run(conflicting, "status")
    status = output(capsys)
    assert "On draft 'new-port' (branch draft/new-port)" in status
    assert "A sync is in progress." in status
    assert "  app.yaml" in status

    (conflicting / "app.yaml").write_bytes(b"port: 6500\n")
    assert run(conflicting, "sync", "--finish") == EXIT_OK
    assert "Sync finished" in output(capsys)

    assert run(conflicting, "publish", "new-port") == EXIT_OK
    assert committed(conflicting, "app.yaml") == b"port: 6500\n"


def test_sync_abort(conflicting: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """sync --abort puts the draft back."""
    run(conflicting, "sync", "new-port")
    capsys.readouterr()

    assert run(conflicting, "sync", "--abort") == EXIT_OK

    assert output(capsys) == "Sync aborted. The draft is back as it was.\n"
    assert (conflicting / "app.yaml").read_bytes() == b"port: 6000\n"


def test_sync_without_conflict(
    repo_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A sync with no conflicts completes in one step."""
    run(repo_dir, "draft", "start", "db")
    run(repo_dir, "set", "db.yaml", "host", "db1")
    run(repo_dir, "draft", "main")
    run(repo_dir, "set", "app.yaml", "port", "1")
    capsys.readouterr()

    assert run(repo_dir, "sync", "db") == EXIT_OK

    assert "Draft 'db' is up to date with main" in output(capsys)
    assert (repo_dir / "app.yaml").exists()


@pytest.mark.parametrize("args", [[], ["db", "--finish"], ["--finish", "--abort"]])
def test_sync_needs_exactly_one_mode(repo_dir: Path, args: list[str]) -> None:
    """sync takes a draft name, or --finish, or --abort; never none or two."""
    with pytest.raises(SystemExit) as excinfo:
        run(repo_dir, "sync", *args)

    assert excinfo.value.code == 2


def test_status_on_main(repo_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """status on a fresh repository just names the branch."""
    assert run(repo_dir, "status") == EXIT_OK
    assert output(capsys) == "On branch main\n"


# --- History, rollback and reading other branches --------------------------


@pytest.fixture
def three_ports(repo_dir: Path, capsys: pytest.CaptureFixture[str]) -> Path:
    """app.yaml set to ports 1, 2 and 3 in turn."""
    for port in ("1", "2", "3"):
        run(repo_dir, "set", "app.yaml", "port", port, "-m", f"Port {port}")
    capsys.readouterr()
    return repo_dir


def test_history_lists_versions(
    three_ports: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """history prints one line per version, newest first."""
    assert run(three_ports, "history", "app.yaml") == EXIT_OK

    lines = output(capsys).splitlines()
    assert [line.split("  ")[-1] for line in lines] == ["Port 3", "Port 2", "Port 1"]
    assert all("Test User" in line for line in lines)


def test_history_limit(three_ports: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """-n limits the output to the newest versions."""
    run(three_ports, "history", "app.yaml", "-n", "1")

    assert len(output(capsys).splitlines()) == 1


def test_rollback_restores_and_reports(
    three_ports: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """rollback restores an old version as a new commit."""
    first = Repo(three_ports).head.commit.parents[0].parents[0].hexsha[:7]

    assert run(three_ports, "rollback", "app.yaml", first) == EXIT_OK

    assert "Rolled back app.yaml on main" in output(capsys)
    assert committed(three_ports, "app.yaml") == b"port: 1\n"


def test_rollback_unknown_commit_reports_error(
    three_ports: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unknown commit ID is a short error, not a traceback."""
    assert run(three_ports, "rollback", "app.yaml", "nope") == EXIT_ERROR
    assert "Commit 'nope': no such commit" in capsys.readouterr().err


def test_show_from_another_branch(
    three_ports: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """show --branch reads main while a draft stays checked out."""
    run(three_ports, "draft", "start", "x")
    run(three_ports, "set", "app.yaml", "port", "99")
    capsys.readouterr()

    run(three_ports, "show", "app.yaml", "port", "--branch", "main")

    assert output(capsys) == "3\n"
    assert Repo(three_ports).active_branch.name == "draft/x"


def test_clone_command(
    three_ports: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """clone copies a repository into --repo."""
    target = tmp_path / "copy"

    assert run(target, "clone", str(three_ports)) == EXIT_OK

    assert f"into {target.resolve()} (main)" in output(capsys)
    assert (target / "app.yaml").read_bytes() == b"port: 3\n"


# --- Audit log and verbose output ------------------------------------------


def read_audit_log(repo_dir: Path) -> list[dict[str, object]]:
    """Parse the repository's JSON Lines audit log."""
    text = audit_log_path(repo_dir).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def test_commands_are_written_to_the_audit_log(repo_dir: Path) -> None:
    """Every command and the Git actions it caused are logged as JSON."""
    run(repo_dir, "set", "app.yaml", "port", "1")

    records = read_audit_log(repo_dir)
    events = [record["event"] for record in records]
    assert events[:2] == ["repository_initialized", "command_started"]
    assert events[-2:] == ["command_started", "committed"]
    assert records[-1]["files"] == ["app.yaml"]
    assert records[-2]["command"] == "set"


def test_failed_commands_are_logged(
    repo_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Errors are recorded with their type and message."""
    run(repo_dir, "show", "missing.yaml")

    failure = read_audit_log(repo_dir)[-1]
    assert failure["event"] == "command_failed"
    assert failure["level"] == "ERROR"
    assert failure["error_type"] == "ConfigNotFound"


def test_verbose_prints_audit_records(
    repo_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """-v shows the audit records on stderr as well."""
    run(repo_dir, "-v", "set", "app.yaml", "port", "1")

    err = capsys.readouterr().err
    assert "[INFO] command_started command=set" in err
    assert "[INFO] committed branch=main" in err


# --- The CLI depends on abstractions ---------------------------------------


def test_cli_can_run_with_fake_services(capsys: pytest.CaptureFixture[str]) -> None:
    """Handlers only use the injected Services, so a fake repo can stand in.

    This checks the CLI's own behaviour (turning an engine error into a
    message and exit code) without creating any Git repository.
    """
    fake_repo = mock.Mock()
    fake_repo.checkout.side_effect = BranchError("draft/x", "branch does not exist")
    services = Services(open_repository=lambda path: fake_repo)

    exit_code = main(["-C", "anywhere", "draft", "switch", "x"], services)

    assert exit_code == EXIT_ERROR
    assert capsys.readouterr().err == (
        "Error: Branch 'draft/x': branch does not exist\n"
    )
    fake_repo.checkout.assert_called_once_with("draft/x")
