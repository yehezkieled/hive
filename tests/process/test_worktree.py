"""Tests for ``WorktreeManager`` create/remove idempotence (Ticket 015).

The worktree floor relies on two idempotence properties of ``create``:

- A worktree that survived a service restart is handed back as-is, so the
  lazy (re-)provisioning in ``_get_or_create_adapter`` is safe to repeat.
- ``git worktree remove`` never deletes the branch, so re-creating a name
  after a kill (team names are reusable) must attach to the surviving
  branch instead of failing on ``-b``.

These drive a real ``git`` against a throwaway repo in ``tmp_path`` — the
manager is a thin subprocess wrapper, so mocking git would test nothing.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hive.process.worktree import WorktreeManager


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A minimal git repo with one commit, so worktrees can be added."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    for cmd in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "test@hive.local"],
        ["git", "config", "user.name", "Hive Test"],
    ):
        subprocess.run(cmd, cwd=repo_path, check=True)
    (repo_path / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo_path, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=repo_path, check=True)
    return repo_path


async def test_create_after_remove_attaches_surviving_branch(repo: Path, tmp_path: Path) -> None:
    """Re-creating a removed worktree reuses its branch instead of failing.

    ``git worktree remove`` leaves the branch behind; a second
    ``create(name, branch=...)`` used to die on ``-b`` with "a branch
    named '...' already exists". Kill-then-recreate of the same team name
    is a supported flow, so create must be idempotent across it.
    """
    wt_mgr = WorktreeManager(repo, tmp_path / "worktrees")

    first = await wt_mgr.create("dev.backend", branch="hive/dev.backend")
    await wt_mgr.remove("dev.backend")
    assert not first.exists()

    second = await wt_mgr.create("dev.backend", branch="hive/dev.backend")

    assert second == first
    assert second.exists()


async def test_create_on_existing_worktree_returns_it(repo: Path, tmp_path: Path) -> None:
    """create() on a worktree that already exists hands it back unchanged.

    This is what makes the lead's lazy re-provisioning after a service
    restart safe — the worktree survived the restart, and the repeated
    create is a no-op returning the same path.
    """
    wt_mgr = WorktreeManager(repo, tmp_path / "worktrees")

    first = await wt_mgr.create("dev.backend", branch="hive/dev.backend")
    again = await wt_mgr.create("dev.backend", branch="hive/dev.backend")

    assert again == first
    assert again.exists()
