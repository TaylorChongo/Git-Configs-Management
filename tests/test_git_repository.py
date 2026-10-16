"""Tests for creating and opening configuration repositories."""

from __future__ import annotations

from pathlib import Path

import pytest
from git import Repo

from config_cli.exceptions import BranchError, InvalidRepoError
from config_cli.git_repository import (
    DEFAULT_GITATTRIBUTES,
    DEFAULT_GITIGNORE,
    MAIN_BRANCH,
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
