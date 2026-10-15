"""Tests for the initial CLI setup."""

from config_cli.cli import build_parser


def test_cli_parser_has_expected_program_name() -> None:
    """The CLI parser should expose the configured application name."""
    assert build_parser().prog == "config-cli"
