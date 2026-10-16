"""Custom exceptions raised by the configuration management tool."""

from __future__ import annotations

from pathlib import Path


class ConfigCliError(Exception):
    """Base class for every error raised by this tool.

    Catching this one class lets the CLI handle all expected failures in a
    single place, while unexpected bugs still surface as normal exceptions.
    """


class ConfigNotFound(ConfigCliError):
    """Raised when a requested configuration file does not exist.

    Attributes:
        path: The configuration file that could not be found.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(f"Configuration file not found: {path}")


class InvalidConfigError(ConfigCliError):
    """Raised when a configuration file is malformed or uses an unsupported format.

    Attributes:
        path: The configuration file that caused the error.
    """

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        super().__init__(f"Invalid configuration file {path}: {reason}")


class InvalidRepoError(ConfigCliError):
    """Raised when a path is not a usable Git repository.

    Attributes:
        path: The path that was expected to be a Git repository.
    """

    def __init__(self, path: Path, reason: str) -> None:
        self.path = path
        super().__init__(f"Invalid Git repository at {path}: {reason}")


class MergeConflictError(ConfigCliError):
    """Raised when publishing a draft branch causes a merge conflict.

    Attributes:
        branch: The draft branch that could not be merged.
        conflicted_files: The files Git could not merge automatically.
    """

    def __init__(self, branch: str, conflicted_files: list[str]) -> None:
        self.branch = branch
        self.conflicted_files = conflicted_files
        files = ", ".join(conflicted_files) or "<unknown>"
        super().__init__(
            f"Merge conflict while publishing '{branch}' in: {files}. "
            "Edit the files to resolve the conflict markers, then try again."
        )
