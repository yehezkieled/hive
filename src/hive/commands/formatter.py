"""Formatter — the read-only command views (Ticket 045).

Holds the handlers that *render* state and never mutate it: status / org /
teams / quota / help / maestros / comms / health / cost / audit / tasks / files.

Constructed with a ``ProcessManager`` plus only the **read-only** stores it
needs (token / audit / task / attachment). It deliberately takes **no**
approval/mutation store (vault / mode_request / blueprint), so a read-only
surface (e.g. a web status endpoint) can build a Formatter without the whole
mutation + approval machinery. See ADR 0006 (composition) — this is the same
facade+collaborator split, with dependency injection because the collaborator
touches no facade-private state and must stand alone.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from hive.commands.result import CommandResult
from hive.models.maestro import Maestro
from hive.models.task import TaskStatus
from hive.telegram.help_text import format_all, format_one

if TYPE_CHECKING:
    from hive.bus.attachment_store import AttachmentStore
    from hive.bus.audit_log import AuditLog
    from hive.bus.task_store import TaskStore
    from hive.bus.token_store import TokenStore
    from hive.models.task import Task
    from hive.process.manager import ProcessManager
    from hive.telegram.commands import Command


class Formatter:
    """Read-only command views. Needs a ProcessManager + read-only stores only."""

    def __init__(
        self,
        process_manager: ProcessManager,
        token_store: TokenStore | None = None,
        audit_log: AuditLog | None = None,
        task_store: TaskStore | None = None,
        attachment_store: AttachmentStore | None = None,
    ) -> None:
        self.process_manager = process_manager
        self.token_store = token_store
        self.audit_log = audit_log
        self.task_store = task_store
        self.attachment_store = attachment_store

    # ------------------------------------------------------------------
    # Registry handlers — uniform ``async (cmd, actor) -> CommandResult``.
    # ------------------------------------------------------------------

    async def status(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=self._format_status())

    async def org(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=self._format_org())

    async def teams(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=self._format_teams())

    async def quota(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=self._format_quota())

    async def help(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=self._execute_help(cmd.target))

    async def maestros(self, cmd: Command, actor: str) -> CommandResult:
        entities = self.process_manager.entities
        maestros = [e for e in entities.values() if e.role == "maestro"]
        if not maestros:
            return CommandResult(text="No maestros running.")
        lines = [f"- {m.name} ({m.state.value}, model={m.model})" for m in maestros]
        return CommandResult(text="Maestros:\n" + "\n".join(lines))

    async def comms(self, cmd: Command, actor: str) -> CommandResult:
        recent = await self.process_manager.router.store.get_recent(limit=10)
        if not recent:
            return CommandResult(text="No messages yet.")
        lines = []
        for msg in reversed(recent):
            lines.append(f"[{msg['sender']} -> {msg['recipient']}] {msg['content'][:80]}")
        return CommandResult(text="Recent comms:\n" + "\n".join(lines))

    async def health(self, cmd: Command, actor: str) -> CommandResult:
        unhealthy = await self.process_manager.health_check()
        if unhealthy:
            return CommandResult(text=f"Unhealthy entities: {', '.join(unhealthy)}")
        return CommandResult(text="All entities healthy.")

    async def cost(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=await self._format_cost(cmd.args))

    async def audit(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=await self._format_audit(cmd.args))

    async def tasks(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=await self._format_tasks_list())

    async def files(self, cmd: Command, actor: str) -> CommandResult:
        return CommandResult(text=await self._execute_files(cmd.args))

    # ------------------------------------------------------------------
    # View builders (moved verbatim from CommandDispatcher; Ticket 045)
    # ------------------------------------------------------------------

    def _execute_help(self, name: str | None) -> str:
        """Format a /help response — grouped listing or per-command detail."""
        if name:
            return format_one(name)
        return format_all()

    def _format_quota(self) -> str:
        """Render the on-demand /quota response using the manager's monitor."""
        from hive.config import HIVE_QUOTA_POLL_SECONDS
        from hive.runtime.quota_monitor import format_quota_text

        monitor = self.process_manager.quota_monitor
        reading = monitor.get_quota() if monitor is not None else None
        return format_quota_text(
            reading,
            now=datetime.now(UTC),
            stale_after_seconds=HIVE_QUOTA_POLL_SECONDS * 2,
        )

    async def _format_cost(self, args: str) -> str:
        """Format a /cost report over an optional time window (default 24h)."""
        if self.token_store is None:
            return "Token tracking not configured."

        window = _parse_window(args)
        since = datetime.now(UTC) - window.delta
        totals = await self.token_store.totals(since=since)

        calls = int(totals.get("call_count", 0))
        if calls == 0:
            return f"No token usage in the last {window.label}."

        in_tok = int(totals.get("input_tokens", 0))
        out_tok = int(totals.get("output_tokens", 0))
        cache_create = int(totals.get("cache_creation_input_tokens", 0))
        cache_read = int(totals.get("cache_read_input_tokens", 0))
        cost = float(totals.get("cost_usd", 0) or 0)

        return (
            f"Tokens (last {window.label}, {calls} call(s)):\n"
            f"  input:  {in_tok:,}\n"
            f"  output: {out_tok:,}\n"
            f"  cache create: {cache_create:,}\n"
            f"  cache read:   {cache_read:,}\n"
            f"  ${cost:.4f} equivalent API cost (covered by Max subscription)"
        )

    async def _format_audit(self, args: str) -> str:
        """Format a /audit report — the last N events, optionally prefix-filtered."""
        if self.audit_log is None:
            return "Audit log not configured."

        prefix, limit = _parse_audit_args(args)
        events = await self.audit_log.recent(limit=limit, action_prefix=prefix)
        if not events:
            scope = f"{prefix}*" if prefix else "all"
            return f"No audit events ({scope}, limit {limit})."

        lines = [_format_audit_row(event) for event in events]
        header = f"Audit (last {len(events)}):"
        return header + "\n" + "\n".join(lines)

    async def _execute_files(self, args: str) -> str:
        """Handle /files [N] — list the most recent uploads (default 20, max 100)."""
        if self.attachment_store is None:
            return "Attachments not configured."

        limit = 20
        raw = (args or "").strip()
        if raw:
            try:
                limit = int(raw.split()[0])
            except ValueError:
                return "Usage: /files [N]"
            if limit < 1:
                return "Usage: /files [N] — N must be >= 1."
            limit = min(limit, 100)

        rows = await self.attachment_store.list_recent(limit=limit)
        if not rows:
            return "No attachments yet."

        lines = [f"Recent attachments (last {len(rows)}):"]
        for r in rows:
            ts = r.created_at.strftime("%Y-%m-%d %H:%M")
            forwarded = r.forwarded_to or "—"
            mime = r.mime_type or "?"
            size = _format_bytes(r.size_bytes)
            name = r.original_name or Path(r.file_path).name
            lines.append(f"  #{r.id} {ts} {r.source} →{forwarded} {mime} {size} {name}")
        return "\n".join(lines)

    async def _format_tasks_list(self) -> str:
        """Format the open (pending + in-progress) tasks for /tasks."""
        if self.task_store is None:
            return "Task tracking not configured."

        pending = await self.task_store.list(status=TaskStatus.PENDING)
        in_progress = await self.task_store.list(status=TaskStatus.IN_PROGRESS)
        open_tasks: list[Task] = pending + in_progress
        if not open_tasks:
            return "No open tasks."

        lines = [_format_task_row(t) for t in open_tasks]
        return "Open tasks:\n" + "\n".join(lines)

    def _format_teams(self) -> str:
        """Format all teams across all maestros for /teams output."""
        entities = self.process_manager.entities
        maestros = [e for e in entities.values() if isinstance(e, Maestro)]
        if not maestros:
            return "No maestros registered."

        lines = []
        for m in maestros:
            if not m.teams:
                lines.append(f"{m.name}: no teams")
                continue
            for team_name, team in m.teams.items():
                worker_count = len(team.workers)
                lead_status = "active" if team.lead and team.lead in entities else "none"
                lines.append(f"{m.name}.{team_name}: lead={lead_status}, workers={worker_count}")
        return "Teams:\n" + "\n".join(lines) if lines else "No teams."

    def _format_org(self) -> str:
        """Format a tree view of the organization for /org."""
        entities = self.process_manager.entities
        maestros = [e for e in entities.values() if isinstance(e, Maestro)]
        if not maestros:
            return "No entities running."

        lines = []
        for m in sorted(maestros, key=lambda x: x.name):
            lines.append(f"{m.name} [maestro] {m.state.value}")
            for team_name, team in m.teams.items():
                lines.append(f"  {team_name} [team]")
                if team.lead and team.lead in entities:
                    lead = entities[team.lead]
                    lines.append(f"    {team.lead} [lead] {lead.state.value}")
        return "\n".join(lines) if lines else "No entities running."

    def _format_status(self) -> str:
        """Format entity status for display."""
        statuses = self.process_manager.get_status()
        if not statuses:
            return "No entities running."

        lines = []
        for s in statuses:
            uptime = f", uptime={int(s['uptime'])}s" if s["uptime"] else ""
            pid = f", pid={s['pid']}" if s["pid"] else ""
            lines.append(
                f"- {s['name']} [{s['role']}] {s['state']} (model={s['model']}{pid}{uptime})"
            )
        return "Entities:\n" + "\n".join(lines)


