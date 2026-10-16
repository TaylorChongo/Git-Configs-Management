"""Tests for creating and opening configuration repositories."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest
from git import GitCommandError, Repo

from config_cli.exceptions import (
    BranchError,
    CommitNotFoundError,
    ConfigNotFound,
    InvalidRepoError,
    MergeConflictError,
)
from config_cli.git_repository import (
    DEFAULT_GITATTRIBUTES,
    DEFAULT_GITIGNORE,
    MAIN_BRANCH,
    CommitInfo,
    GitRepository,
)

# These tests check the result with GitPython's own ``Repo`` instead of
# GitRepository, so they confirm the real Git state rather than trusting
# the code under test to report on itself.


def test_init_creates_repository_on_main(config_repo: GitRepository) -> None:
    """A new repository should be a real Git repo checked out on main."""
    repo = Repo(config_repo.path)

    assert (config_repo.path / ".git").is_dir()
    assert repo.active_branch.name == MAIN_BRANCH
    assert config_repo.current_branch == MAIN_BRANCH


@pytest.mark.parametrize(
    ("filename", "content"),
    [(".gitignore", DEFAULT_GITIGNORE), (".gitattributes", DEFAULT_GITATTRIBUTES)],
)
def test_init_makes_one_commit_with_repo_files(
    config_repo: GitRepository, filename: str, content: str
) -> None:
    """main should start with one commit holding .gitignore and .gitattributes."""
    repo = Repo(config_repo.path)
    commits = list(repo.iter_commits(MAIN_BRANCH))

    assert len(commits) == 1
    assert commits[0].message == "Initialize configuration repository"
    # Compare raw bytes: reading as text on Windows would hide \r\n endings.
    expected = content.encode("utf-8")
    assert (commits[0].tree / filename).data_stream.read() == expected
    assert (config_repo.path / filename).read_bytes() == expected


def test_checkout_keeps_lf_line_endings(config_repo: GitRepository) -> None:
    """Switching branches must not convert files to CRLF, even on Windows."""
    (config_repo.path / "app.yaml").write_bytes(b"port: 80\n")
    config_repo.commit_files([Path("app.yaml")], "Add app.yaml")
    config_repo.create_branch("feature")
    (config_repo.path / "app.yaml").write_bytes(b"port: 81\n")
    config_repo.commit_files([Path("app.yaml")], "Change port")

    config_repo.checkout(MAIN_BRANCH)

    assert (config_repo.path / "app.yaml").read_bytes() == b"port: 80\n"


def test_init_leaves_working_tree_clean(config_repo: GitRepository) -> None:
    """After init there should be nothing uncommitted."""
    assert not Repo(config_repo.path).is_dirty(untracked_files=True)


def test_init_creates_missing_folder(tmp_path: Path) -> None:
    """init should create the target folder, including missing parents."""
    path = tmp_path / "a" / "b" / "configs"

    GitRepository.init(path)

    assert (path / ".git").is_dir()


def test_init_refuses_existing_repository(config_repo: GitRepository) -> None:
    """Running init twice must not overwrite an existing repository."""
    with pytest.raises(InvalidRepoError, match="already exists"):
        GitRepository.init(config_repo.path)


def test_open_existing_repository(config_repo: GitRepository) -> None:
    """open should load a repository created earlier."""
    reopened = GitRepository.open(config_repo.path)

    assert reopened.path == config_repo.path
    assert reopened.current_branch == MAIN_BRANCH


def test_open_missing_path_raises_invalid_repo(tmp_path: Path) -> None:
    """Opening a folder that does not exist should raise InvalidRepoError."""
    path = tmp_path / "nowhere"

    with pytest.raises(InvalidRepoError, match="does not exist") as excinfo:
        GitRepository.open(path)

    assert excinfo.value.path == path


def test_open_plain_folder_raises_invalid_repo(tmp_path: Path) -> None:
    """Opening an ordinary folder should raise InvalidRepoError."""
    with pytest.raises(InvalidRepoError, match="not a Git repository"):
        GitRepository.open(tmp_path)


def test_open_subfolder_of_repository_raises_invalid_repo(
    config_repo: GitRepository,
) -> None:
    """Only the repository root is accepted, not a folder inside it."""
    subfolder = config_repo.path / "nested"
    subfolder.mkdir()

    with pytest.raises(InvalidRepoError, match="not a Git repository"):
        GitRepository.open(subfolder)


def test_commit_files_records_file_in_new_commit(config_repo: GitRepository) -> None:
    """commit_files should create a commit containing the file's content."""
    (config_repo.path / "app.yaml").write_bytes(b"port: 80\n")

    sha = config_repo.commit_files([Path("app.yaml")], "Add app.yaml")

    head = Repo(config_repo.path).head.commit
    assert sha == head.hexsha
    assert head.message == "Add app.yaml"
    assert (head.tree / "app.yaml").data_stream.read() == b"port: 80\n"


