# Research — Ticket 024: Project ownership & PA write-policy

Grounded in a parallel code-mapping sweep (5 areas + critic) and two
live Claude Code probes (`claude 2.1.177`). File refs are `src/`-relative
unless absolute. Items the code/probe could not settle are flagged
**CONFIRM IN CC**.

## Headline

The ticket's stated mechanism — "per-path CC permission rules at every
spawn (deny `Edit`/`Write` under owned roots)" — **works, but only if the
target Entity is NOT in bypass mode.** Today every maestro (including the
PA, `otter`, which is 024's primary target) is registered with
`permission_mode = "yolo"` → `--dangerously-skip-permissions`, which
**skips explicit `deny` rules entirely** (probed). So 024's enforcement
is defeated by Ticket 023's D6 decision unless 024 also moves maestros
off bypass mode. This is the central design problem; everything else is
mechanical.

## Decision update (post-grill, 2026-06-14)

The grill resolved the central problem the **opposite** way from §4's first
recommendation: **bypass stays on for all entities** (user's call), so neither
`deny` rules nor `acceptEdits` are viable. Two further live probes settled the
mechanism:

- **cwd does NOT confine under bypass.** With `cwd=projA` and
  `--dangerously-skip-permissions`, CC wrote a file into `projB` (outside cwd)
  with no error. So "spawn in the project dir" is *not* a fence under bypass.
- **A `PreToolUse` hook DOES fire under bypass.** A settings.json `hooks`
  block matching `Write|Edit|MultiEdit|NotebookEdit`, whose guard script exits
  non-zero for paths outside the owned root, **hard-blocked** a write into
  `projB` under `--dangerously-skip-permissions` ("that boundary would have to
  be changed by whoever set up the guard — I can't override it"), and allowed
  the write inside the owned root.

**Chosen mechanism:** a per-spawn `PreToolUse` **ownership guard hook** (not
deny rules, not a mode change), injected via `--settings <file>`. It fences the
file-edit tools only — a guardrail against accidental cross-project writes, not
a `Bash`/subprocess-proof wall (matches the project's capability-over-
sandboxing stance). PA policy confirmed as the original ticket: **read any,
write only ownerless projects.** Full design in [`design.md`](design.md);
decision recorded in [ADR 0016](../../adr/0016-ownership-guard-pretooluse-hook.md).

## 1. Current state — no project concept exists

- **No registry, no `Project` model, no `projects` table.** Migrations
  top out at `028_drop_advisor_calls.sql`; next free number is
  `029_*.sql` (verified — it's a *proposed* file, not existing).
- **Persistence is PostgreSQL/asyncpg, exclusively.** Structured state
  rides stores wired in `bootstrap.py:34-79` (`EntityStore`,
  `VaultStore`, `TaskStore`, …). A registry should be a new
  `ProjectStore` following the `EntityStore` upsert/load/all/delete
  pattern (`bus/entity_store.py`). No JSON/sqlite/yaml runtime stores.
- **Entities carry no `cwd`/`root_path`.** `entities` table
  (`bus/migrations/002_entities.sql`) has no such column; `Entity`
  dataclass (`models/entity.py:196-219`) has none. Only
  `TeamLead.worktree_path` (`models/team_lead.py:29`) exists, and it is
  **not round-tripped** by `EntityStore` — it's re-provisioned lazily at
  spawn (`lifecycle_manager.py:253-261`). 024 may follow that
  "derive-at-spawn" pattern rather than persisting a maestro cwd.
- **Maestros are configured** by personality markdown
  (`personalities/*.md` → `parse_personality`, `models/entity.py:70-124`)
  + the `entities` row. Created via
  `LifecycleManager.register_maestro` (`lifecycle_manager.py:188-228`)
  and the `/new maestro` command flow (`commands/dispatch.py:789-847`).

## 2. The policy seam (verified, restart-proof)

- `_adapter_config_from_entity()` (`lifecycle_manager.py:96-120`) merges
  three deny sources at **every** spawn — `entity.disallowed_tools` +
  `role_tool_denylist(role)` (`tool_policy.py`) +
  `skill_denylist_for(role)` (`skill_curation.py`) — and runs on restart
  too (entities restored from postgres, `__main__.py:278-283`). This is
  the policy-in-code contract from **ADR 0010 / ADR 0008**.
- These become **`--disallowedTools` CLI flags**
  (`claude_adapter.py:113-138`, line 118-119) — passed through
  unvalidated. **But `--disallowedTools` accepts BARE tool names only**
  (probe Q2); every existing token in the repo is bare
  (`tool_policy.py`, `models/vault.py:19`).
- **→ Path-scoped rules cannot ride `--disallowedTools`.** They must go
  in a settings.json `permissions.deny` array, injected via
  **`--settings <file>`** at spawn. This is a *new* spawn mechanism for
  Hive (today it writes no per-spawn settings file).

## 3. Claude Code permission semantics (probed on 2.1.177)

| Q | Finding | Confidence |
|---|---------|------------|
| Path-deny syntax | `Write(//abs/path/**)` — `//` = absolute, `**` = recursive. Must deny `Edit`, `Write`, `MultiEdit`, `NotebookEdit` **and** `Bash(...)` for shell writes. | HIGH (docs) |
| Injection | path rules **only** via `permissions.deny` in a settings.json; point at it with `--settings <file>`. `--disallowedTools` = bare names only. | HIGH (docs) |
| **Bypass skips deny** | `--permission-mode bypassPermissions` **wrote to a denied path with no error** — deny + ask both skipped. `yolo` → `--dangerously-skip-permissions` is at least as permissive. | **VERY HIGH (live probe)** |
| **`acceptEdits` honors deny** | Probe A/B/C: `acceptEdits` created files where allowed, **blocked** the denied path ("denied by the current permission settings"). Deny binds; edits auto-accept elsewhere. | **HIGH (live probe)** |
| **cwd under bypass** | `cwd=projA` + bypass **still wrote into `projB`** — cwd is not a fence under bypass. | **HIGH (live probe)** |
| **PreToolUse hook under bypass** | hook matching the edit tools **hard-blocked** an out-of-root write under `--dangerously-skip-permissions`; allowed in-root writes. **This is the chosen mechanism.** | **HIGH (live probe)** |
| Subprocess escape | the hook (like deny) covers CC's file tools + *recognized* Bash writes; an arbitrary interpreter (`python -c "open(...,'w')"`) bypasses it. OS sandbox needed for a hard boundary. | HIGH (docs) |
| Sub-agent / Workflow inheritance | subagents inherit parent perms + extra restrictions; Workflow leaf agents run `acceptEdits` + inherit the allowlist — **deny propagation to Workflow agents unverified**. | MED — **CONFIRM IN CC** |

## 4. The yolo blocker (central finding, verified in code)

- `register_maestro` sets `maestro.permission_mode = "yolo"`
  (`lifecycle_manager.py:207-214`) — an **intentional** Ticket-023 D6
  shadow: "first-spawn safety… first turn must not stall on permission
  prompts before any gate bridge exists." It is **persisted**, not
  first-turn-only ("Existing maestros restored from postgres keep their
  persisted mode").
- `yolo` ∈ `DANGEROUS_MODES` (`models/entity.py:193`) → emits
  `--dangerously-skip-permissions` (`entity.py:185-188, 283-286`).
- **Therefore the PA maestro skips all deny rules.** For 024 to enforce
  anything, maestros must run in a deny-honoring mode. `acceptEdits` is
  the natural choice — it honors deny (probed) and still auto-accepts
  edits, so it largely preserves D6's no-stall benefit. The first-turn
  rationale is *also* weakening independently: Ticket **029** (gate
  bridge, in progress, #144) is exactly "a maestro gate reaches the
  user," which is the bridge D6 said didn't yet exist.
- **Read-vs-edit asymmetry resolves cleanly:** deny only
  `Write`/`Edit`/`MultiEdit`/`NotebookEdit`/`Bash`-writes, **never
  `Read`** → the PA reads owned projects fine, writes are blocked. No
  `--add-dir` gymnastics needed (CC reads absolute paths broadly).

## 5. Reusable seams (the concrete hooks)

- **NEW `models/project.py`** — `Project(name, root_path,
  owning_maestro: str | None, created_at, updated_at)`; mirror the
  `Entity` dataclass.
- **NEW `bus/project_store.py`** — asyncpg store on the `EntityStore`
  pattern; wire into `bootstrap.py:34-79` beside `EntityStore`.
- **NEW `bus/migrations/029_projects.sql`** — `name` PK, `root_path`
  UNIQUE, `owning_maestro` nullable FK → `entities.name`, timestamps.
- **NEW `process/path_policy.py`** (parallel to `tool_policy.py`, not an
  extension of it) — `path_deny_rules(entity, projects) -> list[str]`
  emitting `Write(//root/**)` etc. for roots the entity may not edit.
- **`lifecycle_manager.py:96-120`** (`_adapter_config_from_entity`) — add
  a 4th source: the path-deny rules. Because they can't ride
  `--disallowedTools`, also extend `ClaudeAdapterConfig`
  (`claude_adapter.py:54-67`) with a `path_deny_rules` field that
  `_build_pty_extra_args` (`:113-138`) writes to a temp settings.json +
  `--settings`.
- **`lifecycle_manager.py:263-267`** — cwd derivation; extend so a
  project maestro gets its project root (PA stays `cwd=None` /
  checkout — see open Q).
- **`lifecycle_manager.py:188-228`** (`register_maestro`) — add the
  "1 project ↔ ≤1 Maestro" rejection (today only a name-collision
  `ValueError` at :199-200). Change `permission_mode` default off `yolo`.
- **`config.py:98`** — `DEFAULT_MAESTRO` ("otter"); already imported into
  the process layer, so `entity.name == DEFAULT_MAESTRO` is the
  PA-vs-project branch, available at the seam.
- **`commands/dispatch.py`** — `/project new|assign|list` command surface
  + ownership capture in `_finalize_new_maestro` (~:832). (Exact
  add-a-command seam not fully traced — see open Qs.)

## 6. Open questions → design grill

- **G1 (decision). Resolve the yolo conflict.** Move maestro default
  `yolo → acceptEdits` (deny binds, no edit-stall), riding 029's bridge
  for any residual prompts? This changes live maestro behaviour and
  touches 023 D6 → **ADR-worthy**.
- **G2 (framing). Guardrail vs. boundary.** Subprocess writes escape the
  deny. Is 024 a *cooperative guardrail* (matches the project's
  capability-over-sandboxing stance) rather than a hard security wall?
  This sets the bar for "done."
- **G3. PA cwd.** The PA owns no project — keep `cwd=None` (checkout) or
  give it a neutral home? Read-access spans all projects but cwd is one
  dir.
- **G4. Registry bootstrapping.** How does a `root_path` first enter the
  registry — `/project new <name> <path> [maestro]`? Auto-discovered? The
  ticket assumes records "exist" but not how the first one is created.
- **CONFIRM IN CC:** does a path-`deny` on a lead/maestro propagate to
  its Workflow **leaf agents**? Likely out of scope (ticket non-goal: the
  floor already scopes leaf writes), but worth a one-line live check.

## 7. Lane signal → FAN-OUT (2 slices)

- **Slice A — registry + ownership** (`models/project.py`,
  `project_store.py`, `029_projects.sql`, register/assign "≤1 Maestro"
  validation, `/project` commands). Self-contained postgres CRUD;
  delivers acceptance #1–2. Low risk.
- **Slice B — spawn-time enforcement** (`path_policy.py`, the
  `_adapter_config_from_entity` 4th source + `--settings` plumbing,
  maestro `yolo→acceptEdits`, project-root cwd, read-works). Depends on A
  (reads the registry); carries the behaviour change + ADR + a deployed
  re-smoke. Delivers acceptance #3–5.
- If G1 lands on "don't use permission rules at all," B reshapes — but
  plan for two PRs.
