"""Wrap the Git operations the configuration tool needs."""

from __future__ import annotations

from pathlib import Path

from git import GitCommandError, InvalidGitRepositoryError, NoSuchPathError, Repo

from config_cli.exceptions import BranchError, InvalidRepoError

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

# Store and check out every text file with LF endings, overriding a machine's
# core.autocrlf setting, so configs look identical on every OS.
DEFAULT_GITATTRIBUTES = "* text=auto eol=lf\n"

_INITIAL_FILES = {
    ".gitignore": DEFAULT_GITIGNORE,
    ".gitattributes": DEFAULT_GITATTRIBUTES,
}


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

        The repository starts on the ``main`` branch with ``.gitignore`` and
        ``.gitattributes`` committed, so ``main`` exists before any
        configuration is added and line endings are consistent on every OS.

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

        for filename, content in _INITIAL_FILES.items():
            (path / filename).write_text(content, encoding="utf-8", newline="\n")
        repo.index.add(list(_INITIAL_FILES))
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

    def commit_files(self, paths: list[Path], message: str) -> str | None:
        """Stage the given files and commit them.

        Args:
            paths: Files to commit, relative to the repository root.
            message: The commit message.

        Returns:
            The new commit's SHA, or ``None`` if the files had no changes,
            in which case no commit is made.
        """
        self._repo.index.add([path.as_posix() for path in paths])
        if not self._repo.index.diff("HEAD"):
            return None
        return self._repo.index.commit(message).hexsha

    def create_branch(self, name: str, start_point: str = MAIN_BRANCH) -> None:
        """Create a new branch from ``start_point`` and check it out.

        Args:
            name: The new branch's name.
            start_point: The existing branch to start from.

        Raises:
            BranchError: If the name is invalid, the branch already exists,
                ``start_point`` does not exist, or there are uncommitted changes.
        """
        self._validate_branch_name(name)
        if self.branch_exists(name):
            raise BranchError(name, "branch already exists")
        if not self.branch_exists(start_point):
            raise BranchError(start_point, "branch does not exist")
        self._ensure_clean(name)

        self._repo.create_head(name, self._repo.heads[start_point].commit).checkout()

    def checkout(self, name: str) -> None:
        """Switch to an existing branch.

        Args:
            name: The branch to check out.

        Raises:
            BranchError: If the branch does not exist or there are uncommitted
                changes that switching would put at risk.
        """
        if not self.branch_exists(name):
            raise BranchError(name, "branch does not exist")
        self._ensure_clean(name)

        self._repo.heads[name].checkout()

    def branch_exists(self, name: str) -> bool:
        """Return whether a local branch called ``name`` exists."""
        return name in self._repo.heads

    def list_branches(self) -> list[str]:
        """Return the names of all local branches, sorted alphabetically."""
        return sorted(head.name for head in self._repo.heads)

    def _validate_branch_name(self, name: str) -> None:
        """Raise BranchError if Git would reject ``name`` as a branch name."""
        try:
            self._repo.git.check_ref_format("--branch", name)
        except GitCommandError as error:
            raise BranchError(name, "invalid branch name") from error

    def _ensure_clean(self, branch: str) -> None:
        """Raise BranchError if the working tree has uncommitted changes."""
        if self._repo.is_dirty(untracked_files=True):
            raise BranchError(
                branch,
                "the repository has uncommitted changes; "
                "commit or discard them first",
            )

    @property
    def path(self) -> Path:
        """The repository's root folder."""
        return Path(self._repo.working_dir)

    @property
    def current_branch(self) -> str:
        """The name of the branch that is currently checked out."""
        return self._repo.active_branch.name