# ---------------------------------------------------------------------- #
# Module-level helpers (moved from dispatch.py — used only by the views)  #
# ---------------------------------------------------------------------- #


class _Window:
    """Time window for /cost queries — a delta + a human label."""

    __slots__ = ("delta", "label")

    def __init__(self, delta: timedelta, label: str) -> None:
        self.delta = delta
        self.label = label


def _parse_window(raw: str) -> _Window:
    """Parse /cost argument into a time window. Defaults to 24h on unrecognised input."""
    raw = (raw or "").strip().lower()
    windows = {
        "24h": _Window(timedelta(hours=24), "24h"),
        "7d": _Window(timedelta(days=7), "7d"),
        "30d": _Window(timedelta(days=30), "30d"),
    }
    return windows.get(raw, windows["24h"])


def _format_task_row(task: Task) -> str:
    """Render a single task as a one-liner for /tasks output."""
    return f"- #{task.id} [{task.status.value}] p{task.priority} {task.title}"


def _parse_audit_args(raw: str) -> tuple[str | None, int]:
    """Parse /audit args into (prefix, limit).

    Accepts a category prefix (``entity``, ``command``, ``task``) or
    nothing. Limit defaults to 20.
    """
    raw = (raw or "").strip().lower()
    if not raw:
        return None, 20
    known_prefixes = {"entity", "command", "task"}
    if raw in known_prefixes:
        return f"{raw}.", 20
    return None, 20


def _format_audit_row(event: dict) -> str:
    """Render one audit event as a one-liner for /audit output."""
    ts = event["timestamp"]
    actor = event["actor"]
    action = event["action"]
    target = event["target"] or "-"
    return f"{ts:%H:%M:%S} {actor} {action} {target}"


def _format_bytes(n: int | None) -> str:
    """Pretty-print a byte count (B/KB/MB/GB) for /files output."""
    if n is None:
        return "?"
    if n < 1024:
        return f"{n}B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f}KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f}MB"
    return f"{n / (1024 * 1024 * 1024):.1f}GB"
