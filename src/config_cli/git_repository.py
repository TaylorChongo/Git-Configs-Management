"""Wrap the Git operations the configuration tool needs."""

from __future__ import annotations

import logging
import re
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import TracebackType

from git import (
    BadName,
    Commit,
    GitCommandError,
    InvalidGitRepositoryError,
    NoSuchPathError,
    Repo,
)

from config_cli.audit import audit
from config_cli.exceptions import (
    BranchError,
    CommitNotFoundError,
    ConfigNotFound,
    InvalidRepoError,
    MergeConflictError,
)

_log = logging.getLogger(__name__)

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

# The lines Git writes into a file to mark a conflict: <<<<<<<, ======= and >>>>>>>.
_CONFLICT_MARKER = re.compile(rb"^(<{7}|={7}|>{7})( |\r?$)", re.MULTILINE)


@dataclass(frozen=True)
class CommitInfo:
    """A plain summary of one commit, safe to pass outside this module.

    Attributes:
        sha: The full commit ID.
        message: The first line of the commit message.
        author: The name of the commit's author.
        date: When the commit was made.
    """

    sha: str
    message: str
    author: str
    date: datetime

    @property
    def short_sha(self) -> str:
        """The first 7 characters of the commit ID, as Git usually shows it."""
        return self.sha[:7]

    @classmethod
    def from_commit(cls, commit: Commit) -> CommitInfo:
        """Build a CommitInfo from a GitPython commit object."""
        summary = commit.summary
        if isinstance(summary, bytes):
            summary = summary.decode("utf-8", errors="replace")
        return cls(
            sha=commit.hexsha,
            message=summary,
            author=commit.author.name or "",
            date=commit.committed_datetime,
        )