def test_commit_files_skips_commit_when_nothing_changed(
    config_repo: GitRepository,
) -> None:
    """Committing an unchanged file should not create an empty commit."""
    (config_repo.path / "app.yaml").write_bytes(b"port: 80\n")
    config_repo.commit_files([Path("app.yaml")], "Add app.yaml")

    sha = config_repo.commit_files([Path("app.yaml")], "Again")

    assert sha is None
    assert len(list(Repo(config_repo.path).iter_commits())) == 2


def test_create_branch_starts_from_main_and_checks_it_out(
    config_repo: GitRepository,
) -> None:
    """A new branch should point at main's latest commit and become active."""
    repo = Repo(config_repo.path)
    main_commit = repo.heads[MAIN_BRANCH].commit

    config_repo.create_branch("feature")

    assert repo.active_branch.name == "feature"
    assert repo.heads["feature"].commit == main_commit


def test_create_branch_refuses_existing_name(config_repo: GitRepository) -> None:
    """Creating a branch twice must not reset the existing one."""
    config_repo.create_branch("feature")

    with pytest.raises(BranchError, match="already exists"):
        config_repo.create_branch("feature")


def test_create_branch_requires_existing_start_point(
    config_repo: GitRepository,
) -> None:
    """The branch to start from has to exist."""
    with pytest.raises(BranchError, match="does not exist") as excinfo:
        config_repo.create_branch("feature", start_point="nope")

    assert excinfo.value.branch == "nope"


@pytest.mark.parametrize("name", ["has space", "double..dot", "ends/", "-dash", ""])
def test_create_branch_rejects_invalid_names(
    config_repo: GitRepository, name: str
) -> None:
    """Names Git does not allow should raise BranchError, not a Git error."""
    with pytest.raises(BranchError, match="invalid branch name"):
        config_repo.create_branch(name)

    assert config_repo.list_branches() == [MAIN_BRANCH]


def test_checkout_switches_branch(config_repo: GitRepository) -> None:
    """checkout should make the named branch the active one."""
    config_repo.create_branch("feature")

    config_repo.checkout(MAIN_BRANCH)

    assert Repo(config_repo.path).active_branch.name == MAIN_BRANCH


def test_checkout_missing_branch_raises(config_repo: GitRepository) -> None:
    """Switching to a branch that does not exist should raise BranchError."""
    with pytest.raises(BranchError, match="does not exist"):
        config_repo.checkout("nope")


@pytest.mark.parametrize("action", ["create", "checkout"])
def test_branch_switch_refuses_uncommitted_changes(
    config_repo: GitRepository, action: str
) -> None:
    """Switching branches with unsaved edits could lose them, so refuse."""
    config_repo.create_branch("feature")
    config_repo.checkout(MAIN_BRANCH)
    (config_repo.path / "scratch.yaml").write_bytes(b"a: 1\n")

    with pytest.raises(BranchError, match="uncommitted changes"):
        if action == "create":
            config_repo.create_branch("other")
        else:
            config_repo.checkout("feature")

    assert config_repo.current_branch == MAIN_BRANCH
    assert (config_repo.path / "scratch.yaml").exists()


