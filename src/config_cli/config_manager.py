"""Combine the config parser and Git repository into configuration actions."""

from __future__ import annotations

from pathlib import Path

from config_cli.config_parser import ConfigData, ConfigParser
from config_cli.exceptions import InvalidConfigError
from config_cli.git_repository import GitRepository


class ConfigManager:
    """Save configuration files into a Git repository as commits.

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