class GitRepository:
    """A Git repository used as the versioned store for configuration files.

    Create instances with :meth:`init`, :meth:`open` or :meth:`clone` rather
    than calling the constructor directly. Instances can be used as context
    managers (``with GitRepository.open(path) as repo: ...``) to release
    Git's file handles as soon as the block ends.
    """

    def __init__(self, repo: Repo) -> None:
        """Wrap an existing GitPython ``Repo`` object.

        Args:
            repo: The GitPython repository to wrap.
        """
        self._repo = repo

    def __enter__(self) -> GitRepository:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        """Release the background Git processes and open files GitPython holds."""
        self._repo.close()

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
        commit = repo.index.commit("Initialize configuration repository")
        audit(_log, "repository_initialized", path=str(path), sha=commit.hexsha)
        return cls(repo)

    @classmethod
    def clone(cls, source: str, path: Path, branch: str | None = None) -> GitRepository:
        """Clone an existing configuration repository.

        Args:
            source: A URL or local path of the repository to copy.
            path: The folder to clone into. It must not exist or be empty.
            branch: The branch to check out. Defaults to the source's default.

        Returns:
            The new local copy.

        Raises:
            InvalidRepoError: If ``path`` is not empty or the clone fails.
        """
        if path.exists() and any(path.iterdir()):
            raise InvalidRepoError(path, "the target folder is not empty")
        options = [f"--branch={branch}"] if branch is not None else None
        try:
            repo = Repo.clone_from(source, path, multi_options=options)
        except GitCommandError as error:
            raise InvalidRepoError(path, f"could not clone '{source}'") from error
        audit(_log, "repository_cloned", source=source, path=str(path), branch=branch)
        return cls(repo)

    @contextmanager
    def temporary_clone(self, branch: str | None = None) -> Iterator[GitRepository]:
        """Clone this repository into a temporary folder for the ``with`` block.

        The clone can be read or changed freely without touching this
        repository's working files or checked-out branch. It is closed and
        deleted when the block ends, even if an exception is raised.

        Args:
            branch: The branch to check out in the clone. Defaults to the
                branch that is checked out here.

        Yields:
            The temporary clone.

        Raises:
            BranchError: If ``branch`` does not exist.
        """
        branch = branch if branch is not None else self.current_branch
        if not self.branch_exists(branch):
            raise BranchError(branch, "branch does not exist")

        # ignore_cleanup_errors: on Windows a virus scanner or indexer can
        # briefly lock a file; a leftover temp folder is better than a crash.
        with tempfile.TemporaryDirectory(
            prefix="config-cli-", ignore_cleanup_errors=True
        ) as folder:
            clone = GitRepository.clone(str(self.path), Path(folder) / "repo", branch)
            try:
                yield clone
            finally:
                clone.close()

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

        Raises:
            BranchError: If a merge is in progress. A normal commit then would
                silently drop the merge, so it has to be finished first.
        """
        self._ensure_no_merge_in_progress()
        self._repo.index.add([path.as_posix() for path in paths])
        files = [path.as_posix() for path in paths]
        if not self._repo.index.diff("HEAD"):
            audit(_log, "commit_skipped", branch=self.current_branch, files=files)
            return None
        sha = self._repo.index.commit(message).hexsha
        audit(
            _log,
            "committed",
            branch=self.current_branch,
            sha=sha,
            files=files,
            message=message,
        )
        return sha

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
        audit(_log, "branch_created", branch=name, start_point=start_point)

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
        audit(_log, "branch_checked_out", branch=name)

    def merge(
        self, source: str, target: str, message: str, keep_conflicts: bool = False
    ) -> str:
        """Merge ``source`` into ``target`` with a merge commit.

        ``target`` is left checked out on success. On a conflict, what happens
        depends on ``keep_conflicts``:

        * ``False`` (default): the merge is cancelled and the originally
          checked-out branch is restored, so nothing changes.
        * ``True``: the merge is left in progress on ``target``, with conflict
          markers in the files, so the user can fix them and then call
          :meth:`finish_merge` or :meth:`abort_merge`.

        Any other Git failure is always cancelled.

        Args:
            source: The branch whose changes are merged in.
            target: The branch that receives the changes.
            message: The merge commit's message.
            keep_conflicts: Leave a conflicted merge in progress instead of
                cancelling it.

        Returns:
            The SHA of the new merge commit.

        Raises:
            BranchError: If either branch does not exist, ``source`` has no new
                commits, there are uncommitted changes, or Git fails for a
                reason other than a conflict.
            MergeConflictError: If the branches changed the same lines.
        """
        for branch in (source, target):
            if not self.branch_exists(branch):
                raise BranchError(branch, "branch does not exist")
        self._ensure_clean(source)

        source_commit = self._repo.heads[source].commit
        target_commit = self._repo.heads[target].commit
        if self._repo.is_ancestor(source_commit, target_commit):
            raise BranchError(source, f"has no new commits to merge into '{target}'")

        original_branch = self.current_branch
        self._repo.heads[target].checkout()
        try:
            # --no-ff always records a merge commit, so the history shows
            # exactly when a branch was merged, even if Git could fast-forward.
            self._repo.git.merge("--no-ff", "-m", message, source)
        except GitCommandError as error:
            conflicted_files = self.conflicted_files()
            audit(
                _log,
                "merge_conflict" if conflicted_files else "merge_failed",
                logging.WARNING if conflicted_files else logging.ERROR,
                source=source,
                target=target,
                files=conflicted_files,
                kept_open=bool(conflicted_files and keep_conflicts),
            )
            if conflicted_files and keep_conflicts:
                raise MergeConflictError(
                    source,
                    conflicted_files,
                    "Edit these files to keep the right values and remove the "
                    "conflict markers (<<<<<<<, =======, >>>>>>>), then finish "
                    "the merge, or abort it to go back.",
                ) from error

            self._cancel_merge()
            self._repo.heads[original_branch].checkout()
            if conflicted_files:
                raise MergeConflictError(
                    source,
                    conflicted_files,
                    f"The merge was cancelled, so '{target}' is unchanged.",
                ) from error
            raise BranchError(source, f"could not be merged into '{target}'") from error

        sha = self._repo.head.commit.hexsha
        audit(_log, "merged", source=source, target=target, sha=sha)
        return sha

    def finish_merge(self, validate: Callable[[Path], None] | None = None) -> str:
        """Commit a merge whose conflicts the user has fixed by hand.

        Args:
            validate: Optional check run on each previously conflicted file
                (given as a full path) before anything is committed. It should
                raise an exception if the file is not acceptable.

        Returns:
            The SHA of the merge commit.

        Raises:
            BranchError: If no merge is in progress.
            MergeConflictError: If a conflicted file still contains conflict
                markers. Nothing is committed in that case.
            Exception: Whatever ``validate`` raises. Nothing is committed.
        """
        if not self.merge_in_progress:
            raise BranchError(self.current_branch, "no merge is in progress")

        files = self.conflicted_files()
        unresolved = [name for name in files if self._has_conflict_markers(name)]
        if unresolved:
            raise MergeConflictError(
                self.current_branch,
                unresolved,
                "These files still contain conflict markers "
                "(<<<<<<<, =======, >>>>>>>). Remove them, then finish again.",
            )
        if validate is not None:
            for name in files:
                if (self.path / name).exists():
                    validate(self.path / name)

        if files:
            self._repo.git.add("--", *files)
        # --no-edit reuses the message Git saved when the merge started.
        self._repo.git.commit("--no-edit")
        sha = self._repo.head.commit.hexsha
        audit(_log, "merge_finished", branch=self.current_branch, sha=sha, files=files)
        return sha

    def abort_merge(self) -> None:
        """Cancel the merge in progress, restoring the files to before it began.

        Raises:
            BranchError: If no merge is in progress.
        """
        if not self.merge_in_progress:
            raise BranchError(self.current_branch, "no merge is in progress")
        self._cancel_merge()
        audit(_log, "merge_aborted", branch=self.current_branch)

    def conflicted_files(self) -> list[str]:
        """Return the files Git could not merge automatically, sorted.

        The list is empty when no merge is in progress. Files stay on the
        list until the merge is finished, even after they have been edited.
        """
        output = self._repo.git.diff("--name-only", "--diff-filter=U")
        return sorted(output.splitlines())

    def file_history(self, path: Path) -> list[CommitInfo]:
        """Return the commits on the current branch that changed a file.

        Args:
            path: The file, relative to the repository root.

        Returns:
            The commits, newest first. Empty if the file was never committed.
        """
        commits = self._repo.iter_commits("HEAD", paths=path.as_posix())
        return [CommitInfo.from_commit(commit) for commit in commits]

    def restore_file(self, path: Path, revision: str) -> CommitInfo:
        """Put a file back to how it was in an earlier commit.

        The restored file is written to disk and staged, but not committed,
        so the caller decides the commit message.

        Args:
            path: The file, relative to the repository root.
            revision: The commit to take the file from: a full or short
                commit ID, or anything else Git accepts, such as ``HEAD~1``.

        Returns:
            The commit the file was restored from.

        Raises:
            BranchError: If there are uncommitted changes or a merge is in
                progress.
            CommitNotFoundError: If the commit does not exist or is not part of
                the current branch's history.
            ConfigNotFound: If the file did not exist in that commit.
        """
        self._ensure_clean(self.current_branch)
        commit = self._resolve_commit(revision)
        try:
            commit.tree / path.as_posix()
        except KeyError as error:
            raise ConfigNotFound(path) from error

        # `git checkout <commit> -- <file>` copies one file out of an old
        # commit without moving the branch, so history is never rewritten.
        self._repo.git.checkout(commit.hexsha, "--", path.as_posix())
        audit(
            _log,
            "file_restored",
            branch=self.current_branch,
            file=path.as_posix(),
            from_commit=commit.hexsha,
        )
        return CommitInfo.from_commit(commit)

    def delete_branch(self, name: str) -> None:
        """Delete a branch whose changes are already merged.

        Args:
            name: The branch to delete.

        Raises:
            BranchError: If the branch does not exist, is checked out, or has
                commits that have not been merged yet.
        """
        if not self.branch_exists(name):
            raise BranchError(name, "branch does not exist")
        if name == self.current_branch:
            raise BranchError(name, "cannot delete the branch that is checked out")
        try:
            # force=False matches `git branch -d`: Git refuses to delete a
            # branch whose work would be lost.
            self._repo.delete_head(name, force=False)
        except GitCommandError as error:
            raise BranchError(name, "has changes that are not merged") from error
        audit(_log, "branch_deleted", branch=name)

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

    def _resolve_commit(self, revision: str) -> Commit:
        """Find a commit on the current branch from a user-supplied revision.

        Raises:
            CommitNotFoundError: If the revision is unknown, is not a commit,
                or belongs only to another branch.
        """
        try:
            commit = self._repo.commit(revision)
        except (BadName, ValueError) as error:
            raise CommitNotFoundError(revision, "no such commit") from error

        if not self._repo.is_ancestor(commit, self._repo.head.commit):
            raise CommitNotFoundError(
                revision, f"is not in the history of '{self.current_branch}'"
            )
        return commit

    def _cancel_merge(self) -> None:
        """Cancel a merge in progress, if there is one."""
        if self.merge_in_progress:
            self._repo.git.merge("--abort")

    def _has_conflict_markers(self, name: str) -> bool:
        """Return whether a file still contains Git's conflict marker lines."""
        path = self.path / name
        return path.exists() and bool(_CONFLICT_MARKER.search(path.read_bytes()))

    def _ensure_no_merge_in_progress(self) -> None:
        """Raise BranchError if a merge has been started but not finished."""
        if self.merge_in_progress:
            raise BranchError(
                self.current_branch,
                "a merge is in progress; finish or abort it first",
            )

    def _ensure_clean(self, branch: str) -> None:
        """Raise BranchError if a merge is unfinished or files are uncommitted."""
        self._ensure_no_merge_in_progress()
        if self._repo.is_dirty(untracked_files=True):
            raise BranchError(
                branch,
                "the repository has uncommitted changes; "
                "commit or discard them first",
            )

    @property
    def merge_in_progress(self) -> bool:
        """Whether a merge has been started but not yet finished or aborted.

        Git records an unfinished merge in a ``.git/MERGE_HEAD`` file.
        """
        return (Path(self._repo.git_dir) / "MERGE_HEAD").exists()

    @property
    def path(self) -> Path:
        """The repository's root folder."""
        return Path(self._repo.working_dir)

    @property
    def current_branch(self) -> str:
        """The name of the branch that is currently checked out."""
        return self._repo.active_branch.name
