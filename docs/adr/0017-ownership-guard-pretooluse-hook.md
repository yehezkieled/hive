# ADR 0017 — Enforce project write-boundaries with a PreToolUse hook, under bypass

- **Status:** Accepted
- **Date:** 2026-06-14
- **Ticket:** [024](../tickets/024-project-ownership-pa-write-policy/)

## Context

Ticket 024 introduces project ownership (1 project ↔ ≤1 Maestro) and a write
boundary: a project Maestro may write only its own project, and the PA Maestro
(`otter`) may read any project but write only **ownerless** ones. The ticket
assumed enforcement via "per-path CC permission rules at every spawn" — deny
`Edit`/`Write` under owned roots, following the policy-in-code pattern of
[ADR 0010](0010-leads-orchestrate-via-workflow.md).

Two facts, both verified by live probe on `claude 2.1.177`, broke that
assumption:

1. **Every Entity runs in bypass mode.** Maestros are registered with
   `permission_mode = "yolo"` → `--dangerously-skip-permissions`
   (`models/entity.py:193`, `process/lifecycle_manager.py:214`), an intentional
   023 D6 shadow, and the user's standing decision is to **keep bypass on for
   all Entities**.
2. **Bypass defeats every permission-based fence.** Under
   `--permission-mode bypassPermissions` a session **wrote to a `deny`-listed
   path with no error**; under `--dangerously-skip-permissions` a session with
   `cwd=projA` **wrote a file into `projB`** (outside cwd). So neither
   `permissions.deny` rules nor the cwd/`--add-dir` boundary enforce anything
   while bypass is on.

The naïve fix — move Maestros off bypass to `acceptEdits` (which *does* honor
`deny`, also probed) — is ruled out by constraint (1).

A third probe found the escape hatch: a **`PreToolUse` hook fires even under
`--dangerously-skip-permissions`**. A settings.json `hooks.PreToolUse` block
whose guard script exits non-zero **hard-blocked** an out-of-root write under
full bypass ("that boundary would have to be changed by whoever set up the
guard — I can't override it") while allowing in-root writes. Hooks live outside
the permission system, so bypass does not skip them.

## Decision

Enforce the 024 write boundary with a **per-spawn `PreToolUse` ownership-guard
hook**, not permission rules and not a permission-mode change. **Bypass mode
stays on for every Entity.**

- At each spawn, `_adapter_config_from_entity`
  (`process/lifecycle_manager.py:96-120`) emits a settings.json carrying a
  `hooks.PreToolUse` block matched on `Write|Edit|MultiEdit|NotebookEdit`,
  injected via `--settings <file>` (path-scoped rules cannot ride
  `--disallowedTools`, which takes bare tool names only). The file is
  regenerated every spawn — restart-proof, same contract as the role tool
  denylist.
- The guard (`src/hive/hooks/ownership_guard.py`) reads the tool call on stdin,
  resolves the target `file_path` against the Entity's writable policy (project
  Maestro → only its own root; PA → every owned root denied, elsewhere
  allowed), and exits non-zero to block. `Read` is unguarded → "read any
  project" is free.

## Consequences

- **Guardrail, not a security wall.** The hook fences Claude's file-edit tools.
  Raw `Bash`/subprocess writes (`python -c "open(...,'w')"`) still escape;
  closing that needs OS-level isolation. This is an accepted boundary,
  consistent with Hive's capability-over-sandboxing stance — the threat model
  is *accidental* cross-project writes by cooperative Entities, not a hostile
  breakout.
- **Does not reverse 023 D6.** Bypass stays on; this ADR adds an orthogonal
  enforcement layer rather than changing permission mode. ADR 0010's
  policy-in-code-at-every-spawn contract is extended (a 4th source), not broken.
- **New spawn plumbing.** Hive now writes a per-spawn settings file and passes
  `--settings`; `ClaudeAdapterConfig` carries a `settings_path`. Previously Hive
  passed only `--disallowedTools`.
- **Per-edit latency.** The guard runs as a subprocess on each Edit/Write
  (~tens of ms). Acceptable.
- **Liveness vs. staleness.** The owned-roots the guard checks come from the
  registry. A live snapshot file (rewritten on registry change) keeps the fence
  current mid-session; baking the rule into spawn config is simpler but
  refreshes only on respawn. (Implementation choice — see the Ticket's
  `outline.md`.)

## Alternatives considered

- **`permissions.deny` path rules** — rejected: bypass skips `deny` (probed).
- **Maestros `yolo → acceptEdits`** — rejected: user keeps bypass on; would
  reverse 023 D6.
- **cwd / `--add-dir` scoping** — rejected: bypass writes outside cwd (probed).
- **OS-level isolation (per-project Unix users / containers)** — rejected as
  overkill for cooperative agents and incompatible with Hive's single-user,
  per-user-CC-state, plan-billed model. Revisit only if the threat model
  changes to untrusted Entities.
