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

import shutil
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


# ---------------------------------------------------------------------------
# Ticket 025 — reconciliation git helpers (managed_worktrees / is_dirty / prune)
# ---------------------------------------------------------------------------


async def test_managed_worktrees_only_returns_those_under_worktree_dir(
    repo: Path, tmp_path: Path
) -> None:
    """``managed_worktrees`` returns ONLY worktrees under ``WORKTREES_DIR``.

    This is the load-bearing safety filter (ADR 0016): the main checkout and
    any worktree outside ``WORKTREES_DIR`` (e.g. the developer's own
    ``.claude/worktrees/`` sessions) must be invisible to the sweep. Here an
    "external" worktree stands in for a dev session.
    """
    wt_mgr = WorktreeManager(repo, tmp_path / "worktrees")

    await wt_mgr.create("dev.backend", branch="hive/dev.backend")
    await wt_mgr.create("dev.frontend", branch="hive/dev.frontend")

    # An external worktree OUTSIDE WORKTREES_DIR — the dev-session stand-in.
    external = tmp_path / "external" / "human-session"
    subprocess.run(
        ["git", "worktree", "add", "-b", "human/wip", str(external)],
        cwd=repo,
        check=True,
    )

    managed = await wt_mgr.managed_worktrees()
    names = {Path(wt["path"]).name for wt in managed}

    assert names == {"dev.backend", "dev.frontend"}


async def test_is_dirty_true_with_uncommitted_work_false_when_clean(
    repo: Path, tmp_path: Path
) -> None:
    """``is_dirty`` reflects uncommitted work in the worktree.

    Drives the never-delete-dirty policy (ADR 0016): a clean orphan is
    reclaimed, a dirty one is quarantined.
    """
    wt_mgr = WorktreeManager(repo, tmp_path / "worktrees")
    wt_path = await wt_mgr.create("dev.backend", branch="hive/dev.backend")

    assert await wt_mgr.is_dirty("dev.backend") is False

    (wt_path / "scratch.txt").write_text("uncommitted work\n")

    assert await wt_mgr.is_dirty("dev.backend") is True


async def test_prune_clears_stale_admin_record(repo: Path, tmp_path: Path) -> None:
    """``prune`` removes git-admin records whose working dir vanished.

    Crash matrix #5: a partial cleanup leaves the dir gone but the git
    metadata dangling, so ``git worktree list`` still shows a prunable
    entry. ``prune`` clears it.
    """
    wt_mgr = WorktreeManager(repo, tmp_path / "worktrees")
    wt_path = await wt_mgr.create("dev.backend", branch="hive/dev.backend")

    # Delete the working dir out-of-band (git admin record now dangles).
    shutil.rmtree(wt_path)
    assert any(Path(wt["path"]).name == "dev.backend" for wt in await wt_mgr.list_worktrees())

    pruned = await wt_mgr.prune()

    assert pruned  # reported at least one pruned record
    assert not any(Path(wt["path"]).name == "dev.backend" for wt in await wt_mgr.list_worktrees())
