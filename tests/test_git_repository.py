"""Tests for creating and opening configuration repositories."""

from __future__ import annotations

from pathlib import Path

import pytest
from git import Repo

from config_cli.exceptions import InvalidRepoError
from config_cli.git_repository import DEFAULT_GITIGNORE, MAIN_BRANCH, GitRepository

# These tests check the result with GitPython's own ``Repo`` instead of
# GitRepository, so they confirm the real Git state rather than trusting
# the code under test to report on itself.


def test_init_creates_repository_on_main(config_repo: GitRepository) -> None:
    """A new repository should be a real Git repo checked out on main."""
    repo = Repo(config_repo.path)

    assert (config_repo.path / ".git").is_dir()
    assert repo.active_branch.name == MAIN_BRANCH
    assert config_repo.current_branch == MAIN_BRANCH


def test_init_makes_one_commit_with_gitignore(config_repo: GitRepository) -> None:
    """main should start with exactly one commit that tracks .gitignore."""
    repo = Repo(config_repo.path)
    commits = list(repo.iter_commits(MAIN_BRANCH))

    assert len(commits) == 1
    assert commits[0].message == "Initialize configuration repository"
    assert ".gitignore" in commits[0].tree
    gitignore = (config_repo.path / ".gitignore").read_text(encoding="utf-8")
    assert gitignore == DEFAULT_GITIGNORE


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
