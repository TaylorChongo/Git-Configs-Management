"""Tests for the tool's custom exceptions."""

from __future__ import annotations

from pathlib import Path

import pytest

from config_cli.exceptions import (
    BranchError,
    ConfigCliError,
    ConfigNotFound,
    InvalidConfigError,
    InvalidRepoError,
    MergeConflictError,
)


@pytest.mark.parametrize(
    "error",
    [
        ConfigNotFound(Path("app.yaml")),
        InvalidConfigError(Path("app.yaml"), "bad"),
        InvalidRepoError(Path("repo"), "not a git repository"),
        BranchError("draft/x", "branch already exists"),
        MergeConflictError("draft/x", ["app.yaml"]),
    ],
)
def test_every_error_is_a_config_cli_error(error: ConfigCliError) -> None:
    """All custom errors share one parent so the CLI can catch them together."""
    assert isinstance(error, ConfigCliError)


def test_branch_error_keeps_branch_and_reason() -> None:
    """BranchError should name the branch and explain the problem."""
    error = BranchError("draft/db", "branch already exists")

    assert error.branch == "draft/db"
    assert str(error) == "Branch 'draft/db': branch already exists"


def test_config_not_found_keeps_path_and_message() -> None:
    """ConfigNotFound should remember the missing path and name it."""
    error = ConfigNotFound(Path("app.yaml"))

    assert error.path == Path("app.yaml")
    assert "app.yaml" in str(error)


def test_invalid_config_error_includes_reason() -> None:
    """InvalidConfigError should explain what was wrong with the file."""
    error = InvalidConfigError(Path("app.json"), "content could not be parsed")

    assert error.path == Path("app.json")
    assert "content could not be parsed" in str(error)


def test_invalid_repo_error_includes_path_and_reason() -> None:
    """InvalidRepoError should name the path and the reason it is unusable."""
    error = InvalidRepoError(Path("configs"), "not a git repository")

    assert error.path == Path("configs")
    assert "configs" in str(error)
    assert "not a git repository" in str(error)


def test_merge_conflict_error_lists_files_and_guidance() -> None:
    """MergeConflictError should name the branch, files, and how to fix it."""
    error = MergeConflictError("draft/db", ["app.yaml", "db.json"])

    assert error.branch == "draft/db"
    assert error.conflicted_files == ["app.yaml", "db.json"]
    message = str(error)
    assert "draft/db" in message
    assert "app.yaml, db.json" in message
    assert "main is unchanged" in message
    assert "publish again" in message


def test_merge_conflict_error_without_files_says_unknown() -> None:
    """If Git does not report the files, the message should still make sense."""
    error = MergeConflictError("draft/db", [])

    assert "<unknown>" in str(error)
