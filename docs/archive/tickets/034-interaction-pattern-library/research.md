# Research — Ticket 034: interaction-pattern delivery mechanism + `debate`

Code-grounded findings (6-agent sweep + direct verification of the two
load-bearing facts). Each claim carries a file ref. The closing **Implications**
section feeds `design.md`.

> Paths are repo-relative. Verified against `origin/main` as of branch
> `ticket-034-interaction-pattern-library`.

## §1 — How a Lead's prompt is assembled (the JD delivery path)

- Each Entity's system prompt is built at spawn by appending, in order: base
  `system_prompt` (from the personality file) → identity preamble (`"You are
  {name}. Your role is {role}."`) → loop prompt → **role JD** loaded from
  `personalities/role-{role}.md`.
  — `src/hive/models/entity.py:336-337`, `src/hive/runtime/claude_adapter.py:96-114`,
  `src/hive/process/loops.py:12-30`
- Only `maestro` and `lead` roles get a role JD appended (`vault` does not).
  — `src/hive/process/loops.py:8` (`_VALID_ROLES`), `entity.py:336`
- The role JD is loaded live at spawn via `load_role_jd()` and injected through
  `--append-system-prompt`. **It is in-repo, version-controlled, and always
  present regardless of the Lead's working directory.**
  — `src/hive/process/loops.py:12` (`_DEFAULT_BASE_DIR` → `personalities/`)
- `personalities/role-lead.md` (~158 lines) currently teaches **free-form**
  Workflow authoring — a "Author the Workflow" step plus "Authoring rules"
  (enumerate failure modes, bound fan-out, tag hygiene, pick worktree isolation
  by release granularity). **No `debate`/`blackboard`/`tournament` exist.**
  — `personalities/role-lead.md:26-74`
  → This confirms the ticket's premise ("authoring is free-form, no canonical
  template") and locates the injection point for a JD-delivered pattern: a new
  section between "Authoring rules" and "What you do NOT do".

## §2 — Skill curation is denylist-only and role-keyed (ADR 0008)

- `skill_denylist_for(role)`: every role denies `_ALL_ROLES_DENY`
  (`Skill(prototype)`); **every role *except* maestro** also denies
  `_THINKING_DENY` (the human-pausing skills). — `src/hive/process/skill_curation.py:52-63`
- It is **denylist-only**: a newly installed skill auto-allows until added to the
  list ("denylist rot"); Claude Code has no true allowlist under
  `--dangerously-skip-permissions`. — ADR 0008; `skill_curation.py:1-10`
- Tokens reach the spawned binary via the `--disallowedTools` CLI flag (not a
  settings file). Plugin skills need the namespaced form `Skill(plugin:name)`;
  the bare name is a silent no-op for plugin skills (verified on pinned 2.1.177).
  — `skill_curation.py:14-18`, `src/hive/runtime/claude_adapter.py:116-145`
- **Lead-only-via-skill requires inverting the policy**: to hide a skill from a
  maestro you add it to the maestro deny path — the opposite of the thinking-skill
  rule. But that inversion is only meaningful for *thinking* (human-pausing)
  skills; an autonomous skill has no deadlock reason to be denied anywhere.

## §3 — How a Lead runs leaf work today (ADR 0010 / Tickets 015–017)

- A Lead authors a Workflow script **inline** as a tool invocation inside one
  Turn, launches it, and blocks on `TaskOutput(block=true)` until completion,
  then synthesizes results in-context. — `personalities/role-lead.md:26-44`
- **Hive provides no Workflow scaffolding, template files, or reusable-script
  loader.** The script is LLM-authored each run; the Maestro passes only a prose
  contract (the text "Spawn Template", `role-maestro.md:62-74`). There is no
  existing hook for "load a saved pattern and run it."
- Workflow runs are observed **read-only** by a sweeper tailing on-disk run
  records; single-Turn, no mid-run steering (ADR 0014).
  — `src/hive/process/workflow_watcher.py:158-224`,
  `src/hive/runtime/workflow_progress.py:46-62`, ADR 0014

## §4 — Skills are inherited, not provisioned (the constraint that decides Q1)

