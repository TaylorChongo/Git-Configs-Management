"""Tests for saving configuration files into the repository as commits."""

from __future__ import annotations

import pytest
from git import Repo

from config_cli.config_manager import ConfigManager
from config_cli.config_parser import ConfigParser
from config_cli.exceptions import InvalidConfigError
from config_cli.git_repository import GitRepository


@pytest.fixture
def manager(config_repo: GitRepository) -> ConfigManager:
    """Provide a ConfigManager working on a fresh temporary repository."""
    return ConfigManager(config_repo, ConfigParser())


def commit_count(repo: GitRepository) -> int:
    """Count the commits on the current branch."""
    return len(list(Repo(repo.path).iter_commits()))


def committed_text(repo: GitRepository, name: str) -> str:
    """Return a file's content as stored in the latest commit."""
    blob = Repo(repo.path).head.commit.tree / name
    content: bytes = blob.data_stream.read()
    return content.decode("utf-8")


def test_save_new_config_creates_commit(
    manager: ConfigManager, config_repo: GitRepository
) -> None:
    """Saving a new file should commit it with a 'Create' message."""
    sha = manager.save("app.yaml", {"port": 5432})

    head = Repo(config_repo.path).head.commit
    assert sha == head.hexsha
    assert head.message == "Create app.yaml"
    assert committed_text(config_repo, "app.yaml") == "port: 5432\n"
    assert commit_count(config_repo) == 2


def test_save_existing_config_creates_update_commit(
    manager: ConfigManager, config_repo: GitRepository
) -> None:
    """Saving changes to an existing file should add an 'Update' commit."""
    manager.save("app.yaml", {"port": 5432})

    manager.save("app.yaml", {"port": 5433})

    assert Repo(config_repo.path).head.commit.message == "Update app.yaml"
    assert committed_text(config_repo, "app.yaml") == "port: 5433\n"
    assert commit_count(config_repo) == 3


def test_save_uses_custom_message(
    manager: ConfigManager, config_repo: GitRepository
) -> None:
    """A message passed by the caller should be used as the commit message."""
    manager.save("app.json", {"debug": False}, message="Turn off debug mode")

    assert Repo(config_repo.path).head.commit.message == "Turn off debug mode"


def test_save_unchanged_config_makes_no_commit(
    manager: ConfigManager, config_repo: GitRepository
) -> None:
    """Saving identical content again should not add a commit."""
    manager.save("app.yaml", {"port": 5432})

    assert manager.save("app.yaml", {"port": 5432}) is None
    assert commit_count(config_repo) == 2


def test_save_into_subfolder(
    manager: ConfigManager, config_repo: GitRepository
) -> None:
    """Configs can be organised in folders inside the repository."""
    manager.save("env/prod.json", {"replicas": 3})

    assert Repo(config_repo.path).head.commit.message == "Create env/prod.json"
    assert '"replicas": 3' in committed_text(config_repo, "env/prod.json")


def test_save_leaves_working_tree_clean(
    manager: ConfigManager, config_repo: GitRepository
) -> None:
    """Everything written by save should be committed."""
    manager.save("app.yaml", {"port": 5432})

    assert not Repo(config_repo.path).is_dirty(untracked_files=True)


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("../outside.yaml", "inside the repository"),
        ("env/../../outside.yaml", "inside the repository"),
        (".", "inside the repository"),
        (".git/config.yaml", "inside .git"),
    ],
)
def test_save_rejects_unsafe_names(
    manager: ConfigManager, config_repo: GitRepository, name: str, reason: str
) -> None:
    """Names that escape the repository or touch .git must be refused."""
    with pytest.raises(InvalidConfigError, match=reason):
        manager.save(name, {"a": 1})

    assert commit_count(config_repo) == 1
    assert not (config_repo.path.parent / "outside.yaml").exists()


def test_save_rejects_absolute_path_outside_repo(
    manager: ConfigManager, config_repo: GitRepository
) -> None:
    """An absolute path elsewhere on disk must be refused."""
    outside = config_repo.path.parent / "outside.yaml"

    with pytest.raises(InvalidConfigError, match="inside the repository"):
        manager.save(str(outside), {"a": 1})

    assert not outside.exists()


def test_save_unsupported_format_writes_and_commits_nothing(
    manager: ConfigManager, config_repo: GitRepository
) -> None:
    """A bad file type should fail before anything is written or committed."""
    with pytest.raises(InvalidConfigError, match="unsupported format"):
        manager.save("notes.txt", {"a": 1})

    assert not (config_repo.path / "notes.txt").exists()
    assert commit_count(config_repo) == 1
