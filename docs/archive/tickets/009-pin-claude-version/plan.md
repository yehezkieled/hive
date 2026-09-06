# Plan — Ticket 009: Pin & align the fleet's Claude Code version

Direct lane — a single PR. Resolve the harness `claude` from a config absolute
path (default `"claude"`), point it at the native self-updating install on the
host, and log the resolved version on every spawn.

## Files this Ticket creates / modifies

| Path | Op | Step |
|------|----|------|
| `src/hive/config.py` | modify | Add `CLAUDE_BINARY = os.path.expanduser(os.environ.get("HIVE_CLAUDE_BINARY", "claude"))` to the "Claude CLI defaults" block (after line 89). |
| `src/hive/runtime/pty_session.py` | modify | Import `CLAUDE_BINARY`; use it at `:71` (`args = [CLAUDE_BINARY, …]`); add `_resolve_claude_version()` helper; log resolved binary + version at the spawn site (~`:176`). |
| `tests/runtime/test_pty_session.py` | modify/add | Unit tests: `_resolve_claude_version` happy path (symlink basename), non-version path → `--version` fallback (mocked), failure → `"unknown"`; `_build_spawn_args` honors `HIVE_CLAUDE_BINARY`. No real `claude` spawned. |
| `docs/DEPLOYMENT.md` | modify | **(cross-cutting)** Add a "Claude Code version policy" note — the knob, track-latest default, how to freeze, version logged at spawn. |
| `.env` (host, not in git) | modify | Set `HIVE_CLAUDE_BINARY=/home/hezki/.local/bin/claude`. Applied on deploy; documented in `DEPLOYMENT.md`. |
| `docs/tickets/INDEX.md` | modify | Flip 009 → `in progress`, then `done` at close. |

## Verification

- `ruff check src/ tests/ && ruff format --check src/ tests/`
- `pytest -m "not integration"` green (incl. the new `_resolve_claude_version`
  + `CLAUDE_BINARY` tests).
- **On the deployed host after merge** (deploy from the **main** repo, not a
  worktree): set the `.env` line, `systemctl --user restart hive.service`, then
  `journalctl --user -u hive.service -n 30` shows the new
  `… on claude 2.1.162 (…/versions/2.1.162)` line — confirming the fleet now
  resolves the **native** install, not npm 2.1.140.
- Smoke: a maestro Turn completes end-to-end on the deployed code.

## Out of scope
- The Advisor's `claude -p` spawn (`advisor_server.py:139`) → Ticket
  [013](../013-retire-custom-advisor/) (retire custom advisor for CC native
  `/advisor`).
- Any systemd unit edit / PATH change — the config absolute path makes it
  unnecessary; dropped during design.
- Freezing to an exact version — policy is track-latest (see `design.md`).

## Cross-cutting impact
- `docs/DEPLOYMENT.md` — new version-policy note (declared here per the
  reference-doc rule). No `README` / `ARCHITECTURE` impact.