def test_list_branches_is_sorted(config_repo: GitRepository) -> None:
    """list_branches should return every local branch in alphabetical order."""
    config_repo.create_branch("zeta")
    config_repo.create_branch("alpha")

    assert config_repo.list_branches() == ["alpha", MAIN_BRANCH, "zeta"]


# --- Merging ---------------------------------------------------------------


def commit_file(repo: GitRepository, name: str, content: bytes, message: str) -> None:
    """Write a file and commit it on the current branch."""
    (repo.path / name).write_bytes(content)
    repo.commit_files([Path(name)], message)


@pytest.fixture
def diverged_repo(config_repo: GitRepository) -> GitRepository:
    """A repo where 'feature' and main both changed the same line of app.yaml."""
    commit_file(config_repo, "app.yaml", b"port: 80\n", "Add app.yaml")
    config_repo.create_branch("feature")
    commit_file(config_repo, "app.yaml", b"port: 81\n", "Feature port")
    config_repo.checkout(MAIN_BRANCH)
    commit_file(config_repo, "app.yaml", b"port: 82\n", "Main port")
    config_repo.checkout("feature")
    return config_repo


def test_merge_creates_merge_commit_on_target(config_repo: GitRepository) -> None:
    """A clean merge should add a two-parent merge commit to the target."""
    config_repo.create_branch("feature")
    commit_file(config_repo, "app.yaml", b"port: 80\n", "Add app.yaml")
    repo = Repo(config_repo.path)
    feature_commit = repo.heads["feature"].commit
    main_commit = repo.heads[MAIN_BRANCH].commit

    sha = config_repo.merge("feature", MAIN_BRANCH, "Merge feature")

    head = repo.heads[MAIN_BRANCH].commit
    assert sha == head.hexsha
    assert head.message.strip() == "Merge feature"
    assert set(head.parents) == {main_commit, feature_commit}
    assert (head.tree / "app.yaml").data_stream.read() == b"port: 80\n"
    assert config_repo.current_branch == MAIN_BRANCH


def test_merge_conflict_raises_and_leaves_repo_unchanged(
    diverged_repo: GitRepository,
) -> None:
    """A conflict must be reported, and every branch left as it was."""
    repo = Repo(diverged_repo.path)
    main_before = repo.heads[MAIN_BRANCH].commit
    feature_before = repo.heads["feature"].commit

    with pytest.raises(MergeConflictError) as excinfo:
        diverged_repo.merge("feature", MAIN_BRANCH, "Merge feature")

    assert excinfo.value.branch == "feature"
    assert excinfo.value.conflicted_files == ["app.yaml"]
    assert repo.heads[MAIN_BRANCH].commit == main_before
    assert repo.heads["feature"].commit == feature_before
    assert diverged_repo.current_branch == "feature"
    assert not (Path(repo.git_dir) / "MERGE_HEAD").exists()
    assert not repo.is_dirty(untracked_files=True)
    assert (diverged_repo.path / "app.yaml").read_bytes() == b"port: 81\n"


def test_merge_other_git_failure_raises_branch_error(
    config_repo: GitRepository,
) -> None:
    """A Git failure that is not a conflict should still be reported cleanly.

    Such failures are hard to cause for real, so the Git command is replaced
    with a mock that fails the way Git would.
    """
    config_repo.create_branch("feature")
    commit_file(config_repo, "app.yaml", b"port: 80\n", "Add app.yaml")
    main_before = Repo(config_repo.path).heads[MAIN_BRANCH].commit
    git_failure = GitCommandError("git merge", 128, b"fatal: something broke")
    git_class = type(Repo(config_repo.path).git)

    with mock.patch.object(git_class, "merge", create=True, side_effect=git_failure):
        with pytest.raises(BranchError, match="could not be merged") as excinfo:
            config_repo.merge("feature", MAIN_BRANCH, "Merge feature")

    assert excinfo.value.__cause__ is git_failure
    assert Repo(config_repo.path).heads[MAIN_BRANCH].commit == main_before
    assert config_repo.current_branch == "feature"


