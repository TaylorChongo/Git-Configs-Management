"""Wrap the Git operations the configuration tool needs."""

from __future__ import annotations

from pathlib import Path

from git import InvalidGitRepositoryError, NoSuchPathError, Repo

from config_cli.exceptions import InvalidRepoError

MAIN_BRANCH = "main"

# Editor backups and OS clutter that should never be versioned as configuration.
DEFAULT_GITIGNORE = """\
*.bak
*.swp
*.tmp
*~
.DS_Store
Thumbs.db
"""


class GitRepository:
    """A Git repository used as the versioned store for configuration files.

    Create instances with :meth:`init` for a new repository or :meth:`open`
    for an existing one, rather than calling the constructor directly.
    """

    def __init__(self, repo: Repo) -> None:
        """Wrap an existing GitPython ``Repo`` object.

        Args:
            repo: The GitPython repository to wrap.
        """
        self._repo = repo

    @classmethod
    def init(cls, path: Path) -> GitRepository:
        """Create a new configuration repository.

        The repository starts on the ``main`` branch with a ``.gitignore``
        file committed, so ``main`` exists before any configuration is added.

        Args:
            path: Folder to create the repository in. It is created if missing.

        Returns:
            The newly initialized repository.

        Raises:
            InvalidRepoError: If ``path`` is already a Git repository.
        """
        if (path / ".git").exists():
            raise InvalidRepoError(path, "a Git repository already exists here")

        path.mkdir(parents=True, exist_ok=True)
        repo = Repo.init(path, initial_branch=MAIN_BRANCH)

        (path / ".gitignore").write_text(DEFAULT_GITIGNORE, encoding="utf-8")
        repo.index.add([".gitignore"])
        repo.index.commit("Initialize configuration repository")
        return cls(repo)

    @classmethod
    def open(cls, path: Path) -> GitRepository:
        """Open an existing configuration repository.

        Args:
            path: The repository's root folder.

        Returns:
            The opened repository.

        Raises:
            InvalidRepoError: If ``path`` does not exist or is not the root of
                a Git repository.
        """
        try:
            repo = Repo(path)
        except NoSuchPathError as error:
            raise InvalidRepoError(path, "path does not exist") from error
        except InvalidGitRepositoryError as error:
            raise InvalidRepoError(path, "not a Git repository") from error
        return cls(repo)

    @property
    def path(self) -> Path:
        """The repository's root folder."""
        return Path(self._repo.working_dir)

    @property
    def current_branch(self) -> str:
        """The name of the branch that is currently checked out."""
        return self._repo.active_branch.name
