"""Tests for saving configuration files into the repository as commits."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest
from git import Repo

from config_cli.config_manager import ConfigManager, RepoStatus
from config_cli.config_parser import ConfigParser
from config_cli.exceptions import (
    BranchError,
    CommitNotFoundError,
    ConfigNotFound,
    InvalidConfigError,
    KeyNotFoundError,
    MergeConflictError,
)
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

    assert excinfo.value.branch == "draft/new-port"
    assert excinfo.value.conflicted_files == ["app.yaml"]
    assert "Sync the draft with main" in str(excinfo.value)
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


# --- Syncing drafts and resolving conflicts --------------------------------


@pytest.fixture
def conflicting_draft(manager: ConfigManager) -> ConfigManager:
    """A draft and main that both changed the port in app.yaml."""
    manager.save("app.yaml", {"port": 5432})
    manager.start_draft("new-port")
    manager.save("app.yaml", {"port": 6000})
    manager.switch_to_main()
    manager.save("app.yaml", {"port": 7000})
    return manager


@pytest.fixture
def syncing_draft(conflicting_draft: ConfigManager) -> ConfigManager:
    """A draft whose sync with main has stopped on a conflict."""
    with pytest.raises(MergeConflictError):
        conflicting_draft.sync_draft("new-port")
    return conflicting_draft


def test_sync_brings_main_changes_into_draft(
    manager: ConfigManager, config_repo: GitRepository
) -> None:
    """Changes published on main since the draft started should reach it."""
    manager.start_draft("db")
    manager.save("db.yaml", {"host": "db1"})
    manager.switch_to_main()
    manager.save("app.yaml", {"port": 5432})

    sha = manager.sync_draft("db")

    head = Repo(config_repo.path).heads["draft/db"].commit
    assert sha == head.hexsha
    assert head.message.strip() == "Sync draft db with main"
    assert manager.current_draft == "db"
    assert (config_repo.path / "app.yaml").exists()
    assert (config_repo.path / "db.yaml").exists()


def test_sync_up_to_date_draft_raises(manager: ConfigManager) -> None:
    """A draft that already has everything from main has nothing to sync."""
    manager.start_draft("db")

    with pytest.raises(BranchError, match="no new commits"):
        manager.sync_draft("db")


def test_sync_conflict_is_left_on_the_draft(
    conflicting_draft: ConfigManager, config_repo: GitRepository
) -> None:
    """A sync conflict stays on the draft, with guidance on what to do next."""
    main_before = Repo(config_repo.path).heads[MAIN_BRANCH].commit

    with pytest.raises(MergeConflictError) as excinfo:
        conflicting_draft.sync_draft("new-port")

    assert excinfo.value.branch == "draft/new-port"
    assert excinfo.value.conflicted_files == ["app.yaml"]
    assert "finish the sync" in str(excinfo.value)
    assert conflicting_draft.current_draft == "new-port"
    assert config_repo.merge_in_progress
    assert b"<<<<<<<" in (config_repo.path / "app.yaml").read_bytes()
    assert Repo(config_repo.path).heads[MAIN_BRANCH].commit == main_before


def test_full_conflict_resolution_then_publish(
    syncing_draft: ConfigManager, config_repo: GitRepository
) -> None:
    """Sync, fix the file, finish, publish: the whole conflict workflow."""
    (config_repo.path / "app.yaml").write_bytes(b"port: 6500\n")

    syncing_draft.finish_sync()
    syncing_draft.publish("new-port")

    head = Repo(config_repo.path).heads[MAIN_BRANCH].commit
    assert head.message.strip() == "Publish draft new-port"
    assert (head.tree / "app.yaml").data_stream.read() == b"port: 6500\n"
    assert syncing_draft.list_drafts() == []


def test_finish_sync_refuses_leftover_markers(
    syncing_draft: ConfigManager, config_repo: GitRepository
) -> None:
    """Finishing without removing the markers must not commit anything."""
    with pytest.raises(MergeConflictError, match="still contain conflict markers"):
        syncing_draft.finish_sync()

    assert config_repo.merge_in_progress


def test_finish_sync_refuses_invalid_config(
    syncing_draft: ConfigManager, config_repo: GitRepository
) -> None:
    """A fixed file must still be valid YAML before it is committed."""
    (config_repo.path / "app.yaml").write_bytes(b"port: [6500\n")

    with pytest.raises(InvalidConfigError, match="could not be parsed"):
        syncing_draft.finish_sync()

    assert config_repo.merge_in_progress


def test_abort_sync_restores_draft(
    syncing_draft: ConfigManager, config_repo: GitRepository
) -> None:
    """Aborting a sync puts the draft back exactly as it was."""
    syncing_draft.abort_sync()

    assert not config_repo.merge_in_progress
    assert syncing_draft.current_draft == "new-port"
    assert (config_repo.path / "app.yaml").read_bytes() == b"port: 6000\n"


def test_save_is_refused_during_sync(
    syncing_draft: ConfigManager, config_repo: GitRepository
) -> None:
    """Saving mid-sync would silently drop the merge, so it is refused."""
    with pytest.raises(BranchError, match="merge is in progress"):
        syncing_draft.save("other.yaml", {"a": 1})

    assert config_repo.merge_in_progress


# --- History and rollback --------------------------------------------------


@pytest.fixture
def three_versions(manager: ConfigManager) -> ConfigManager:
    """app.yaml saved three times, with ports 5432, 5433 and 5434."""
    for port in (5432, 5433, 5434):
        manager.save("app.yaml", {"port": port})
    return manager


def test_history_lists_versions_newest_first(three_versions: ConfigManager) -> None:
    """history shows every commit that changed the file."""
    messages = [commit.message for commit in three_versions.history("app.yaml")]

    assert messages == ["Update app.yaml", "Update app.yaml", "Create app.yaml"]


def test_history_of_unknown_file_raises(manager: ConfigManager) -> None:
    """Asking for the history of a file that was never saved is an error."""
    with pytest.raises(ConfigNotFound):
        manager.history("nope.yaml")


def test_history_rejects_unsafe_name(manager: ConfigManager) -> None:
    """history uses the same path safety check as save."""
    with pytest.raises(InvalidConfigError, match="inside the repository"):
        manager.history("../outside.yaml")


def test_rollback_restores_old_version_as_new_commit(
    three_versions: ConfigManager, config_repo: GitRepository
) -> None:
    """Rolling back adds a commit; the old commits stay in the history."""
    first = three_versions.history("app.yaml")[-1]

    sha = three_versions.rollback("app.yaml", first.short_sha)

    head = Repo(config_repo.path).head.commit
    assert sha == head.hexsha
    assert head.message == f"Roll back app.yaml to {first.short_sha}"
    assert (head.tree / "app.yaml").data_stream.read() == b"port: 5432\n"
    assert len(three_versions.history("app.yaml")) == 4
    assert not Repo(config_repo.path).is_dirty(untracked_files=True)


def test_rollback_uses_custom_message(
    three_versions: ConfigManager, config_repo: GitRepository
) -> None:
    """A caller-supplied message is used for the rollback commit."""
    three_versions.rollback("app.yaml", "HEAD~1", message="Undo bad port")

    assert Repo(config_repo.path).head.commit.message == "Undo bad port"


def test_rollback_can_itself_be_rolled_back(
    three_versions: ConfigManager, config_repo: GitRepository
) -> None:
    """Because rollback adds a commit, it can be undone like any change."""
    newest = three_versions.history("app.yaml")[0]
    three_versions.rollback("app.yaml", "HEAD~2")

    three_versions.rollback("app.yaml", newest.short_sha)

    assert (config_repo.path / "app.yaml").read_bytes() == b"port: 5434\n"


def test_rollback_to_current_version_makes_no_commit(
    three_versions: ConfigManager,
) -> None:
    """Rolling back to the version already in place changes nothing."""
    newest = three_versions.history("app.yaml")[0]

    assert three_versions.rollback("app.yaml", newest.sha) is None
    assert len(three_versions.history("app.yaml")) == 3


def test_rollback_on_draft_does_not_touch_main(
    three_versions: ConfigManager, config_repo: GitRepository
) -> None:
    """A rollback is a normal change, so on a draft it stays off main."""
    three_versions.start_draft("undo")

    three_versions.rollback("app.yaml", "HEAD~2")

    main_blob = Repo(config_repo.path).heads[MAIN_BRANCH].commit.tree / "app.yaml"
    assert main_blob.data_stream.read() == b"port: 5434\n"
    assert (config_repo.path / "app.yaml").read_bytes() == b"port: 5432\n"


def test_rollback_to_before_file_existed_raises(
    three_versions: ConfigManager, config_repo: GitRepository
) -> None:
    """The very first commit has no app.yaml, so there is nothing to restore."""
    initial_commit = list(Repo(config_repo.path).iter_commits())[-1].hexsha

    with pytest.raises(ConfigNotFound):
        three_versions.rollback("app.yaml", initial_commit)

    assert (config_repo.path / "app.yaml").read_bytes() == b"port: 5434\n"


def test_rollback_unknown_commit_raises(three_versions: ConfigManager) -> None:
    """A commit ID that does not exist should raise CommitNotFoundError."""
    with pytest.raises(CommitNotFoundError):
        three_versions.rollback("app.yaml", "deadbeef")


# --- Reading and setting single values -------------------------------------


@pytest.fixture
def db_config(manager: ConfigManager) -> ConfigManager:
    """app.yaml with a nested database section and a top-level flag."""
    manager.save("app.yaml", {"database": {"host": "localhost", "port": 5432}})
    return manager


def test_read_returns_parsed_file(db_config: ConfigManager) -> None:
    """read should return the whole configuration as Python data."""
    assert db_config.read("app.yaml") == {
        "database": {"host": "localhost", "port": 5432}
    }


def test_read_missing_file_raises(manager: ConfigManager) -> None:
    """Reading a config that does not exist should raise ConfigNotFound."""
    with pytest.raises(ConfigNotFound):
        manager.read("nope.yaml")


@pytest.mark.parametrize(
    ("key", "expected"),
    [
        ("database.port", 5432),
        ("database.host", "localhost"),
        ("database", {"host": "localhost", "port": 5432}),
    ],
)
def test_get_value_follows_dotted_keys(
    db_config: ConfigManager, key: str, expected: object
) -> None:
    """Dots step into nested sections; a section itself can be returned."""
    assert db_config.get_value("app.yaml", key) == expected


@pytest.mark.parametrize("key", ["database.user", "cache", "database.port.extra"])
def test_get_value_missing_key_raises(db_config: ConfigManager, key: str) -> None:
    """Unknown keys, including stepping into a plain value, are not found."""
    with pytest.raises(KeyNotFoundError) as excinfo:
        db_config.get_value("app.yaml", key)

    assert excinfo.value.key == key


@pytest.mark.parametrize("key", ["", "database..port", ".port", "database."])
def test_malformed_keys_are_rejected(db_config: ConfigManager, key: str) -> None:
    """Keys with empty parts are mistakes, not lookups."""
    with pytest.raises(InvalidConfigError, match="invalid key"):
        db_config.get_value("app.yaml", key)
    with pytest.raises(InvalidConfigError, match="invalid key"):
        db_config.set_value("app.yaml", key, 1)


def test_set_value_updates_one_value_and_keeps_the_rest(
    db_config: ConfigManager, config_repo: GitRepository
) -> None:
    """set_value changes one key, leaves the others, and commits."""
    sha = db_config.set_value("app.yaml", "database.port", 5433)

    head = Repo(config_repo.path).head.commit
    assert sha == head.hexsha
    assert head.message == "Set database.port in app.yaml"
    assert db_config.read("app.yaml") == {
        "database": {"host": "localhost", "port": 5433}
    }


def test_set_value_creates_missing_sections(db_config: ConfigManager) -> None:
    """Sections along the key are created as needed."""
    db_config.set_value("app.yaml", "cache.redis.ttl", 60)

    assert db_config.get_value("app.yaml", "cache") == {"redis": {"ttl": 60}}


def test_set_value_creates_missing_file(
    manager: ConfigManager, config_repo: GitRepository
) -> None:
    """Setting a value in a file that does not exist yet creates it."""
    manager.set_value("env/prod.json", "replicas", 3)

    assert manager.read("env/prod.json") == {"replicas": 3}
    assert not Repo(config_repo.path).is_dirty(untracked_files=True)


def test_set_value_custom_message(
    db_config: ConfigManager, config_repo: GitRepository
) -> None:
    """A caller-supplied message is used for the commit."""
    db_config.set_value("app.yaml", "debug", True, message="Enable debug")

    assert Repo(config_repo.path).head.commit.message == "Enable debug"


def test_set_value_to_same_value_makes_no_commit(db_config: ConfigManager) -> None:
    """Setting a value it already has changes nothing."""
    assert db_config.set_value("app.yaml", "database.port", 5432) is None


def test_set_value_through_a_plain_value_raises(db_config: ConfigManager) -> None:
    """database.port is a number, so it cannot contain further keys."""
    with pytest.raises(InvalidConfigError, match="'port' holds a value"):
        db_config.set_value("app.yaml", "database.port.extra", 1)

    assert db_config.get_value("app.yaml", "database.port") == 5432


def test_set_value_in_list_file_raises(manager: ConfigManager) -> None:
    """A file whose top level is a list has no keys to set."""
    manager.save("servers.yaml", ["web1", "web2"])

    with pytest.raises(InvalidConfigError, match="top level is a list"):
        manager.set_value("servers.yaml", "web3", True)


# --- Importing, other branches and status ----------------------------------


def test_save_from_file_imports_and_converts(
    manager: ConfigManager, config_repo: GitRepository, tmp_path: Path
) -> None:
    """A local JSON file is stored in the format of the target name."""
    source = tmp_path / "local.json"
    source.write_text('{"port": 5432}', encoding="utf-8")

    manager.save_from_file("app.yaml", source)

    assert (config_repo.path / "app.yaml").read_bytes() == b"port: 5432\n"


def test_save_from_missing_file_raises(manager: ConfigManager, tmp_path: Path) -> None:
    """A source file that does not exist is reported as ConfigNotFound."""
    with pytest.raises(ConfigNotFound):
        manager.save_from_file("app.yaml", tmp_path / "missing.yaml")


def test_read_from_another_branch_leaves_checkout_alone(
    db_config: ConfigManager, config_repo: GitRepository
) -> None:
    """Reading main while on a draft uses a temporary clone, not a checkout."""
    db_config.start_draft("db")
    db_config.set_value("app.yaml", "database.port", 6000)

    assert db_config.get_value("app.yaml", "database.port", MAIN_BRANCH) == 5432
    assert db_config.current_draft == "db"
    assert db_config.get_value("app.yaml", "database.port") == 6000


def test_read_from_missing_branch_raises(db_config: ConfigManager) -> None:
    """Reading a branch that does not exist is a BranchError."""
    with pytest.raises(BranchError, match="does not exist"):
        db_config.read("app.yaml", "nope")


@pytest.mark.parametrize(
    ("name", "expected"),
    [("app.json", '{\n  "a": 1\n}\n'), ("app.yaml", "a: 1\n")],
)
def test_render_uses_the_names_format(
    manager: ConfigManager, name: str, expected: str
) -> None:
    """render formats data as JSON or YAML depending on the file name."""
    assert manager.render(name, {"a": 1}) == expected


def test_status_on_main(manager: ConfigManager) -> None:
    """A fresh repository is on main with nothing in progress."""
    assert manager.status() == RepoStatus(
        branch=MAIN_BRANCH, draft=None, merge_in_progress=False, conflicted_files=[]
    )


def test_status_during_sync(syncing_draft: ConfigManager) -> None:
    """status reports the draft and the files that need fixing."""
    assert syncing_draft.status() == RepoStatus(
        branch="draft/new-port",
        draft="new-port",
        merge_in_progress=True,
        conflicted_files=["app.yaml"],
    )


# --- Working against the abstractions --------------------------------------


def test_manager_works_with_any_version_store() -> None:
    """ConfigManager depends on the VersionStore abstraction, not on Git.

    A mock stands in for the repository, so this test checks the manager's
    own logic (wrapping a conflict with draft-specific advice) without Git.
    """
    fake_repo = mock.Mock()
    fake_repo.merge.side_effect = MergeConflictError("draft/db", ["app.yaml"])
    manager = ConfigManager(fake_repo, ConfigParser())

    with pytest.raises(MergeConflictError, match="Sync the draft with main"):
        manager.publish("db")

    fake_repo.merge.assert_called_once_with("draft/db", MAIN_BRANCH, "Publish draft db")
    fake_repo.delete_branch.assert_not_called()