def test_merge_with_nothing_new_raises(config_repo: GitRepository) -> None:
    """Merging a branch that adds no commits would be a pointless merge."""
    config_repo.create_branch("feature")

    with pytest.raises(BranchError, match="no new commits"):
        config_repo.merge("feature", MAIN_BRANCH, "Merge feature")


@pytest.mark.parametrize(("source", "target"), [("nope", MAIN_BRANCH), ("x", "nope")])
def test_merge_missing_branch_raises(
    config_repo: GitRepository, source: str, target: str
) -> None:
    """Both branches in a merge have to exist."""
    config_repo.create_branch("x")

    with pytest.raises(BranchError, match="does not exist") as excinfo:
        config_repo.merge(source, target, "Merge")

    assert excinfo.value.branch == "nope"


def test_merge_refuses_uncommitted_changes(config_repo: GitRepository) -> None:
    """Merging with unsaved edits could mix them into the merge, so refuse."""
    config_repo.create_branch("feature")
    commit_file(config_repo, "app.yaml", b"port: 80\n", "Add app.yaml")
    (config_repo.path / "scratch.yaml").write_bytes(b"a: 1\n")

    with pytest.raises(BranchError, match="uncommitted changes"):
        config_repo.merge("feature", MAIN_BRANCH, "Merge feature")


def test_delete_branch_removes_merged_branch(config_repo: GitRepository) -> None:
    """A branch that has been merged can be deleted."""
    config_repo.create_branch("feature")
    commit_file(config_repo, "app.yaml", b"port: 80\n", "Add app.yaml")
    config_repo.merge("feature", MAIN_BRANCH, "Merge feature")

    config_repo.delete_branch("feature")

    assert config_repo.list_branches() == [MAIN_BRANCH]


def test_delete_branch_refuses_unmerged_work(config_repo: GitRepository) -> None:
    """Deleting a branch with unmerged commits would lose work, so refuse."""
    config_repo.create_branch("feature")
    commit_file(config_repo, "app.yaml", b"port: 80\n", "Add app.yaml")
    config_repo.checkout(MAIN_BRANCH)

    with pytest.raises(BranchError, match="not merged"):
        config_repo.delete_branch("feature")

    assert config_repo.branch_exists("feature")


def test_delete_branch_refuses_current_branch(config_repo: GitRepository) -> None:
    """Git cannot delete the branch you are standing on."""
    with pytest.raises(BranchError, match="checked out"):
        config_repo.delete_branch(MAIN_BRANCH)


def test_delete_missing_branch_raises(config_repo: GitRepository) -> None:
    """Deleting a branch that does not exist should raise BranchError."""
    with pytest.raises(BranchError, match="does not exist"):
        config_repo.delete_branch("nope")


# --- Resolving conflicts by hand -------------------------------------------


@pytest.fixture
def conflicted_merge(diverged_repo: GitRepository) -> GitRepository:
    """A repo with main merged into 'feature' and stopped on a conflict."""
    with pytest.raises(MergeConflictError):
        diverged_repo.merge(MAIN_BRANCH, "feature", "Sync", keep_conflicts=True)
    return diverged_repo


def test_merge_keep_conflicts_leaves_merge_in_progress(
    conflicted_merge: GitRepository,
) -> None:
    """With keep_conflicts, the conflict is left on the target for the user."""
    assert conflicted_merge.merge_in_progress
    assert conflicted_merge.current_branch == "feature"
    assert conflicted_merge.conflicted_files() == ["app.yaml"]
    content = (conflicted_merge.path / "app.yaml").read_bytes()
    assert b"<<<<<<<" in content
    assert b"port: 81" in content
    assert b"port: 82" in content


