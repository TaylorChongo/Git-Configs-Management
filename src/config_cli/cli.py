"""Command-line entry point for the configuration management tool."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config_cli import __version__
from config_cli.audit import audit, audit_log_path, configure_logging, shutdown_logging
from config_cli.config_manager import ConfigManager
from config_cli.config_parser import ConfigParser
from config_cli.exceptions import ConfigCliError, MergeConflictError
from config_cli.git_repository import GitRepository
from config_cli.interfaces import ConfigFormat, VersionStore

_log = logging.getLogger(__name__)

# Exit codes: 0 means success; 1 means the tool reported a problem.
# (argparse itself exits with 2 when the command line is malformed.)
EXIT_OK = 0
EXIT_ERROR = 1


@dataclass(frozen=True)
class Services:
    """The building blocks the CLI uses, so tests can swap in fakes.

    The command handlers only see these factories and the abstract
    ``VersionStore`` and ``ConfigFormat`` types, never Git directly.
    """

    open_repository: Callable[[Path], VersionStore] = GitRepository.open
    init_repository: Callable[[Path], VersionStore] = GitRepository.init
    clone_repository: Callable[[str, Path, str | None], VersionStore] = (
        GitRepository.clone
    )
    config_format: Callable[[], ConfigFormat] = ConfigParser

    def open_manager(self, repo_path: Path) -> ConfigManager:
        """Open the repository at ``repo_path`` and wrap it in a ConfigManager."""
        return ConfigManager(self.open_repository(repo_path), self.config_format())


Handler = Callable[[argparse.Namespace, Services], None]


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser.

    Each command is a sub-parser that stores its handler function in
    ``args.handler``, so :func:`main` can run any command the same way.

    Returns:
        The configured parser.
    """
    parser = argparse.ArgumentParser(
        prog="config-cli",
        description="Manage versioned configuration files with Git.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "-C",
        "--repo",
        type=Path,
        default=Path("."),
        metavar="PATH",
        help="the configuration repository to use (default: current folder)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="also print the audit log records to the terminal",
    )

    commands = parser.add_subparsers(
        title="commands", dest="command", required=True, metavar="COMMAND"
    )
    _add_repository_commands(commands)
    _add_config_commands(commands)
    _add_draft_commands(commands)
    _add_history_commands(commands)
    return parser


