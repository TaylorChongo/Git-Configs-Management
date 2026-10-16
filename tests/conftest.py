"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from config_cli.git_repository import GitRepository


@pytest.fixture
def config_repo(tmp_path: Path) -> GitRepository:
    """Provide a brand-new configuration repository in a temporary folder."""
    return GitRepository.init(tmp_path / "configs")