def test_finish_merge_commits_fixed_files(conflicted_merge: GitRepository) -> None:
    """After the user fixes the file, finishing creates the merge commit."""
    (conflicted_merge.path / "app.yaml").write_bytes(b"port: 90\n")
    repo = Repo(conflicted_merge.path)
    feature_before = repo.heads["feature"].commit
    main_commit = repo.heads[MAIN_BRANCH].commit

    sha = conflicted_merge.finish_merge()

    head = repo.heads["feature"].commit
    assert sha == head.hexsha
    assert set(head.parents) == {feature_before, main_commit}
    assert (head.tree / "app.yaml").data_stream.read() == b"port: 90\n"
    assert not conflicted_merge.merge_in_progress
    assert not repo.is_dirty(untracked_files=True)


def test_finish_merge_refuses_leftover_markers(
    conflicted_merge: GitRepository,
) -> None:
    """Committing a file that still has conflict markers would break it."""
    with pytest.raises(MergeConflictError, match="still contain conflict markers"):
        conflicted_merge.finish_merge()

    assert conflicted_merge.merge_in_progress


def test_finish_merge_ignores_marker_text_inside_a_line(
    conflicted_merge: GitRepository,
) -> None:
    """Only whole marker lines count, not the same characters inside a value."""
    (conflicted_merge.path / "app.yaml").write_bytes(b"note: a ======= b\n")

    conflicted_merge.finish_merge()

    assert not conflicted_merge.merge_in_progress


def test_finish_merge_runs_validation_before_committing(
    conflicted_merge: GitRepository,
) -> None:
    """A failing validation must stop the commit and keep the merge open."""
    (conflicted_merge.path / "app.yaml").write_bytes(b"port: 90\n")
    checked: list[Path] = []

    def reject(path: Path) -> None:
        checked.append(path)
        raise ValueError("not good enough")

    with pytest.raises(ValueError, match="not good enough"):
        conflicted_merge.finish_merge(validate=reject)

    assert checked == [conflicted_merge.path / "app.yaml"]
    assert conflicted_merge.merge_in_progress


def test_abort_merge_restores_target(conflicted_merge: GitRepository) -> None:
    """Aborting puts the target branch's files back as they were."""
    conflicted_merge.abort_merge()

    assert not conflicted_merge.merge_in_progress
    assert (conflicted_merge.path / "app.yaml").read_bytes() == b"port: 81\n"
    assert not Repo(conflicted_merge.path).is_dirty(untracked_files=True)


@pytest.mark.parametrize("action", ["finish", "abort"])
def test_finish_or_abort_without_merge_raises(
    config_repo: GitRepository, action: str
) -> None:
    """There is nothing to finish or abort when no merge was started."""
    with pytest.raises(BranchError, match="no merge is in progress"):
        if action == "finish":
            config_repo.finish_merge()
        else:
            config_repo.abort_merge()


def test_commit_and_checkout_refused_during_merge(
    conflicted_merge: GitRepository,
) -> None:
    """Normal work mid-merge would leave Git in a confusing state, so refuse."""
    with pytest.raises(BranchError, match="merge is in progress"):
        conflicted_merge.commit_files([Path("app.yaml")], "Sneaky commit")
    with pytest.raises(BranchError, match="merge is in progress"):
        conflicted_merge.checkout(MAIN_BRANCH)

    assert conflicted_merge.current_branch == "feature"


# --- History and restoring old versions ------------------------------------


@pytest.fixture
def versioned_repo(config_repo: GitRepository) -> GitRepository:
    """A repo where app.yaml went through ports 80, 81 and 82."""
    for port in (80, 81, 82):
        commit_file(config_repo, "app.yaml", f"port: {port}\n".encode(), f"Port {port}")
    commit_file(config_repo, "other.yaml", b"a: 1\n", "Add other.yaml")
    return config_repo


def test_file_history_lists_commits_for_that_file_newest_first(
    versioned_repo: GitRepository,
) -> None:
    """Only commits that touched the file are listed, newest first."""
    history = versioned_repo.file_history(Path("app.yaml"))

    assert [commit.message for commit in history] == ["Port 82", "Port 81", "Port 80"]
    newest = history[0]
    assert newest.author == "Test User"
    assert isinstance(newest.date, datetime)
    assert newest.short_sha == newest.sha[:7]
    assert len(newest.short_sha) == 7


