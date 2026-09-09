"""Tests for /commit, /pr, /merge Telegram commands.

Mocks hive.process.git_ops.run so no real git/gh subprocesses are spawned.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from hive.bus.router import MessageRouter
from hive.models.team_lead import TeamLead
from hive.process.manager import ProcessManager
from hive.telegram.bridge import TelegramBridge


@pytest_asyncio.fixture
async def manager(router: MessageRouter) -> AsyncIterator[ProcessManager]:
    mgr = ProcessManager(router=router)
    try:
        yield mgr
    finally:
        await mgr.kill_all()


@pytest.fixture
def bridge(manager: ProcessManager) -> TelegramBridge:
    b = TelegramBridge(
        bot_token="test-token",
        allowed_user_ids=[],
        process_manager=manager,
    )
    return b


@pytest.fixture
def lead_with_worktree(manager: ProcessManager, tmp_path: Path) -> TeamLead:
    """A Lead running in its own worktree (the worktree floor).

    The git commands (/commit, /pr, /merge) operate on any entity that
    has a ``worktree_path``; a Lead is the one that carries one now that
    the persistent Worker entity is retired (Ticket 018).
    """
    wt = tmp_path / "wt"
    wt.mkdir()
    lead = TeamLead(
        name="dev.backend",
        team_name="backend",
        maestro_name="dev",
        worktree_path=wt,
    )
    manager._entities[lead.name] = lead
    manager.router.register(lead.name)
    return lead


# ---------------------------------------------------------------------------
# /commit
# ---------------------------------------------------------------------------


async def test_commit_without_entity_returns_usage(bridge: TelegramBridge) -> None:
    result = await bridge.dispatcher.git._execute_commit(None, "")
    assert "Usage" in result


async def test_commit_unknown_entity(bridge: TelegramBridge) -> None:
    result = await bridge.dispatcher.git._execute_commit("ghost", '"message"')
    assert "not found" in result


async def test_commit_entity_without_worktree(
    bridge: TelegramBridge, manager: ProcessManager
) -> None:
    lead = TeamLead(name="dev.frontend", team_name="frontend", maestro_name="dev")
    manager._entities[lead.name] = lead
    manager.router.register(lead.name)
    result = await bridge.dispatcher.git._execute_commit(lead.name, '"message"')
    assert "no worktree" in result


async def test_commit_success(
    bridge: TelegramBridge,
    lead_with_worktree: TeamLead,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    async def fake_run(cmd: list[str], cwd: Path) -> tuple[int, str, str]:
        calls.append(cmd)
        if cmd[:2] == ["git", "log"] and "--pretty=format:%h %s" in cmd:
            return 0, "abc123 retry on transient errors", ""
        if cmd[:2] == ["git", "log"] and "--shortstat" in cmd:
            return 0, " 2 files changed, 14 insertions(+)", ""
        return 0, "", ""

    monkeypatch.setattr("hive.process.git_ops.run", fake_run)
    result = await bridge.dispatcher.git._execute_commit(
        lead_with_worktree.name, '"retry on transient errors"'
    )
    assert "Committed in" in result
    assert "abc123" in result
    # Verify we ran the right sequence
    assert ["git", "add", "-A"] in calls
    commit_calls = [c for c in calls if c[:2] == ["git", "commit"]]
    assert len(commit_calls) == 1
    assert "retry on transient errors" in commit_calls[0]


async def test_commit_propagates_git_failure(
    bridge: TelegramBridge,
    lead_with_worktree: TeamLead,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run(cmd: list[str], cwd: Path) -> tuple[int, str, str]:
        if cmd[:2] == ["git", "commit"]:
            return 1, "", "nothing to commit, working tree clean"
        return 0, "", ""

    monkeypatch.setattr("hive.process.git_ops.run", fake_run)
    result = await bridge.dispatcher.git._execute_commit(lead_with_worktree.name, '"empty"')
    assert "git commit failed" in result
    assert "nothing to commit" in result


# ---------------------------------------------------------------------------
# /pr
# ---------------------------------------------------------------------------


async def test_pr_pushes_and_creates(
    bridge: TelegramBridge,
    lead_with_worktree: TeamLead,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[list[str]] = []

    async def fake_run(cmd: list[str], cwd: Path) -> tuple[int, str, str]:
        recorded.append(cmd)
        if cmd[:4] == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return 0, "hive/dev.backend.w1\n", ""
        if cmd[:3] == ["git", "push", "-u"]:
            return 0, "", "pushed"
        if cmd[:3] == ["gh", "pr", "create"]:
            return 0, "https://github.com/yehezkieled/hive/pull/42", ""
        return 0, "", ""

    monkeypatch.setattr("hive.process.git_ops.run", fake_run)
    result = await bridge.dispatcher.git._execute_pr(lead_with_worktree.name, '"my change"')
    assert "pull/42" in result
    assert "hive/dev.backend.w1" in result
    # gh should have been called with --title when title provided
    gh_calls = [c for c in recorded if c[:3] == ["gh", "pr", "create"]]
    assert gh_calls and "--title" in gh_calls[0]


async def test_pr_without_title_uses_fill(
    bridge: TelegramBridge,
    lead_with_worktree: TeamLead,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[list[str]] = []

    async def fake_run(cmd: list[str], cwd: Path) -> tuple[int, str, str]:
        recorded.append(cmd)
        if cmd[:4] == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return 0, "hive/x\n", ""
        if cmd[:3] == ["gh", "pr", "create"]:
            return 0, "https://example/pr/1", ""
        return 0, "", ""

    monkeypatch.setattr("hive.process.git_ops.run", fake_run)
    await bridge.dispatcher.git._execute_pr(lead_with_worktree.name, "")
    gh_calls = [c for c in recorded if c[:3] == ["gh", "pr", "create"]]
    assert gh_calls and "--fill" in gh_calls[0]


async def test_pr_detached_head_error(
    bridge: TelegramBridge,
    lead_with_worktree: TeamLead,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run(cmd: list[str], cwd: Path) -> tuple[int, str, str]:
        if cmd[:4] == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
            return 0, "HEAD\n", ""
        return 0, "", ""

    monkeypatch.setattr("hive.process.git_ops.run", fake_run)
    result = await bridge.dispatcher.git._execute_pr(lead_with_worktree.name, "")
    assert "detached" in result.lower() or "branch" in result.lower()


# ---------------------------------------------------------------------------
# /merge
# ---------------------------------------------------------------------------


async def test_merge_disabled_by_default(
    bridge: TelegramBridge,
    lead_with_worktree: TeamLead,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("hive.commands.git_commands.ALLOW_AUTO_MERGE", False)
    result = await bridge.dispatcher.git._execute_merge(lead_with_worktree.name)
    assert "disabled" in result
    assert "HIVE_ALLOW_AUTO_MERGE" in result


async def test_merge_when_enabled(
    bridge: TelegramBridge,
    lead_with_worktree: TeamLead,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("hive.commands.git_commands.ALLOW_AUTO_MERGE", True)
    fake = AsyncMock(return_value=(0, "Squashed and merged!", ""))
    monkeypatch.setattr("hive.process.git_ops.run", fake)
    result = await bridge.dispatcher.git._execute_merge(lead_with_worktree.name)
    assert "Merged PR" in result
    assert "Squashed and merged" in result
    # Verify the actual gh invocation
    actual_cmd = fake.call_args.args[0]
    assert actual_cmd[:3] == ["gh", "pr", "merge"]
    assert "--squash" in actual_cmd


# ---------------------------------------------------------------------------
# /ship (T007 — folds /commit /pr /merge)
# ---------------------------------------------------------------------------


async def test_ship_without_entity_returns_usage(bridge: TelegramBridge) -> None:
    result = await bridge.dispatcher.git._execute_ship(None, "")
    assert "Usage" in result and "/ship" in result


async def test_ship_unknown_entity(bridge: TelegramBridge) -> None:
    result = await bridge.dispatcher.git._execute_ship("ghost", "")
    assert "not found" in result.lower()


async def test_ship_commits_and_opens_pr(
    bridge: TelegramBridge,
    lead_with_worktree: TeamLead,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bare /ship commits (default message) + pushes + opens a PR; no merge."""
    fake = AsyncMock(return_value=(0, "ok", ""))
    monkeypatch.setattr("hive.process.git_ops.run", fake)
    monkeypatch.setattr("hive.commands.git_commands.ALLOW_AUTO_MERGE", True)
    result = await bridge.dispatcher.git._execute_ship(lead_with_worktree.name, "")
    assert "Committed" in result
    assert "PR opened" in result
    # No merge requested → gh pr merge must not have been called.
    calls = [c.args[0] for c in fake.call_args_list]
    assert not any(c[:3] == ["gh", "pr", "merge"] for c in calls)


