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
    result = await bridge.dispatcher._execute_commit(None, "")
    assert "Usage" in result


async def test_commit_unknown_entity(bridge: TelegramBridge) -> None:
    result = await bridge.dispatcher._execute_commit("ghost", '"message"')
    assert "not found" in result


async def test_commit_entity_without_worktree(
    bridge: TelegramBridge, manager: ProcessManager
) -> None:
    lead = TeamLead(name="dev.frontend", team_name="frontend", maestro_name="dev")
    manager._entities[lead.name] = lead
    manager.router.register(lead.name)
    result = await bridge.dispatcher._execute_commit(lead.name, '"message"')
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
    result = await bridge.dispatcher._execute_commit(
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
    result = await bridge.dispatcher._execute_commit(lead_with_worktree.name, '"empty"')
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
    result = await bridge.dispatcher._execute_pr(lead_with_worktree.name, '"my change"')
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
    await bridge.dispatcher._execute_pr(lead_with_worktree.name, "")
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
    result = await bridge.dispatcher._execute_pr(lead_with_worktree.name, "")
    assert "detached" in result.lower() or "branch" in result.lower()


# ---------------------------------------------------------------------------
# /merge
# ---------------------------------------------------------------------------


async def test_merge_disabled_by_default(
    bridge: TelegramBridge,
    lead_with_worktree: TeamLead,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("hive.commands.dispatch.ALLOW_AUTO_MERGE", False)
    result = await bridge.dispatcher._execute_merge(lead_with_worktree.name)
    assert "disabled" in result
    assert "HIVE_ALLOW_AUTO_MERGE" in result


async def test_merge_when_enabled(
    bridge: TelegramBridge,
    lead_with_worktree: TeamLead,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("hive.commands.dispatch.ALLOW_AUTO_MERGE", True)
    fake = AsyncMock(return_value=(0, "Squashed and merged!", ""))
    monkeypatch.setattr("hive.process.git_ops.run", fake)
    result = await bridge.dispatcher._execute_merge(lead_with_worktree.name)
    assert "Merged PR" in result
    assert "Squashed and merged" in result
    # Verify the actual gh invocation
    actual_cmd = fake.call_args.args[0]
    assert actual_cmd[:3] == ["gh", "pr", "merge"]
    assert "--squash" in actual_cmd