def main(argv: Sequence[str] | None = None, services: Services | None = None) -> int:
    """Run the command-line application.

    Args:
        argv: The command-line arguments, without the program name. Defaults
            to ``sys.argv[1:]``; tests pass their own list.
        services: The building blocks to use. Defaults to the real Git and
            JSON/YAML implementations; tests can pass fakes.

    Returns:
        The process exit code: ``0`` on success, ``1`` if the tool reported
        a problem.
    """
    args = build_parser().parse_args(argv)
    configure_logging(audit_log_path(args.repo), verbose=args.verbose)
    try:
        audit(_log, "command_started", command=args.command, repo=str(args.repo))
        args.handler(args, services or Services())
    except ConfigCliError as error:
        # Expected problems get a short message. Anything else is a bug and
        # is allowed to crash with a full traceback so it can be fixed.
        audit(
            _log,
            "command_failed",
            logging.ERROR,
            command=args.command,
            error_type=type(error).__name__,
            error=str(error),
        )
        print(f"Error: {error}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        shutdown_logging()
    return EXIT_OK


# --- Parser sections --------------------------------------------------------


def _add_command(
    commands: Any, name: str, handler: Handler, help_text: str, description: str
) -> argparse.ArgumentParser:
    """Add a sub-command whose handler :func:`main` will call."""
    command: argparse.ArgumentParser = commands.add_parser(
        name, help=help_text, description=description
    )
    command.set_defaults(handler=handler)
    return command


def _add_repository_commands(commands: Any) -> None:
    _add_command(
        commands,
        "init",
        _init,
        "create a new configuration repository",
        "Create a new configuration repository at --repo.",
    )

    clone = _add_command(
        commands,
        "clone",
        _clone,
        "copy an existing configuration repository",
        "Clone SOURCE (a URL or a local path) into --repo.",
    )
    clone.add_argument("source", help="URL or path of the repository to copy")
    clone.add_argument("--branch", help="branch to check out after cloning")

    _add_command(
        commands,
        "status",
        _status,
        "show the current branch, draft and any unfinished sync",
        "Show which branch or draft is checked out and whether a sync is "
        "waiting for its conflicts to be fixed.",
    )


def _add_config_commands(commands: Any) -> None:
    save = _add_command(
        commands,
        "save",
        _save,
        "save a whole config file from a local file",
        "Copy a local JSON/YAML file into the repository as NAME and commit it "
        "on the current branch. The format follows NAME's extension, so a JSON "
        "file can be saved as YAML and vice versa.",
    )
    _add_name_argument(save)
    save.add_argument(
        "--from",
        dest="source",
        type=Path,
        required=True,
        metavar="FILE",
        help="the local file to read the configuration from",
    )
    _add_message_option(save)

    set_ = _add_command(
        commands,
        "set",
        _set,
        "set one value in a config file",
        "Set KEY to VALUE in NAME and commit it on the current branch. Use dots "
        "for nested keys, e.g. database.port. VALUE is read as JSON when "
        'possible (5432, true, null, [1, 2], "text"), otherwise as plain text.',
    )
    _add_name_argument(set_)
    set_.add_argument("key", help="dotted key, e.g. database.port")
    set_.add_argument("value", help="the new value")
    _add_message_option(set_)

    show = _add_command(
        commands,
        "show",
        _show,
        "print a config file or one value from it",
        "Print NAME from the current branch, or only KEY if given.",
    )
    _add_name_argument(show)
    show.add_argument("key", nargs="?", help="optional dotted key to print")
    show.add_argument(
        "--branch",
        help="read from another branch (e.g. main or draft/db) without "
        "switching to it",
    )


def _add_draft_commands(commands: Any) -> None:
    draft = commands.add_parser(
        "draft",
        help="start, switch between and list drafts",
        description="A draft is a branch where changes are prepared before "
        "being published to main.",
    )
    actions = draft.add_subparsers(
        title="actions", dest="draft_action", required=True, metavar="ACTION"
    )
    start = _add_command(
        actions,
        "start",
        _draft_start,
        "create a draft from main and switch to it",
        "Create draft NAME from the latest main and switch to it.",
    )
    start.add_argument("name", help="the draft's name, e.g. new-db-settings")
    switch = _add_command(
        actions,
        "switch",
        _draft_switch,
        "switch to an existing draft",
        "Switch to draft NAME so further changes go onto it.",
    )
    switch.add_argument("name", help="the draft to switch to")
    _add_command(
        actions,
        "main",
        _draft_main,
        "switch back to main",
        "Switch back to main, the published configuration.",
    )
    _add_command(
        actions,
        "list",
        _draft_list,
        "list all drafts",
        "List all drafts; the current one is marked with *.",
    )

    publish = _add_command(
        commands,
        "publish",
        _publish,
        "merge a draft into main",
        "Merge draft NAME into main and delete the draft. If it conflicts "
        "with main, nothing changes: sync the draft, fix it, then publish.",
    )
    publish.add_argument("name", help="the draft to publish")
    _add_message_option(publish)

    sync = _add_command(
        commands,
        "sync",
        _sync,
        "bring main's changes into a draft, and resolve conflicts",
        "Merge main into draft NAME. If they conflict, fix the listed files, "
        "then run 'sync --finish', or 'sync --abort' to undo.",
    )
    mode = sync.add_mutually_exclusive_group(required=True)
    mode.add_argument("name", nargs="?", help="the draft to update from main")
    mode.add_argument(
        "--finish", action="store_true", help="commit a sync after fixing conflicts"
    )
    mode.add_argument(
        "--abort", action="store_true", help="undo a sync that stopped on conflicts"
    )


def _add_history_commands(commands: Any) -> None:
    history = _add_command(
        commands,
        "history",
        _history,
        "list the versions of a config file",
        "List the commits on the current branch that changed NAME, newest first.",
    )
    _add_name_argument(history)
    history.add_argument(
        "-n",
        "--limit",
        type=int,
        metavar="N",
        help="show only the newest N versions",
    )

    rollback = _add_command(
        commands,
        "rollback",
        _rollback,
        "restore an earlier version of a config file",
        "Restore NAME to how it was in REVISION, as a new commit. History is "
        "never rewritten, so a rollback can itself be rolled back.",
    )
    _add_name_argument(rollback)
    rollback.add_argument(
        "revision", help="the commit to go back to (an ID from 'history')"
    )
    _add_message_option(rollback)


def _add_name_argument(parser: argparse.ArgumentParser) -> None:
    """Add the NAME argument shared by commands that work on one file."""
    parser.add_argument("name", help="config file in the repository, e.g. app.yaml")


def _add_message_option(parser: argparse.ArgumentParser) -> None:
    """Add the shared ``-m/--message`` option to a command that commits."""
    parser.add_argument(
        "-m", "--message", help="commit message (a sensible default is used)"
    )


# --- Command handlers -------------------------------------------------------


def _init(args: argparse.Namespace, services: Services) -> None:
    """Handle ``config-cli init``."""
    repo = services.init_repository(args.repo)
    print(f"Initialized configuration repository in {repo.path.resolve()}")


def _clone(args: argparse.Namespace, services: Services) -> None:
    """Handle ``config-cli clone SOURCE``."""
    repo = services.clone_repository(args.source, args.repo, args.branch)
    print(f"Cloned {args.source} into {repo.path.resolve()} ({repo.current_branch})")


def _status(args: argparse.Namespace, services: Services) -> None:
    """Handle ``config-cli status``."""
    status = services.open_manager(args.repo).status()
    if status.draft is not None:
        print(f"On draft '{status.draft}' (branch {status.branch})")
    else:
        print(f"On branch {status.branch}")
    if status.merge_in_progress:
        print("A sync is in progress.")
        if status.conflicted_files:
            print("Files to fix:")
            for name in status.conflicted_files:
                print(f"  {name}")
        print("Then run 'config-cli sync --finish', or 'sync --abort' to undo.")


def _save(args: argparse.Namespace, services: Services) -> None:
    """Handle ``config-cli save NAME --from FILE``."""
    manager = services.open_manager(args.repo)
    sha = manager.save_from_file(args.name, args.source, args.message)
    _report_commit(manager, args.name, sha, "Saved")


def _set(args: argparse.Namespace, services: Services) -> None:
    """Handle ``config-cli set NAME KEY VALUE``."""
    manager = services.open_manager(args.repo)
    value = _parse_value(args.value)
    sha = manager.set_value(args.name, args.key, value, args.message)
    _report_commit(manager, args.name, sha, "Saved")


def _show(args: argparse.Namespace, services: Services) -> None:
    """Handle ``config-cli show NAME [KEY] [--branch BRANCH]``."""
    manager = services.open_manager(args.repo)
    if args.key is None:
        # Print the whole file in its own format (JSON or YAML).
        data = manager.read(args.name, args.branch)
        print(manager.render(args.name, data), end="")
    else:
        print(_format_value(manager.get_value(args.name, args.key, args.branch)))


def _draft_start(args: argparse.Namespace, services: Services) -> None:
    """Handle ``config-cli draft start NAME``."""
    branch = services.open_manager(args.repo).start_draft(args.name)
    print(f"Started draft '{args.name}' (branch {branch}). Changes now go there.")


def _draft_switch(args: argparse.Namespace, services: Services) -> None:
    """Handle ``config-cli draft switch NAME``."""
    services.open_manager(args.repo).switch_to_draft(args.name)
    print(f"Switched to draft '{args.name}'.")


def _draft_main(args: argparse.Namespace, services: Services) -> None:
    """Handle ``config-cli draft main``."""
    services.open_manager(args.repo).switch_to_main()
    print("Switched to main.")


def _draft_list(args: argparse.Namespace, services: Services) -> None:
    """Handle ``config-cli draft list``."""
    manager = services.open_manager(args.repo)
    drafts = manager.list_drafts()
    if not drafts:
        print("No drafts.")
        return
    current = manager.current_draft
    for name in drafts:
        print(f"{'*' if name == current else ' '} {name}")


def _publish(args: argparse.Namespace, services: Services) -> None:
    """Handle ``config-cli publish NAME``."""
    manager = services.open_manager(args.repo)
    try:
        sha = manager.publish(args.name, args.message)
    except MergeConflictError as error:
        raise _with_hint(
            error, f"Run 'config-cli sync {args.name}' to resolve it."
        ) from error
    print(f"Published draft '{args.name}' to main (commit {sha[:7]}).")


def _sync(args: argparse.Namespace, services: Services) -> None:
    """Handle ``config-cli sync NAME | --finish | --abort``."""
    manager = services.open_manager(args.repo)
    if args.finish:
        sha = manager.finish_sync()
        print(f"Sync finished (commit {sha[:7]}). The draft can now be published.")
    elif args.abort:
        manager.abort_sync()
        print("Sync aborted. The draft is back as it was.")
    else:
        try:
            sha = manager.sync_draft(args.name)
        except MergeConflictError as error:
            raise _with_hint(
                error,
                "(Commands: 'config-cli sync --finish' / 'config-cli sync --abort'.)",
            ) from error
        print(f"Draft '{args.name}' is up to date with main (commit {sha[:7]}).")


def _history(args: argparse.Namespace, services: Services) -> None:
    """Handle ``config-cli history NAME``."""
    commits = services.open_manager(args.repo).history(args.name)
    if args.limit is not None:
        commits = commits[: max(args.limit, 0)]
    for commit in commits:
        when = commit.date.strftime("%Y-%m-%d %H:%M")
        print(f"{commit.short_sha}  {when}  {commit.author}  {commit.message}")


def _rollback(args: argparse.Namespace, services: Services) -> None:
    """Handle ``config-cli rollback NAME REVISION``."""
    manager = services.open_manager(args.repo)
    sha = manager.rollback(args.name, args.revision, args.message)
    _report_commit(manager, args.name, sha, "Rolled back")


# --- Helpers ----------------------------------------------------------------


def _report_commit(
    manager: ConfigManager, name: str, sha: str | None, verb: str
) -> None:
    """Tell the user whether a change was committed, and where."""
    if sha is None:
        print(f"No changes: {name} already has that content.")
        return
    draft = manager.current_draft
    where = f"draft '{draft}'" if draft else "main"
    print(f"{verb} {name} on {where} (commit {sha[:7]}).")


def _with_hint(error: MergeConflictError, hint: str) -> MergeConflictError:
    """Return a copy of a conflict error with a CLI command added to its advice."""
    return MergeConflictError(
        error.branch, error.conflicted_files, f"{error.guidance} {hint}"
    )


def _parse_value(text: str) -> Any:
    """Turn a VALUE typed on the command line into a Python value.

    JSON is tried first, so ``5432`` becomes a number and ``true`` a boolean.
    Anything that is not valid JSON, such as ``localhost``, stays as text.
    """

    def reject_constant(name: str) -> Any:
        # json accepts NaN and Infinity, which are not valid in JSON files.
        raise ValueError(name)

    try:
        return json.loads(text, parse_constant=reject_constant)
    except ValueError:
        return text


def _format_value(value: Any) -> str:
    """Format one value for printing: text as-is, everything else as JSON."""
    if isinstance(value, str):
        return value
    return json.dumps(value, indent=2)
