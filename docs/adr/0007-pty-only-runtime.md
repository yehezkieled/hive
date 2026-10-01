# PTY-only runtime — drop the headless `claude -p` fallback

## Context

[ADR 0001](0001-harness-agnostic-runtime.md) made Hive harness-agnostic and
shipped the persistent-PTY `claude-code` adapter (Phase 1, Tickets 001 + 003).
PTY is live and plan-billed in production (`HIVE_USE_PTY=true`). The original
headless `claude -p` turn path survived only as a dual-runtime fallback behind
the `HIVE_USE_PTY` flag.

That fallback is now dead weight. After Anthropic's 2026-06-15 cutoff the
headless path is API-billed — the expensive path Hive never wants to take — so
it is not a fallback worth keeping. Meanwhile the flag scatters `use_pty`
branches across `claude_adapter.py`, `lifecycle_manager.py`, and `manager.py`,
and forces every `send_to_entity` change to reason about two runtimes.

Removing the headless path exposed a second, older machine entangled with it:
`spawn_entity` → `self._sessions` → `active_count` → `_preempt_for_priority`.
This is the pre-adapter entity lifecycle. It is **dead in PTY production** — its
only writer (`spawn_entity`) has no production caller; production spawns register
an entity IDLE and drive turns through a lazily-built PTY `ClaudeAdapter` cached
in `self._adapters`. So `_sessions` is permanently empty, `active_count` is
always 0, and the capacity/status signals derived from them have been *lying*
since the Phase-1 migration (scheduler reports "all slots free"; the heartbeat
and dashboard report nothing alive). It is typed on `ClaudeSession`, so it
cannot survive the deletion.

## Decision

Commit to **PTY-only**.

- Delete `process/claude_session.py` (`ClaudeSession`), the `use_pty=False`
  branches in `runtime/claude_adapter.py`, the headless `--resume` /
  `initial_session_id` logic, and the `HIVE_USE_PTY` flag. The PTY path becomes
  unconditional.
- Delete the dead headless-era lifecycle: `spawn_entity`, `self._sessions`, and
  `_preempt_for_priority`. **Re-point** `active_count`, `get_status`, and
  `health_check` onto `self._adapters` so the capacity/status signals reflect
  the live PTY adapters instead of the empty `_sessions` dict.
- Keep `MAX_CONCURRENT_SESSIONS` / `max_sessions` as an informational planning
  input (the scheduler still surfaces `N/max free` to maestros). Drop
  `PRIORITY_PREEMPT_ENABLED` with preemption.
- Rebase the unit suite off the headless path onto a mocked `ClaudeAdapter`
  (`FakeAdapter`) injected at the `_get_or_create_adapter` seam; the conftest no
  longer pins `HIVE_USE_PTY=false`.

The advisor's one-shot `claude -p` (`mcp/advisor_server.py`) is a **separate raw
subprocess**, not `ClaudeSession`, and explicitly **stays**.

## Considered options

- **Keep the dual runtime behind the flag.** Rejected: it perpetuates exactly
  the conditionals this change exists to remove, and the headless fallback is
  never wanted post-cutoff.
- **Remove the capacity/status reporting along with `_sessions`** (delete
  `active_count`, the scheduler `free_slots` line, the heartbeat count). Rejected:
  a larger behaviour change that throws away signals the RAM-bound VPS wants;
  re-pointing onto `_adapters` preserves the feature and makes it correct.
- **Preserve behaviour byte-for-byte.** Impossible — `_sessions` is typed on the
  class being deleted, so its readers must move regardless.

## Consequences

- **One-way door.** Hive can no longer fall back to headless. Accepted: the
  fallback would be API-billed anyway, and Phase 4 (codex / opencode adapters)
  provides vendor independence by a better route.
- **Reporting behaviour delta (deliberate).** Re-pointing makes three
  currently-degenerate signals truthful: the scheduler's `free_slots`, the
  Telegram heartbeat's "N running", and the dashboard's `alive` field flip from
  constants (all-free / 0 / False) to live values. No Entity *execution* path
  changes. This is the one place the change is not "zero behaviour change"; it is
  a fix to a latent migration bug, not a feature change.
- **Hard capacity enforcement + preemption are dropped.** They were
  non-functional under PTY (the cap never fired because `active_count` was always
  0). A real adapter-based cap is future work, not part of this change.
- **PTY conversation continuity is unaffected** — it comes from
  `pty_session`'s `--continue`, independent of the deleted adapter `--resume`.
- Supersedes ADR 0001's "spawns headless `claude -p` per turn" status quo;
  cross-references ADR 0004 (interactive-gate hold-and-inject) and ADR 0006
  (god-object breakup, which relocated the lifecycle this change deletes).
