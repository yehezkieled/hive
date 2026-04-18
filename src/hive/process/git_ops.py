"""Git / gh CLI helpers for Telegram /commit, /pr, /merge commands.

Thin async wrappers around subprocess that return (ok, stdout, stderr).
Shared by the bridge handlers; extracted so the tests can substitute a
fake ``run`` without monkey-patching asyncio.
"""

from __future__ import annotations

import asyncio
import logging
import shlex
from pathlib import Path

logger = logging.getLogger(__name__)


async def run(cmd: list[str], cwd: Path) -> tuple[int, str, str]:
    """Run a command, returning (returncode, stdout, stderr) as decoded strings."""
    logger.info("git_ops: %s (cwd=%s)", shlex.join(cmd), cwd)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    return (
        proc.returncode or 0,
        stdout_bytes.decode(errors="replace"),
        stderr_bytes.decode(errors="replace"),
    )


async def commit(cwd: Path, message: str) -> tuple[bool, str]:
    """Stage all changes and commit with the given message.

    Returns (ok, summary_text). On success, summary_text is the SHA and
    shortstat. On failure, it's the captured stderr.
    """
    code, _, err = await run(["git", "add", "-A"], cwd=cwd)
    if code != 0:
        return False, f"git add failed: {err.strip()}"

    code, out, err = await run(["git", "commit", "-m", message], cwd=cwd)
    if code != 0:
        return False, f"git commit failed: {(err or out).strip()}"

    _, sha_line, _ = await run(["git", "log", "-1", "--pretty=format:%h %s"], cwd=cwd)
    _, stat, _ = await run(["git", "log", "-1", "--shortstat", "--pretty=format:"], cwd=cwd)
    summary = sha_line.strip()
    if stat.strip():
        summary += f"\n{stat.strip()}"
    return True, summary


async def current_branch(cwd: Path) -> str:
    """Return the current branch name, or empty string on detached HEAD."""
    code, out, _ = await run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
    if code != 0:
        return ""
    name = out.strip()
    return "" if name == "HEAD" else name


async def push(cwd: Path, branch: str) -> tuple[bool, str]:
    """Push the branch to origin with ``-u`` so future pushes are tracked."""
    code, out, err = await run(
        ["git", "push", "-u", "origin", branch],
        cwd=cwd,
    )
    if code != 0:
        return False, f"git push failed: {(err or out).strip()}"
    return True, (err or out).strip()


async def gh_pr_create(cwd: Path, title: str | None) -> tuple[bool, str]:
    """Create a PR via ``gh``. Returns (ok, url_or_error)."""
    cmd = ["gh", "pr", "create", "--fill"]
    if title:
        cmd = ["gh", "pr", "create", "--title", title, "--body", title]
    code, out, err = await run(cmd, cwd=cwd)
    if code != 0:
        return False, f"gh pr create failed: {(err or out).strip()}"
    return True, out.strip() or err.strip()


async def gh_pr_merge(cwd: Path) -> tuple[bool, str]:
    """Squash-merge the PR associated with the current branch."""
    code, out, err = await run(
        ["gh", "pr", "merge", "--squash", "--delete-branch"],
        cwd=cwd,
    )
    if code != 0:
        return False, f"gh pr merge failed: {(err or out).strip()}"
    return True, (out or err).strip()
