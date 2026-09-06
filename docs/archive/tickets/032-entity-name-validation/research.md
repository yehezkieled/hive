# Research — Ticket 032: Validate entity/team names

All findings carry file refs. Package root is `src/hive/`.

## §1 — Name entry points (creation paths)

A *new, user-supplied* name enters in exactly two methods, both in
`src/hive/process/lifecycle_manager.py`:

| Path | Entry → chokepoint | Names |
|------|--------------------|-------|
| `/new maestro` (human) | `dispatch.py:865,897` → `register_maestro` (`lifecycle_manager.py:243`) | maestro `name` |
| `/team create <name>` (human) | `dispatch.py:673` `_execute_team` → `manager.create_team` → `create_team` (`lifecycle_manager.py:370`) | `team_name` |
| `spawn_team` (maestro AI, hive_actions) | `message_dispatcher.py:599` → `manager.create_team` → `create_team` | `team_name` |

Both team paths converge on `create_team`; the maestro paths converge on
`register_maestro`. These two methods are the single chokepoints.

Every other `_entities[name] =` site **re-registers an already-existing
entity** — no new name, so no validation needed (non-goal: don't touch
existing names):
- `manager.py:773` `restore()` — persisted entity on startup
- `lifecycle_manager.py:294` `register_entity` — restore / tests
- `lifecycle_manager.py:569` `/compact` — re-register same entity after kill
- `dispatch.py:1088` `/reset` — same

## §2 — Where raw names become paths/refs (the danger)

Names flow **unsanitized** into:
- **Worktree dir:** `wt_path = self.worktree_dir / name` — `worktree.py:25`
  (also `:70`, `:120`).
- **Git branch:** `branch=f"hive/{name}"` / `f"hive/{lead_name}"` —
  `lifecycle_manager.py:341, 405, 684`.
- **Lead name / address:** `lead_name = f"{maestro_name}.{team_name}"` —
  `lifecycle_manager.py:397`.
- **CC transcript slug:** handled by Ticket 030 (one symptom, not the class).

## §3 — The `.` is the address separator (decides the allowlist)

`bus/permissions.py` parses the org hierarchy by **splitting the name on `.`**:
- `sender_name.split(".")[0]` = the maestro — `permissions.py:39, 75, 76, 103`.
- Comment `permissions.py:29`: "`dev.backend.w1` belongs to lead `dev.backend`,
  which belongs to maestro `dev`."

→ A `.` inside a *component* (e.g. team `back.end` → `otter.back.end`) corrupts
this parse. The dot must be the join separator only, never a component
character. So `.` is **out** of the per-component allowlist — which also
auto-blocks `.` and `..` (path traversal) for free.

## §4 — Error surfacing (decides the feedback scope)

- **Human paths already surface a clear error.** `_execute_team` catches
  `(KeyError, TypeError, ValueError)` and returns `str(e)` — `dispatch.py:675`.
  `/new maestro` catches `(ValueError, RuntimeError)` — `dispatch.py:869, 900`.
  A `ValueError` raised in the chokepoint reaches the user verbatim.
- **The maestro `spawn_team` path is log-only (the gap).**
  `message_dispatcher.py:620` catches a failed `create_team` and only
  `logger.warning(...)` — the maestro is never told, so it can't retry.
- **A feedback channel already exists.** `_handle_parse_errors(entity,
  parse_errors)` (`message_dispatcher.py:661`) routes action-level error
  feedback back to the sender with overflow escalation. D3's maestro feedback
  rides this existing mechanism rather than inventing a new one.

## §5 — Existing valid names stay valid

`otter` (`config.py:98` `DEFAULT_MAESTRO`), `dev`, `hive_dev`, and the joined
`<maestro>.<team>` all match `[A-Za-z0-9_-]` per component → unaffected. The PA
maestro `otter` is created through `register_maestro`, so it passes the new
check.

## §6 — No existing validation

Grep for `valid*name | sanitiz | allowlist | whitelist` across `src/` → **no
hits**. Names are entirely unchecked today.
