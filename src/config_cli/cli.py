"""Command-line entry point for the configuration management tool."""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line argument parser."""
    parser = argparse.ArgumentParser(
        prog="config-cli",
        description="Manage versioned configuration files with Git.",
    )
    return parser


def main() -> None:
    """Run the command-line application."""
    build_parser().parse_args()


if __name__ == "__main__":
    main()
