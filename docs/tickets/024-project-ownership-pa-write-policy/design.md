# Design — Ticket 024: Project ownership & PA write-policy

Chosen approach, grounded in [`research.md`](research.md) and two live CC
probes. Decision recorded in
[ADR 0016](../../adr/0016-ownership-guard-pretooluse-hook.md).

## Decision in one paragraph

Add a **project registry** (1 project ↔ ≤1 maestro) and enforce write
boundaries with a **per-spawn `PreToolUse` ownership-guard hook** — not CC
`deny` rules and not a permission-mode change. **Bypass mode stays on for every
Entity** (the user's constraint). At each spawn, Hive writes a settings.json
containing a `hooks.PreToolUse` block (matched on `Write|Edit|MultiEdit|
NotebookEdit`) and injects it with `--settings <file>`; the hook runs a guard
that **blocks the edit when the target path falls outside the Entity's writable
set**. A maestro's project root is also its **cwd**. The guard fences the
file-edit tools only — it is a **guardrail against accidental cross-project
writes, not a `Bash`/subprocess-proof security wall**.

## The constraint chain (why the hook, not the obvious options)

```
keep bypass on (user)  ──▶  bypassPermissions skips deny + ask   (probed)
                       ──▶  bypass also escapes the cwd boundary (probed)
                       ──▶  ∴ no PERMISSION-based mechanism enforces anything
                       ──▶  PreToolUse HOOK fires under bypass    (probed)  ✅
```

Each rejected option and the constraint that kills it:

| Option | Killed by |
|--------|-----------|
| `permissions.deny` path rules | bypass skips `deny` (live probe) |
| Move maestros `yolo → acceptEdits` | user keeps bypass on; reverses 023 D6 |
| cwd / `--add-dir` scoping | bypass writes outside cwd (live probe) |
| OS isolation (per-project users/containers) | overkill for cooperative agents; fights Hive's one-user / per-user-CC-state / plan-billing model |
| **PreToolUse hook** | **nothing — fires under bypass, light, policy-in-code** ✅ |

## 1. Data model — the registry (Slice A)

New `projects` table + `Project` model + `ProjectStore`, mirroring the
`EntityStore` pattern (`bus/entity_store.py`).

```
Project
  name           str        PK            e.g. "acme"
  root_path      str        UNIQUE        abs path, e.g. /home/hezki/projects/acme
  owning_maestro str | None FK→entities.name (nullable = ownerless)
  created_at     datetime
  updated_at     datetime
```

- Migration `bus/migrations/029_projects.sql` (next free number — `028` is
  current top).
- **Invariant `1 project ↔ ≤1 maestro`:** enforced two ways — a partial UNIQUE
  index on `owning_maestro WHERE owning_maestro IS NOT NULL` (DB backstop) **and**
  an explicit check in the assign path that raises a typed error
  (`ProjectOwnershipError`) before the DB call, for a clean message. The
  reverse (a maestro owning ≤1 project) is the "Maestros never share a project"
  norm from `CONTEXT.md`; enforce maestro→project uniqueness the same way.
- `ProjectStore` methods: `upsert`, `load(name)`, `by_root_path(path)`,
  `owned_roots()` (all non-null `root_path`s — the PA's fence list),
  `for_maestro(name)`, `all()`, `delete(name)`. Wire into
  `bootstrap.py:build_process_manager` beside `EntityStore`.

## 2. The ownership guard hook (Slice B)

### What gets generated at spawn

For every Entity, `_adapter_config_from_entity` (`lifecycle_manager.py:96-120`)
gains a 4th output: a **settings-file payload** carrying the `PreToolUse` hook.
`ClaudeAdapterConfig` (`claude_adapter.py:54-67`) gains a
`settings_path: Path | None` field; `_build_pty_extra_args`
(`claude_adapter.py:113-138`) appends `--settings <path>` when set. The file is
written to a per-Entity temp location and **regenerated every spawn** (restart-
proof, same contract as the tool denylist per ADR 0010).

```jsonc
// generated per spawn, pointed at by --settings
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "Write|Edit|MultiEdit|NotebookEdit",
      "hooks": [{ "type": "command",
                  "command": "python3 -m hive.hooks.ownership_guard" }]
    }]
  }
}
```

The guard is a small module (`src/hive/hooks/ownership_guard.py`) shipped with
Hive. It reads the tool-call JSON on stdin (`tool_input.file_path`), compares
the absolute target path against the Entity's **writable policy**, and
`sys.exit(2)` with a message to block, else `exit(0)`. Proven to block under
`--dangerously-skip-permissions`.

### How the guard knows the policy

The guard needs (a) the Entity's identity/role and (b) the owned-roots set. Two
options — **decide in `outline.md`**, leaning toward the snapshot for liveness:

- **Bake-at-spawn:** the generated settings/env embeds the writable rule
  (e.g. `HIVE_WRITE_ALLOW=/root`, or `HIVE_WRITE_DENY=/a:/b`). Simple; goes
  stale if ownership changes mid-session (refreshes on next spawn).
- **Live snapshot (preferred):** Hive writes a tiny
  `~/.hive/ownership.json` snapshot whenever the registry changes; the guard
  reads it + an `HIVE_ENTITY` env var each call. Stays current mid-session, no
  per-call DB hit.

## 3. Policy resolution — one rule, two shapes

The writable policy is derived from the registry + the Entity:

```
project maestro (owns root R):      WRITE allowed only under R
PA maestro (otter, owns nothing):   WRITE denied under every owned root,
                                    allowed elsewhere (ownerless projects,
                                    Hive workspace)
```

- PA detection is `entity.name == config.DEFAULT_MAESTRO` (`config.py:98`),
  already available at the seam.
- **Reads are never guarded** (matcher excludes `Read`) → both the PA and
  project maestros can read any project. This is the "read any" half, for free.
- This is exactly the ticket's "PA reads any, edits only ownerless"
  acceptance, generalised so project maestros are symmetrically fenced.

## 4. Project home (cwd)

`lifecycle_manager.py:263-267` today sets cwd only for `TeamLead.worktree_path`.
Extend: a project maestro spawns with `cwd = its project root` (looked up via
`ProjectStore.for_maestro`). The PA keeps `cwd=None` (the Hive checkout) — it
owns no project and reads across all of them. cwd is the *home*; the hook is the
*fence* (cwd alone doesn't confine under bypass).

## 5. Enforcement points (Slice A validation)

- **Reject 2nd maestro:** in the assign path (new `/project assign` command and
  `register_maestro` when a project is named) — check `ProjectStore` before
  commit, raise `ProjectOwnershipError`. Today `register_maestro`
  (`lifecycle_manager.py:199-200`) only guards name collision.
- **Command surface:** `/project new <name> <path> [maestro]`,
  `/project assign <name> <maestro>`, `/project list` in
  `commands/dispatch.py` (mirror an existing `_execute_*` + dispatch-table
  entry).

## Scope / honest limits

- **Guardrail, not a wall.** `Bash` redirects (best-effort: the guard *may*
  also match `Bash` and flag obvious `>`/`tee`/`cp`/`sed -i`) and raw
  subprocesses (`python -c`) are **not** fully fenced. Accepted —
  capability-over-sandboxing, cooperative agents.
- **Leaf agents** (Workflow runs inside a Lead) are out of scope — the worktree
  floor (023) already scopes their writes. One-line `CONFIRM IN CC` on whether
  the parent hook propagates into a Workflow run; not relied upon.
- No multi-user ownership, project sharing, or per-project quotas (ticket
  non-goals).

## Reference-doc impact (cross-cutting)

- **ADR 0016** (new) — the ownership-guard-via-PreToolUse-hook decision, incl.
  the bypass/deny/cwd probe evidence and the guardrail-not-wall boundary.
- **`CONTEXT.md`** (glossary) — add **Project** (registry record), **Project
  ownership**, **Ownership guard** (the hook); cross-link from the existing
  **PA Maestro** relationship lines.
- No `README`/`DEPLOYMENT` change (no new service/port; the guard ships in
  `src/`).

## Acceptance → mechanism map

| Acceptance | Delivered by |
|------------|--------------|
| Registry exists; create/assign rejects 2nd maestro | Slice A (table + `ProjectStore` + `ProjectOwnershipError`) |
| PA in owned project: edits denied, reads work | Slice B (guard denies owned roots; `Read` unguarded) |
| PA in ownerless project: edits work | Slice B (writable elsewhere) |
| Project maestro spawns with project root as cwd | Slice B (cwd derivation) |
| Policy applied every spawn, restart included | per-spawn settings regeneration (ADR 0010 contract) |
| Hermetic tests at the policy seam | guard unit tests (stdin JSON → exit code) + `_adapter_config_from_entity` tests |
| `ruff` + `pytest -m "not integration"` green | both slices |

## Lane → FAN-OUT (2 slices, B depends on A)

- **Slice A — registry + ownership:** `models/project.py`,
  `bus/project_store.py`, `029_projects.sql`, `ProjectOwnershipError`,
  `/project` commands + assign validation. Self-contained postgres CRUD.
- **Slice B — ownership guard + project home:** `hooks/ownership_guard.py`,
  the `ClaudeAdapterConfig.settings_path` + `--settings` plumbing, the
  `_adapter_config_from_entity` hook-emit, ownership snapshot, project-root cwd.
  Reads A's registry; carries the deployed re-smoke.
