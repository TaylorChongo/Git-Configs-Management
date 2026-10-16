"""Combine the config parser and Git repository into configuration actions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from config_cli.config_parser import ConfigData
from config_cli.exceptions import (
    ConfigNotFound,
    InvalidConfigError,
    KeyNotFoundError,
    MergeConflictError,
)
from config_cli.git_repository import MAIN_BRANCH, CommitInfo
from config_cli.interfaces import ConfigFormat, VersionStore

DRAFT_PREFIX = "draft/"


@dataclass(frozen=True)
class RepoStatus:
    """A snapshot of where the repository stands.

    Attributes:
        branch: The checked-out branch.
        draft: The checked-out draft's name, or ``None`` when not on a draft.
        merge_in_progress: Whether a sync is waiting to be finished.
        conflicted_files: Files that still need their conflicts resolved.
    """

    branch: str
    draft: str | None
    merge_in_progress: bool
    conflicted_files: list[str]


class ConfigManager:
    """Save configuration files into a Git repository as commits.

    Changes can be made directly on ``main`` or on a draft. A draft is a Git
    branch named ``draft/<name>`` that starts from ``main``, so work can be
    prepared without affecting the published configuration.

    The manager does not read files or talk to Git itself. It coordinates a
    ``ConfigFormat`` (file format) and a ``VersionStore`` (version history).
    Both are abstractions, passed in so either can be swapped out, for
    example for a fake in tests.
    """

    def __init__(self, repo: VersionStore, parser: ConfigFormat) -> None:
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

    def save_from_file(
        self, name: str, source: Path, message: str | None = None
    ) -> str | None:
        """Copy a local JSON/YAML file into the repository and commit it.

        The data is converted to the format of ``name``, so a JSON file can be
        saved as YAML and vice versa.

        Args:
            name: The file's path inside the repository.
            source: The local file to read.
            message: The commit message (see :meth:`save`).

        Returns:
            The new commit's SHA, or ``None`` if the content was unchanged.

        Raises:
            ConfigNotFound: If ``source`` does not exist.
            InvalidConfigError: If ``source`` or ``name`` is not valid.
        """
        return self.save(name, self._parser.read(source), message)

    def read(self, name: str, branch: str | None = None) -> ConfigData:
        """Read a config file from the current branch or another branch.

        Reading another branch uses a temporary clone, so the checked-out
        branch and its files are never touched.

        Args:
            name: The file's path inside the repository, e.g. ``"app.yaml"``.
            branch: The branch to read from. Defaults to the current branch.

        Returns:
            The parsed configuration.

        Raises:
            InvalidConfigError: If ``name`` points outside the repository or
                the file is not valid JSON/YAML.
            ConfigNotFound: If the file does not exist.
            BranchError: If ``branch`` does not exist.
        """
        relative_path = self._resolve_name(name)
        if branch is None or branch == self._repo.current_branch:
            return self._parser.read(self._repo.path / relative_path)
        with self._repo.temporary_clone(branch) as clone:
            return self._parser.read(clone.path / relative_path)

    def render(self, name: str, data: ConfigData) -> str:
        """Format data as text in the format ``name`` uses (JSON or YAML)."""
        return self._parser.dumps(Path(name), data)

    def status(self) -> RepoStatus:
        """Return the checked-out branch, draft and any unfinished sync."""
        return RepoStatus(
            branch=self._repo.current_branch,
            draft=self.current_draft,
            merge_in_progress=self._repo.merge_in_progress,
            conflicted_files=self._repo.conflicted_files(),
        )

    def get_value(self, name: str, key: str, branch: str | None = None) -> Any:
        """Read one value from a config file using a dotted key.

        Args:
            name: The file's path inside the repository.
            key: Where the value is, with dots between levels, e.g.
                ``"database.port"`` for ``{"database": {"port": 5432}}``.
            branch: The branch to read from. Defaults to the current branch.

        Returns:
            The value, which may itself be a section (a dict) or a list.

        Raises:
            KeyNotFoundError: If any part of the key does not exist.
            InvalidConfigError: If the key is malformed, e.g. ``"a..b"``.
            ConfigNotFound: If the file does not exist.
        """
        parts = self._split_key(name, key)
        current: Any = self.read(name, branch)
        for part in parts:
            if not isinstance(current, dict) or part not in current:
                raise KeyNotFoundError(Path(name), key)
            current = current[part]
        return current

    def set_value(
        self, name: str, key: str, value: Any, message: str | None = None
    ) -> str | None:
        """Set one value in a config file and commit the change.

        Missing sections along the key are created, and so is the file itself
        if it does not exist yet.

        Args:
            name: The file's path inside the repository.
            key: Where to put the value, e.g. ``"database.port"``.
            value: The new value.
            message: The commit message. Defaults to
                ``"Set <key> in <name>"``.

        Returns:
            The new commit's SHA, or ``None`` if the value was already set.

        Raises:
            InvalidConfigError: If the key is malformed, the file's top level
                is a list, or part of the key is a plain value rather than a
                section.
        """
        parts = self._split_key(name, key)
        try:
            data = self.read(name)
        except ConfigNotFound:
            data = {}
        if not isinstance(data, dict):
            raise InvalidConfigError(
                Path(name), "top level is a list, so it has no keys to set"
            )

        section = data
        for part in parts[:-1]:
            child = section.setdefault(part, {})
            if not isinstance(child, dict):
                raise InvalidConfigError(
                    Path(name), f"'{part}' holds a value, not a section"
                )
            section = child
        section[parts[-1]] = value

        return self.save(name, data, message or f"Set {key} in {name}")

    def history(self, name: str) -> list[CommitInfo]:
        """List the commits on the current branch that changed a config file.

        Args:
            name: The file's path inside the repository, e.g. ``"app.yaml"``.

        Returns:
            The commits, newest first.

        Raises:
            InvalidConfigError: If ``name`` points outside the repository.
            ConfigNotFound: If the file has never been committed on this branch.
        """
        relative_path = self._resolve_name(name)
        commits = self._repo.file_history(relative_path)
        if not commits:
            raise ConfigNotFound(relative_path)
        return commits

    def rollback(
        self, name: str, revision: str, message: str | None = None
    ) -> str | None:
        """Restore a config file to an earlier version, as a new commit.

        History is never rewritten: the rollback is recorded as a new commit
        on the current branch, so it can itself be undone later.

        Args:
            name: The file's path inside the repository, e.g. ``"app.yaml"``.
            revision: The commit to go back to, usually a short commit ID
                taken from :meth:`history`.
            message: The commit message. Defaults to
                ``"Roll back <name> to <short id>"``.

        Returns:
            The new commit's SHA, or ``None`` if the file already matched
            that version.

        Raises:
            InvalidConfigError: If ``name`` points outside the repository.
            CommitNotFoundError: If the commit is unknown or not on this branch.
            ConfigNotFound: If the file did not exist in that commit.
            BranchError: If there are uncommitted changes or a merge is in
                progress.
        """
        relative_path = self._resolve_name(name)
        source = self._repo.restore_file(relative_path, revision)
        if message is None:
            message = f"Roll back {relative_path.as_posix()} to {source.short_sha}"
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
        try:
            sha = self._repo.merge(
                branch, MAIN_BRANCH, message or f"Publish draft {name}"
            )
        except MergeConflictError as error:
            raise MergeConflictError(
                branch,
                error.conflicted_files,
                "The publish was cancelled and main is unchanged. Sync the draft "
                "with main, fix the conflicts there, then publish again.",
            ) from error
        self._repo.delete_branch(branch)
        return sha

    def sync_draft(self, name: str) -> str:
        """Bring the latest changes from ``main`` into a draft.

        Use this when publishing reports a conflict. The conflict then happens
        on the draft, where it is safe to fix, instead of on ``main``. The
        draft is left checked out.

        Args:
            name: The draft to update.

        Returns:
            The SHA of the merge commit on the draft.

        Raises:
            BranchError: If the draft does not exist, is already up to date
                with ``main``, or there are uncommitted changes.
            MergeConflictError: If the draft and ``main`` changed the same
                lines. The merge is left in progress on the draft so the
                conflicts can be fixed, then completed with :meth:`finish_sync`
                or undone with :meth:`abort_sync`.
        """
        branch = DRAFT_PREFIX + name
        try:
            return self._repo.merge(
                MAIN_BRANCH,
                branch,
                f"Sync draft {name} with main",
                keep_conflicts=True,
            )
        except MergeConflictError as error:
            raise MergeConflictError(
                branch,
                error.conflicted_files,
                "Open these files in the draft, keep the values you want and "
                "delete the conflict markers (<<<<<<<, =======, >>>>>>>), then "
                "finish the sync. Or abort the sync to undo it.",
            ) from error

    def finish_sync(self) -> str:
        """Commit a sync once its conflicts have been fixed by hand.

        Every previously conflicted configuration file is checked first: it
        must have no conflict markers left and must be valid JSON/YAML.

        Returns:
            The SHA of the merge commit on the draft.

        Raises:
            BranchError: If no sync is in progress.
            MergeConflictError: If conflict markers are still present.
            InvalidConfigError: If a fixed file is not valid JSON/YAML.
        """
        return self._repo.finish_merge(validate=self._validate_config)

    def abort_sync(self) -> None:
        """Undo a sync that stopped on a conflict, restoring the draft.

        Raises:
            BranchError: If no sync is in progress.
        """
        self._repo.abort_merge()

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

    @staticmethod
    def _split_key(name: str, key: str) -> list[str]:
        """Split ``"a.b.c"`` into ``["a", "b", "c"]``, rejecting empty parts."""
        parts = key.split(".")
        if not all(parts):
            raise InvalidConfigError(Path(name), f"invalid key '{key}'")
        return parts

    def _validate_config(self, path: Path) -> None:
        """Raise InvalidConfigError if a supported config file cannot be parsed."""
        if self._parser.supports(path):
            self._parser.read(path)

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