- Entities run as native PTY sessions on the developer's machine with **no
  per-Entity config isolation** — they inherit the entire global
  `~/.claude/skills` library + plugins. — `skill_curation.py:2`,
  `src/hive/runtime/pty_session.py` (no `CLAUDE_CONFIG_DIR`/skill-path override)
- **Hive ships no custom skills.** Its only spawn-time skill mechanism is the
  denylist (it *removes* access; it never *adds* a skill). Ticket 014 only
  *trims* the global library. — `skill_curation.py:1-64`, Ticket 014
- A Lead's cwd is a **dedicated per-project worktree** (worktree floor, ADR
  0010), not the Hive repo root — so project-level `.claude/skills` /
  `.claude/workflows` discovery is unreliable across the projects a Lead works
  in. — `src/hive/process/lifecycle_manager.py:339-348`
  → **Net:** a skill- or saved-script-based pattern library would live *outside*
  the Hive git repo (global `~/.claude` or a plugin) and need a separate VPS
  install/transport step Hive does not have. JD content has none of these
  problems.

## §5 — The spawn path and where role scoping is enforced

- Unified spawn: `_get_or_create_adapter()` → `_adapter_config_from_entity()`
  merges three deny sources into one `--disallowedTools`: the entity's own tokens
  + `role_tool_denylist(role)` + `skill_denylist_for(role)`.
  — `src/hive/process/lifecycle_manager.py:101-125, 324-368`
- **Maestro has `Workflow` denied** (load-bearing for Q2):
  `_MAESTRO_DENY = [*_LEAD_DENY, "TaskOutput", "TaskStop", "Workflow"]`. The
  docstring: *"Maestros never drive a Workflow run themselves — that is the
  Lead's job — so the fan-out chain stays Maestro → Lead → Workflow."*
  — `src/hive/process/tool_policy.py:45-47`
  → A pattern that *is* a Workflow fan-out is **inherently Lead-only**; a maestro
  cannot exercise it. No new denylist work is needed to make `debate` Lead-only.
- Maestro-only fencing (ownership guard) rides a per-spawn `--settings` PreToolUse
  hook; leads need no fence because the worktree floor already scopes them.
  — `src/hive/process/lifecycle_manager.py:298-322`, `ownership_policy.py:30-69`

## §6 — Decision & glossary landscape

- **Next free ADR number is `0020`** (highest existing: `0019-maestro-phase-
  confirmation-gate.md`). ⚠ Parallel S7 worktrees (032/033) are in flight; re-check
  the next free number at ship time and bump if 032/033 land an ADR first
  (known ADR-number race). — `docs/adr/` listing
- The mechanism must stay consistent with: **ADR 0008** (denylist-only skill
  curation), **ADR 0010** (Workflow-exclusive fan-out; maestro Workflow denial),
  **ADR 0014** (read-only, single-Turn runs — patterns cannot steer mid-run).
- `CONTEXT.md` defines `Workflow run`, `Leaf agent`, `Thinking skill`,
  `Team Lead` — a new pattern entry must use these canonical terms (not
  "worker"/"subagent"). — `CONTEXT.md:104-160`

## Implications for the fork (feeds `design.md`)

1. **Source-of-truth location decides it.** JD content is in-repo,
   version-controlled, spawn-injected, cwd-independent, and unit-testable via the
   prompt-assembly path. Skills (B/C) and saved scripts (D) live outside the Hive
   repo, are unprovisioned, and are cwd-fragile — and a skill mechanism for
   provisioning is arguably the "engine work" the ticket lists as a non-goal.
2. **Lead-only is free.** Maestro cannot call `Workflow` (`_MAESTRO_DENY`), so a
   Workflow-shaped pattern is Lead-scoped by construction — no denylist inversion,
   no policy change.
3. **`debate` is autonomous** (orchestrates leaf agents, never pauses for a
   human) → it raises no thinking-skill deadlock concern wherever it runs.
4. The "template" value can be captured **inside** the JD as a Workflow-script
   skeleton (a code block the Lead adapts), blending options A and D without a
   separate, unprovisioned artifact.
5. **ADR scope** should likely be mechanism-only (ADR 0010 precedent); `debate`'s
   shape is a definition for `CONTEXT.md` + `role-lead.md`, not a hard-to-reverse
   decision.
