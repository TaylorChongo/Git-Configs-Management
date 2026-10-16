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


class BranchError(ConfigCliError):
    """Raised when a branch cannot be created or checked out.

    Attributes:
        branch: The branch the operation was about.
    """

    def __init__(self, branch: str, reason: str) -> None:
        self.branch = branch
        super().__init__(f"Branch '{branch}': {reason}")


class CommitNotFoundError(ConfigCliError):
    """Raised when a commit ID cannot be used, e.g. for a rollback.

    Attributes:
        revision: The commit ID (or other Git revision) that was given.
    """

    def __init__(self, revision: str, reason: str) -> None:
        self.revision = revision
        super().__init__(f"Commit '{revision}': {reason}")


class MergeConflictError(ConfigCliError):
    """Raised when a merge stops because both sides changed the same lines.

    Attributes:
        branch: The branch being merged when the conflict happened.
        conflicted_files: The files Git could not merge automatically.
        guidance: What the user should do next.
    """

    def __init__(
        self,
        branch: str,
        conflicted_files: list[str],
        guidance: str = "Resolve the conflicting changes, then try again.",
    ) -> None:
        self.branch = branch
        self.conflicted_files = conflicted_files
        self.guidance = guidance
        files = ", ".join(conflicted_files) or "<unknown>"
        super().__init__(f"Merge conflict with '{branch}' in: {files}. {guidance}")