async def test_ship_custom_message(
    bridge: TelegramBridge,
    lead_with_worktree: TeamLead,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = AsyncMock(return_value=(0, "ok", ""))
    monkeypatch.setattr("hive.process.git_ops.run", fake)
    await bridge.dispatcher.git._execute_ship(lead_with_worktree.name, '"add retry logic"')
    # The commit call carries the custom message.
    commit_calls = [c.args[0] for c in fake.call_args_list if c.args[0][:2] == ["git", "commit"]]
    assert commit_calls, "expected a git commit call"
    assert any("add retry logic" in " ".join(c) for c in commit_calls)


async def test_ship_merge_squash_merges_when_enabled(
    bridge: TelegramBridge,
    lead_with_worktree: TeamLead,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = AsyncMock(return_value=(0, "ok", ""))
    monkeypatch.setattr("hive.process.git_ops.run", fake)
    monkeypatch.setattr("hive.commands.git_commands.ALLOW_AUTO_MERGE", True)
    result = await bridge.dispatcher.git._execute_ship(lead_with_worktree.name, "merge")
    assert "Merged PR" in result
    calls = [c.args[0] for c in fake.call_args_list]
    assert any(c[:3] == ["gh", "pr", "merge"] for c in calls)


async def test_ship_aborts_on_genuine_commit_failure(
    bridge: TelegramBridge,
    lead_with_worktree: TeamLead,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real commit failure (git add error) aborts BEFORE push/PR — a failed
    ship must never read as a success with changes left uncommitted."""

    async def fake_run(cmd: list[str], cwd: Path) -> tuple[int, str, str]:
        if cmd[:2] == ["git", "add"]:
            return 1, "", "fatal: unable to write new index file"
        return 0, "ok", ""

    monkeypatch.setattr("hive.process.git_ops.run", fake_run)
    result = await bridge.dispatcher.git._execute_ship(lead_with_worktree.name, "")

    assert "Ship aborted" in result
    assert "PR opened" not in result


async def test_ship_proceeds_when_nothing_to_commit(
    bridge: TelegramBridge,
    lead_with_worktree: TeamLead,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A clean worktree ("nothing to commit") is benign — /ship still pushes
    existing commits and opens the PR."""
    calls: list[list[str]] = []

    async def fake_run(cmd: list[str], cwd: Path) -> tuple[int, str, str]:
        calls.append(cmd)
        if cmd[:2] == ["git", "commit"]:
            return 1, "nothing to commit, working tree clean", ""
        if cmd[:2] == ["git", "rev-parse"]:
            return 0, "feature-branch", ""
        return 0, "ok", ""

    monkeypatch.setattr("hive.process.git_ops.run", fake_run)
    result = await bridge.dispatcher.git._execute_ship(lead_with_worktree.name, "")

    assert "Commit skipped" in result
    assert "PR opened" in result
    assert any(c[:2] == ["git", "push"] for c in calls)
    assert any(c[:3] == ["gh", "pr", "create"] for c in calls)


async def test_is_git_repo_true_and_false(tmp_path: Path) -> None:
    from hive.process import git_ops

    non_git = tmp_path / "plain"
    non_git.mkdir()
    assert await git_ops.is_git_repo(non_git) is False
    assert await git_ops.is_git_repo(None) is False

    repo = tmp_path / "repo"
    repo.mkdir()
    code, _, _ = await git_ops.run(["git", "init"], cwd=repo)
    assert code == 0
    assert await git_ops.is_git_repo(repo) is True
