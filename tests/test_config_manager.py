"""Tests for saving configuration files into the repository as commits."""

from __future__ import annotations

import pytest
from git import Repo

from config_cli.config_manager import ConfigManager
from config_cli.config_parser import ConfigParser
from config_cli.exceptions import BranchError, InvalidConfigError, MergeConflictError
from config_cli.git_repository import MAIN_BRANCH, GitRepository


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


# --- Drafts ----------------------------------------------------------------


def test_start_draft_creates_draft_branch_and_switches_to_it(
    manager: ConfigManager, config_repo: GitRepository
) -> None:
    """A draft is a 'draft/<name>' branch that becomes the active branch."""
    branch = manager.start_draft("new-db")

    assert branch == "draft/new-db"
    assert Repo(config_repo.path).active_branch.name == "draft/new-db"
    assert manager.current_draft == "new-db"


def test_saving_on_draft_does_not_change_main(
    manager: ConfigManager, config_repo: GitRepository
) -> None:
    """Changes made on a draft must stay off main until published."""
    manager.save("app.yaml", {"port": 5432})
    manager.start_draft("new-port")

    manager.save("app.yaml", {"port": 6000})

    repo = Repo(config_repo.path)
    main_blob = repo.heads[MAIN_BRANCH].commit.tree / "app.yaml"
    draft_blob = repo.heads["draft/new-port"].commit.tree / "app.yaml"
    assert main_blob.data_stream.read() == b"port: 5432\n"
    assert draft_blob.data_stream.read() == b"port: 6000\n"


def test_draft_starts_from_main_even_when_on_another_draft(
    manager: ConfigManager, config_repo: GitRepository
) -> None:
    """A new draft must not inherit unpublished work from the current draft."""
    manager.start_draft("first")
    manager.save("first.yaml", {"a": 1})

    manager.start_draft("second")

    assert not (config_repo.path / "first.yaml").exists()


def test_switch_between_drafts_and_main(
    manager: ConfigManager, config_repo: GitRepository
) -> None:
    """Switching changes which version of the files is on disk."""
    manager.save("app.yaml", {"port": 5432})
    manager.start_draft("new-port")
    manager.save("app.yaml", {"port": 6000})

    manager.switch_to_main()
    assert manager.current_draft is None
    assert (config_repo.path / "app.yaml").read_bytes() == b"port: 5432\n"

    manager.switch_to_draft("new-port")
    assert manager.current_draft == "new-port"
    assert (config_repo.path / "app.yaml").read_bytes() == b"port: 6000\n"


def test_list_drafts_returns_only_draft_names(manager: ConfigManager) -> None:
    """main is not a draft, and names are shown without the prefix."""
    manager.start_draft("b-draft")
    manager.start_draft("a-draft")

    assert manager.list_drafts() == ["a-draft", "b-draft"]


def test_list_drafts_is_empty_for_new_repository(manager: ConfigManager) -> None:
    """A fresh repository has no drafts."""
    assert manager.list_drafts() == []


def test_start_draft_twice_raises(manager: ConfigManager) -> None:
    """Starting a draft that already exists should raise BranchError."""
    manager.start_draft("new-db")

    with pytest.raises(BranchError, match="already exists"):
        manager.start_draft("new-db")


def test_switch_to_missing_draft_raises(manager: ConfigManager) -> None:
    """Switching to an unknown draft should raise BranchError."""
    with pytest.raises(BranchError, match="does not exist"):
        manager.switch_to_draft("nope")


# --- Publishing ------------------------------------------------------------


def test_publish_merges_draft_into_main_and_deletes_it(
    manager: ConfigManager, config_repo: GitRepository
) -> None:
    """Publishing should put the draft's changes on main and remove the draft."""
    manager.save("app.yaml", {"port": 5432})
    manager.start_draft("new-port")
    manager.save("app.yaml", {"port": 6000})

    sha = manager.publish("new-port")

    head = Repo(config_repo.path).heads[MAIN_BRANCH].commit
    assert sha == head.hexsha
    assert head.message.strip() == "Publish draft new-port"
    assert (head.tree / "app.yaml").data_stream.read() == b"port: 6000\n"
    assert manager.list_drafts() == []
    assert config_repo.current_branch == MAIN_BRANCH


def test_publish_uses_custom_message(
    manager: ConfigManager, config_repo: GitRepository
) -> None:
    """A caller-supplied message should be used for the merge commit."""
    manager.start_draft("db")
    manager.save("db.yaml", {"host": "db1"})

    manager.publish("db", message="Move to db1")

    assert Repo(config_repo.path).head.commit.message.strip() == "Move to db1"


def test_publish_conflict_keeps_main_and_draft(
    manager: ConfigManager, config_repo: GitRepository
) -> None:
    """On a conflict, main and the draft must both survive unchanged."""
    manager.save("app.yaml", {"port": 5432})
    manager.start_draft("new-port")
    manager.save("app.yaml", {"port": 6000})
    manager.switch_to_main()
    manager.save("app.yaml", {"port": 7000})
    manager.switch_to_draft("new-port")
    main_before = Repo(config_repo.path).heads[MAIN_BRANCH].commit

    with pytest.raises(MergeConflictError) as excinfo:
        manager.publish("new-port")

    assert excinfo.value.conflicted_files == ["app.yaml"]
    assert Repo(config_repo.path).heads[MAIN_BRANCH].commit == main_before
    assert manager.list_drafts() == ["new-port"]
    assert manager.current_draft == "new-port"


def test_publish_missing_draft_raises(manager: ConfigManager) -> None:
    """Publishing a draft that does not exist should raise BranchError."""
    with pytest.raises(BranchError, match="does not exist"):
        manager.publish("nope")


def test_publish_empty_draft_raises(manager: ConfigManager) -> None:
    """A draft with no saved changes has nothing to publish."""
    manager.start_draft("empty")

    with pytest.raises(BranchError, match="no new commits"):
        manager.publish("empty")

    assert manager.list_drafts() == ["empty"]
