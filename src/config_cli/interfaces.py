"""Abstractions that the higher layers depend on.

``ConfigManager`` and the CLI are written against these Protocols rather than
the concrete ``ConfigParser`` and ``GitRepository`` classes (the Dependency
Inversion Principle). Any object with matching methods can be used instead,
for example a fake repository in tests, without inheriting from anything.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from config_cli.config_parser import ConfigData
    from config_cli.git_repository import CommitInfo


class ConfigFormat(Protocol):
    """Reads and writes configuration files (JSON/YAML)."""

    def read(self, path: Path) -> ConfigData:
        """Read and parse a configuration file."""
        ...

    def write(self, path: Path, data: ConfigData) -> None:
        """Write configuration data to a file."""
        ...

    def dumps(self, path: Path, data: ConfigData) -> str:
        """Format configuration data as text in the format ``path`` uses."""
        ...

    def supports(self, path: Path) -> bool:
        """Return whether the file's extension is a supported format."""
        ...


class VersionStore(Protocol):
    """Keeps the version history of configuration files (a Git repository)."""

    @property
    def path(self) -> Path:
        """The repository's root folder."""
        ...

    @property
    def current_branch(self) -> str:
        """The branch that is checked out."""
        ...

    @property
    def merge_in_progress(self) -> bool:
        """Whether a merge is waiting to be finished or aborted."""
        ...

    def commit_files(self, paths: list[Path], message: str) -> str | None:
        """Commit files on the current branch."""
        ...

    def create_branch(self, name: str, start_point: str = ...) -> None:
        """Create a branch and check it out."""
        ...

    def checkout(self, name: str) -> None:
        """Switch to a branch."""
        ...

    def merge(
        self, source: str, target: str, message: str, keep_conflicts: bool = ...
    ) -> str:
        """Merge one branch into another."""
        ...

    def finish_merge(self, validate: Callable[[Path], None] | None = ...) -> str:
        """Commit a merge whose conflicts were fixed by hand."""
        ...

    def abort_merge(self) -> None:
        """Cancel the merge in progress."""
        ...

    def conflicted_files(self) -> list[str]:
        """Files Git could not merge automatically."""
        ...

    def delete_branch(self, name: str) -> None:
        """Delete a merged branch."""
        ...

    def branch_exists(self, name: str) -> bool:
        """Whether a branch exists."""
        ...

    def list_branches(self) -> list[str]:
        """All branch names."""
        ...

    def file_history(self, path: Path) -> list[CommitInfo]:
        """Commits on the current branch that changed a file."""
        ...

    def restore_file(self, path: Path, revision: str) -> CommitInfo:
        """Put a file back to an earlier version without committing."""
        ...

    def temporary_clone(
        self, branch: str | None = ...
    ) -> AbstractContextManager[VersionStore]:
        """A throwaway clone of the repository, deleted afterwards."""
        ...
