"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from config_cli.git_repository import GitRepository


@pytest.fixture(autouse=True)
def git_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every test the same Git author, whatever the machine's settings.

    Git refuses to commit without a name and email. Setting them here means
    the tests pass on a fresh CI machine too, not just on a developer's laptop.
    """
    for role in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{role}_NAME", "Test User")
        monkeypatch.setenv(f"GIT_{role}_EMAIL", "test@example.com")


@pytest.fixture
def config_repo(tmp_path: Path) -> GitRepository:
    """Provide a brand-new configuration repository in a temporary folder."""
    return GitRepository.init(tmp_path / "configs")
