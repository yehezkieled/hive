# Plan — Ticket 030: CC projects-dir slug fix + fail-loud guard  (issue #168)

Direct lane — one module, one PR that closes #168. Root cause and approach in
[`research.md`](research.md) / [`design.md`](design.md).

One-line summary: Hive computes Claude Code's transcript-dir slug differently
than CC does (misses `_` and other punctuation), so for any cwd with an
underscore the reader polls a nonexistent dir and every turn false-times-out at
`prompt + 180s`. Fix the slug to match CC exactly, and add a guard that makes a
future drift loud instead of silent.

## Files this Ticket creates / modifies

| Path | Op | Step |
|------|----|------|
| `src/hive/runtime/pty_session.py` | modify | 1. Slug → `re.sub(r"[^a-zA-Z0-9]", "-", str(cwd))` at L73 + docstring (L66-72). 2. Add `_SLUG_GUARD_GRACE_S = 5.0` near the timing constants. 3. Add the fail-loud guard in `send()` inside `if self._session_path is None:`, after `resolve_session`, before the await-loop. |
| `tests/runtime/test_pty_session.py` | modify | Rename+augment `…_replaces_slashes_and_dots` → `…_replaces_all_nonalnum` (add underscore assertion, fix docstring); add `test_claude_projects_dir_replaces_underscore`; fix the `tmp_path` slug helpers (~L186, ~L217) to use `_claude_projects_dir(tmp_path)`; add `test_send_alarms_when_projects_dir_missing` and `test_send_no_alarm_on_lazy_creation`. |

No new files; `re`/`time`/`asyncio` already imported. `_claude_projects_dir` is
the sole slug computation — the one fix repairs resume detection, session
pinning, and Workflow-progress liveness together.

## Verification

- `ruff check src/ tests/ && ruff format --check src/ tests/`
- Full `pytest -m "not integration"` green — run with `PYTHONPATH=src` from the
  worktree (shared editable install pins to MAIN's `src/`).
- **Deployed re-smoke (required — closes the 0.85 residual in research §5):**
  maestro → lead → **multi-minute** Workflow turn on deployed code, run under
  **both**:
  - an **underscore-named** entity (e.g. a `hive_dev` lead) — proves the slug fix;
  - a **clean-named** entity (e.g. an `otter` lead) — proves no independent 017 gap.
  The turn must be **accepted on the turn-end sentinel**, not false-timed-out;
  the lead's report + `hive_actions` must reach the maestro.
- Confirm the new ERROR fires in `journalctl` if (and only if) the projects dir
  is genuinely absent — never on the healthy lazy-create path.

## Out of scope

- Auto-bouncing a jammed session — **Ticket 020 / #147**.
- Steering a running Workflow — S7.
- Any change to the 017 liveness path — it is sound (research §5).

## Cross-cutting impact

- **None to reference docs.** Bugfix; CC's slug rule is captured as a code
  comment (external-dependency assumption, per ADR 0010/0014). No `CONTEXT.md`
  term, no new ADR (design §"ADR? — No").
- **Follow-up to file (not in this PR):** entity/team **name validation** —
  names flow raw into git branches, worktree dirs, and addressing; restrict to
  `[A-Za-z0-9._-]` at spawn (research §6).

## Build

Single branch → one PR that **closes #168**. Build directly (you or a single
agent); land code + tests + the deployed re-smoke before merge.
