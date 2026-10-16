"""Command-line entry point for the configuration management tool."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from config_cli import __version__
from config_cli.config_manager import ConfigManager
from config_cli.config_parser import ConfigParser
from config_cli.exceptions import ConfigCliError
from config_cli.git_repository import GitRepository

# Exit codes: 0 means success; 1 means the tool reported a problem.
# (argparse itself exits with 2 when the command line is malformed.)
EXIT_OK = 0
EXIT_ERROR = 1


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

    commands = parser.add_subparsers(
        title="commands", dest="command", required=True, metavar="COMMAND"
    )

    init_parser = commands.add_parser(
        "init",
        help="create a new configuration repository",
        description="Create a new configuration repository at --repo.",
    )
    init_parser.set_defaults(handler=_init)

    save_parser = commands.add_parser(
        "save",
        help="save a whole config file from a local file",
        description=(
            "Copy a local JSON/YAML file into the repository as NAME and commit "
            "it on the current branch. The format follows NAME's extension, so "
            "a JSON file can be saved as YAML and vice versa."
        ),
    )
    save_parser.add_argument(
        "name", help="config file in the repository, e.g. app.yaml"
    )
    save_parser.add_argument(
        "--from",
        dest="source",
        type=Path,
        required=True,
        metavar="FILE",
        help="the local file to read the configuration from",
    )
    _add_message_option(save_parser)
    save_parser.set_defaults(handler=_save)

    set_parser = commands.add_parser(
        "set",
        help="set one value in a config file",
        description=(
            "Set KEY to VALUE in NAME and commit it on the current branch. "
            "Use dots for nested keys, e.g. database.port. VALUE is read as "
            'JSON when possible (5432, true, null, [1, 2], "text"), otherwise '
            "as plain text."
        ),
    )
    set_parser.add_argument("name", help="config file in the repository, e.g. app.yaml")
    set_parser.add_argument("key", help="dotted key, e.g. database.port")
    set_parser.add_argument("value", help="the new value")
    _add_message_option(set_parser)
    set_parser.set_defaults(handler=_set)

    show_parser = commands.add_parser(
        "show",
        help="print a config file or one value from it",
        description="Print NAME from the current branch, or only KEY if given.",
    )
    show_parser.add_argument(
        "name", help="config file in the repository, e.g. app.yaml"
    )
    show_parser.add_argument("key", nargs="?", help="optional dotted key to print")
    show_parser.set_defaults(handler=_show)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line application.

    Args:
        argv: The command-line arguments, without the program name. Defaults
            to ``sys.argv[1:]``; tests pass their own list.

    Returns:
        The process exit code: ``0`` on success, ``1`` if the tool reported
        a problem.
    """
    args = build_parser().parse_args(argv)
    try:
        args.handler(args)
    except ConfigCliError as error:
        # Expected problems get a short message. Anything else is a bug and
        # is allowed to crash with a full traceback so it can be fixed.
        print(f"Error: {error}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


# --- Command handlers -------------------------------------------------------


def _init(args: argparse.Namespace) -> None:
    """Handle ``config-cli init``."""
    repo = GitRepository.init(args.repo)
    print(f"Initialized configuration repository in {repo.path.resolve()}")


def _save(args: argparse.Namespace) -> None:
    """Handle ``config-cli save NAME --from FILE``."""
    manager = _open_manager(args)
    data = ConfigParser().read(args.source)
    sha = manager.save(args.name, data, args.message)
    _report_commit(manager, args.name, sha)


def _set(args: argparse.Namespace) -> None:
    """Handle ``config-cli set NAME KEY VALUE``."""
    manager = _open_manager(args)
    sha = manager.set_value(args.name, args.key, _parse_value(args.value), args.message)
    _report_commit(manager, args.name, sha)


def _show(args: argparse.Namespace) -> None:
    """Handle ``config-cli show NAME [KEY]``."""
    manager = _open_manager(args)
    if args.key is None:
        # Print the whole file in its own format (JSON or YAML).
        data = manager.read(args.name)
        print(ConfigParser().dumps(Path(args.name), data), end="")
    else:
        print(_format_value(manager.get_value(args.name, args.key)))


# --- Helpers ----------------------------------------------------------------


def _add_message_option(parser: argparse.ArgumentParser) -> None:
    """Add the shared ``-m/--message`` option to a command that commits."""
    parser.add_argument(
        "-m", "--message", help="commit message (a sensible default is used)"
    )


def _open_manager(args: argparse.Namespace) -> ConfigManager:
    """Open the repository chosen with --repo and wrap it in a ConfigManager."""
    return ConfigManager(GitRepository.open(args.repo), ConfigParser())


def _report_commit(manager: ConfigManager, name: str, sha: str | None) -> None:
    """Tell the user whether a change was committed, and where."""
    if sha is None:
        print(f"No changes: {name} already has that content.")
        return
    draft = manager.current_draft
    where = f"draft '{draft}'" if draft else "main"
    print(f"Saved {name} on {where} (commit {sha[:7]}).")


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