def test_file_history_of_unknown_file_is_empty(config_repo: GitRepository) -> None:
    """A file that was never committed has no history."""
    assert config_repo.file_history(Path("nope.yaml")) == []


def test_commit_info_decodes_byte_messages() -> None:
    """GitPython can return a message as bytes; CommitInfo should give text.

    Making Git produce such a commit for real is awkward, so a mock commit
    object stands in for it.
    """
    fake_commit = mock.Mock(hexsha="a" * 40, summary=b"Caf\xc3\xa9 settings")
    fake_commit.author.name = None

    info = CommitInfo.from_commit(fake_commit)

    assert info.message == "Café settings"
    assert info.author == ""


@pytest.mark.parametrize("revision_of", ["full", "short", "relative"])
def test_restore_file_brings_back_old_content(
    versioned_repo: GitRepository, revision_of: str
) -> None:
    """A file can be restored from a full ID, short ID, or HEAD~n."""
    port_80 = versioned_repo.file_history(Path("app.yaml"))[-1]
    revision = {
        "full": port_80.sha,
        "short": port_80.short_sha,
        "relative": "HEAD~3",
    }[revision_of]
    head_before = Repo(versioned_repo.path).head.commit

    restored_from = versioned_repo.restore_file(Path("app.yaml"), revision)

    assert restored_from.sha == port_80.sha
    assert (versioned_repo.path / "app.yaml").read_bytes() == b"port: 80\n"
    # Restoring only changes the file; nothing is committed yet.
    assert Repo(versioned_repo.path).head.commit == head_before


@pytest.mark.parametrize("revision", ["nope", "", "HEAD~99"])
def test_restore_file_unknown_commit_raises(
    versioned_repo: GitRepository, revision: str
) -> None:
    """Revisions Git cannot find should raise CommitNotFoundError."""
    with pytest.raises(CommitNotFoundError, match="no such commit") as excinfo:
        versioned_repo.restore_file(Path("app.yaml"), revision)

    assert excinfo.value.revision == revision


def test_restore_file_rejects_ids_that_are_not_commits(
    versioned_repo: GitRepository,
) -> None:
    """A tree or file ID is a real Git object but not a commit."""
    tree_id = Repo(versioned_repo.path).head.commit.tree.hexsha

    with pytest.raises(CommitNotFoundError, match="no such commit"):
        versioned_repo.restore_file(Path("app.yaml"), tree_id)


def test_restore_file_rejects_commit_from_another_branch(
    versioned_repo: GitRepository,
) -> None:
    """Only commits in the current branch's own history can be restored."""
    versioned_repo.create_branch("feature")
    commit_file(versioned_repo, "app.yaml", b"port: 99\n", "Feature only")
    feature_commit = Repo(versioned_repo.path).head.commit.hexsha
    versioned_repo.checkout(MAIN_BRANCH)

    with pytest.raises(CommitNotFoundError, match="not in the history of 'main'"):
        versioned_repo.restore_file(Path("app.yaml"), feature_commit)


def test_restore_file_missing_in_that_commit_raises(
    versioned_repo: GitRepository,
) -> None:
    """other.yaml did not exist yet when port was 80."""
    port_80 = versioned_repo.file_history(Path("app.yaml"))[-1]

    with pytest.raises(ConfigNotFound):
        versioned_repo.restore_file(Path("other.yaml"), port_80.sha)

    assert (versioned_repo.path / "other.yaml").read_bytes() == b"a: 1\n"


def test_restore_file_refuses_uncommitted_changes(
    versioned_repo: GitRepository,
) -> None:
    """Restoring over unsaved edits would destroy them, so refuse."""
    (versioned_repo.path / "app.yaml").write_bytes(b"port: 1234\n")

    with pytest.raises(BranchError, match="uncommitted changes"):
        versioned_repo.restore_file(Path("app.yaml"), "HEAD~1")

    assert (versioned_repo.path / "app.yaml").read_bytes() == b"port: 1234\n"
