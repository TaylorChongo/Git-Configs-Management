"""Combine the config parser and Git repository into configuration actions."""

from __future__ import annotations

from pathlib import Path

from config_cli.config_parser import ConfigData, ConfigParser
from config_cli.exceptions import InvalidConfigError
from config_cli.git_repository import MAIN_BRANCH, GitRepository

DRAFT_PREFIX = "draft/"


class ConfigManager:
    """Save configuration files into a Git repository as commits.

    Changes can be made directly on ``main`` or on a draft. A draft is a Git
    branch named ``draft/<name>`` that starts from ``main``, so work can be
    prepared without affecting the published configuration.

    The manager does not read files or talk to Git itself. It coordinates a
    ``ConfigParser`` (file format) and a ``GitRepository`` (version history),
    which are passed in so either can be swapped out, for example in tests.
    """

    def __init__(self, repo: GitRepository, parser: ConfigParser) -> None:
        """Create a manager that works on one repository.

        Args:
            repo: The repository that stores the configuration history.
            parser: The parser used to read and write configuration files.
        """
        self._repo = repo
        self._parser = parser

    def save(
        self, name: str, data: ConfigData, message: str | None = None
    ) -> str | None:
        """Write a configuration file and commit it on the current branch.

        Args:
            name: The file's path inside the repository, e.g. ``"app.yaml"``
                or ``"env/prod.json"``.
            data: The configuration to save.
            message: The commit message. Defaults to ``"Create <name>"`` for a
                new file and ``"Update <name>"`` for an existing one.

        Returns:
            The new commit's SHA, or ``None`` if the content was unchanged.

        Raises:
            InvalidConfigError: If ``name`` points outside the repository or
                into ``.git``, or the format or data is invalid.
        """
        relative_path = self._resolve_name(name)
        full_path = self._repo.path / relative_path

        if message is None:
            action = "Update" if full_path.exists() else "Create"
            message = f"{action} {relative_path.as_posix()}"

        self._parser.write(full_path, data)
        return self._repo.commit_files([relative_path], message)

    def start_draft(self, name: str) -> str:
        """Create a draft from ``main`` and switch to it.

        Args:
            name: The draft's name, e.g. ``"new-db-settings"``.

        Returns:
            The draft's branch name, e.g. ``"draft/new-db-settings"``.

        Raises:
            BranchError: If the name is invalid, the draft already exists, or
                there are uncommitted changes.
        """
        branch = DRAFT_PREFIX + name
        self._repo.create_branch(branch, start_point=MAIN_BRANCH)
        return branch

    def switch_to_draft(self, name: str) -> None:
        """Switch to an existing draft so further saves go onto it.

        Raises:
            BranchError: If the draft does not exist or there are uncommitted
                changes.
        """
        self._repo.checkout(DRAFT_PREFIX + name)

    def switch_to_main(self) -> None:
        """Switch back to ``main``, the published configuration.

        Raises:
            BranchError: If there are uncommitted changes.
        """
        self._repo.checkout(MAIN_BRANCH)

    def publish(self, name: str, message: str | None = None) -> str:
        """Merge a draft into ``main`` and delete the draft.

        On success ``main`` is left checked out. If the draft conflicts with
        ``main``, nothing changes: ``main``, the draft and the checked-out
        branch all stay as they were.

        Args:
            name: The draft to publish.
            message: The merge commit's message. Defaults to
                ``"Publish draft <name>"``.

        Returns:
            The SHA of the merge commit on ``main``.

        Raises:
            BranchError: If the draft does not exist, has nothing to publish,
                or there are uncommitted changes.
            MergeConflictError: If the draft and ``main`` changed the same lines.
        """
        branch = DRAFT_PREFIX + name
        sha = self._repo.merge(branch, MAIN_BRANCH, message or f"Publish draft {name}")
        self._repo.delete_branch(branch)
        return sha

    def list_drafts(self) -> list[str]:
        """Return the names of all drafts, without the ``draft/`` prefix."""
        return [
            branch.removeprefix(DRAFT_PREFIX)
            for branch in self._repo.list_branches()
            if branch.startswith(DRAFT_PREFIX)
        ]

    @property
    def current_draft(self) -> str | None:
        """The draft currently checked out, or ``None`` when on another branch."""
        branch = self._repo.current_branch
        if branch.startswith(DRAFT_PREFIX):
            return branch.removeprefix(DRAFT_PREFIX)
        return None

    def _resolve_name(self, name: str) -> Path:
        """Turn a user-supplied name into a safe path relative to the repo root.

        Raises:
            InvalidConfigError: If the path escapes the repository or targets
                Git's internal ``.git`` folder.
        """
        root = self._repo.path.resolve()
        target = (root / name).resolve()

        if not target.is_relative_to(root) or target == root:
            raise InvalidConfigError(Path(name), "path must be inside the repository")

        relative_path = target.relative_to(root)
        if ".git" in relative_path.parts:
            raise InvalidConfigError(Path(name), "cannot write inside .git")
        return relative_path
