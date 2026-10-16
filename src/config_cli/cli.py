"""Command-line entry point for the configuration management tool."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from config_cli import __version__
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


def _init(args: argparse.Namespace) -> None:
    """Handle ``config-cli init``."""
    repo = GitRepository.init(args.repo)
    print(f"Initialized configuration repository in {repo.path.resolve()}")
